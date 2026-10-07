"""Shared fixtures, and the seatbelt that stops the suite deleting anything real.

The seatbelt's guarantee: **no test removes anything outside pytest's own temp sandbox.**
Within the sandbox, removals are allowed, because pytest itself needs them (the `tmp_path`
fixture maintains a `pytest-current` symlink) and because that is where fixtures legitimately
build and tear down trees. Tests marked ``destructive`` are held to the tighter rule that
they may only remove things inside *their own* ``tmp_path``.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_REMOVERS = (
    (os, "remove"),
    (os, "unlink"),
    (os, "rmdir"),
    (os, "removedirs"),
    (shutil, "rmtree"),
)


def _inside(path: object, root: Path) -> bool:
    try:
        resolved = Path(str(path)).resolve()
    except OSError:
        return False
    return resolved == root or root in resolved.parents


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
        def wrapper(path, *args, **kwargs):
            if not _inside(path, allowed):
                raise AssertionError(f"{label} {reason}: {path!r}")
            return original(path, *args, **kwargs)

        return wrapper

    for module, name in _REMOVERS:
        label = f"{module.__name__}.{name}"
        monkeypatch.setattr(module, name, guard(getattr(module, name), label))

    def forbid_system(*args, **kwargs):
        raise AssertionError(f"os.system is never permitted in tests; args={args!r}")

    monkeypatch.setattr(os, "system", forbid_system)
