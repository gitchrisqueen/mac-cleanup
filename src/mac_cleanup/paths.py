"""Path resolution. The forest and the guard share this contract by construction.

Two rules, both learned the hard way during planning:

1. ``$HOME`` is spoofable, so home comes from the password database, not the environment.
   ``os.path.expanduser`` is banned repo-wide by ruff for this reason; this module holds
   the single exemption.

2. Resolve the **parent** only and keep the final component lexical. ``realpath`` on a full
   path silently dereferences a symlinked final component, so a machine where something
   replaced ``~/Library/Caches`` with a link to ``/`` would hand back ``/`` -- which then
   passes a deny check written against the original string. Resolving the parent keeps the
   final component's symlink-ness a fact we decide about rather than one already lost.
"""

from __future__ import annotations

import os
import pwd

from mac_cleanup.errors import Refusal


def home() -> str:
    """The invoking user's home directory, from the password database."""
    return pwd.getpwuid(os.getuid()).pw_dir


def expand(path: str) -> str:
    """Expand a leading ``~`` using :func:`home`. No ``$VAR``, no globs."""
    if path == "~":
        return home()
    if path.startswith("~/"):
        return os.path.join(home(), path[2:])
    return path


def resolve_parent(path: str) -> str:
    """Return ``realpath(dirname) / basename``, with the final component unresolved.

    Raises:
        Refusal: ``E_MALFORMED`` for an empty path, a NUL byte, or no basename;
            ``E_RELATIVE`` if not absolute after expansion.
    """
    if not path or "\0" in path:
        raise Refusal("E_MALFORMED", path)
    p = expand(path)
    if not os.path.isabs(p):
        raise Refusal("E_RELATIVE", path)
    p = p.rstrip(os.sep) or os.sep
    parent, base = os.path.split(p)
    if base in ("", ".", ".."):
        raise Refusal("E_MALFORMED", path, "no basename")
    return os.path.join(os.path.realpath(parent), base)


def state_dir() -> str:
    """The tool's own state directory.

    ``XDG_STATE_HOME`` is honoured but validated by the caller: it is user-controlled, and
    pointing it inside a target the tool deletes would let the tool destroy its own state.
    """
    xdg = os.environ.get("XDG_STATE_HOME")
    base = xdg if xdg and os.path.isabs(xdg) else os.path.join(home(), ".local", "state")
    return os.path.join(base, "mac-cleanup")
