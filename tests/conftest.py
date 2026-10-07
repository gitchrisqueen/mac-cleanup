"""Shared fixtures, and the seatbelt that stops the suite deleting anything real.

The guarantee: **no test removes anything outside pytest's own temp sandbox.** Within the
sandbox removals are allowed, because pytest itself needs them (the ``tmp_path`` fixture
maintains a ``pytest-current`` symlink) and because fixtures legitimately build and tear
down trees there. Tests marked ``destructive`` are held to the tighter rule that they may
only remove things inside *their own* ``tmp_path``.

Resolving fd-relative calls
---------------------------
The deleter issues ``unlinkat``-style calls -- ``os.unlink(name, dir_fd=fd)`` -- where
``name`` is a bare basename. Resolving that against the process CWD would be wrong, so the
seatbelt recovers the directory's real path from the descriptor with ``fcntl(F_GETPATH)`` on
Darwin and ``/proc/self/fd`` elsewhere. A guard that could not see through a descriptor
would either block the deleter outright or wave it through blind; neither is acceptable for
the one module allowed to remove things.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_F_GETPATH = 50  # Darwin; see fcntl(2)
_MAXPATH = 1024

_REMOVERS = (
    (os, "remove"),
    (os, "unlink"),
    (os, "rmdir"),
    (os, "removedirs"),
    (shutil, "rmtree"),
)


def _path_of_fd(fd: int) -> str | None:
    try:
        if sys.platform == "darwin":
            # fcntl() takes an immutable buffer and hands the filled one back.
            out = fcntl.fcntl(fd, _F_GETPATH, b"\0" * _MAXPATH)
            return out.split(b"\0", 1)[0].decode()
        return os.readlink(f"/proc/self/fd/{fd}")
    except (OSError, ValueError):
        return None


def _target_path(path: object, dir_fd: int | None) -> str | None:
    """The absolute path a removal call will actually act on, or None if unknowable."""
    raw = os.fsdecode(path) if isinstance(path, (str, bytes, os.PathLike)) else str(path)
    if dir_fd is None:
        return raw if os.path.isabs(raw) else os.path.abspath(raw)
    base = _path_of_fd(dir_fd)
    return None if base is None else os.path.join(base, raw)


def _inside(resolved: str, root: Path) -> bool:
    try:
        p = Path(resolved).resolve()
    except OSError:
        return False
    return p == root or root in p.parents


@pytest.fixture(autouse=True)
def _seatbelt(request, monkeypatch, tmp_path_factory):
    """Fail any removal that escapes the allowed root, naming the offending call."""
    sandbox = Path(str(tmp_path_factory.getbasetemp())).resolve().parent

    if request.node.get_closest_marker("destructive"):
        allowed = Path(str(request.getfixturevalue("tmp_path"))).resolve()
        reason = "escaped tmp_path"
    else:
        allowed = sandbox
        reason = "called in a test without @pytest.mark.destructive"

    def guard(original, label):
        def wrapper(path, *args, dir_fd=None, **kwargs):
            resolved = _target_path(path, dir_fd)
            if resolved is None:
                raise AssertionError(f"{label}: could not resolve dir_fd={dir_fd!r}")
            if not _inside(resolved, allowed):
                raise AssertionError(f"{label} {reason}: {resolved}")
            if dir_fd is None:
                return original(path, *args, **kwargs)
            return original(path, *args, dir_fd=dir_fd, **kwargs)

        return wrapper

    for module, name in _REMOVERS:
        label = f"{module.__name__}.{name}"
        monkeypatch.setattr(module, name, guard(getattr(module, name), label))

    def forbid_system(*args, **kwargs):
        raise AssertionError(f"os.system is never permitted in tests; args={args!r}")

    monkeypatch.setattr(os, "system", forbid_system)
