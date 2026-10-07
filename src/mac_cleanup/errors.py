"""Error types and the OSError classification table.

Every rejection raises. Nothing here returns a boolean a caller can ignore the way
``find ... 2>/dev/null`` ignores a failed unlink -- that is the defect this project
exists to remove.
"""

from __future__ import annotations

import errno
from dataclasses import dataclass

# Error classes, in the vocabulary the run report uses. `readonly` is kept distinct from
# `permission` because the two are genuinely different and were conflated during planning:
# deleting a mounted simulator runtime volume fails EPERM on the root-owned parent, not
# EROFS on the read-only filesystem, and a report that says "read-only" for a permissions
# problem sends the user to the wrong fix.
PERMISSION = "permission"
BUSY = "busy"
RACED = "raced"
READONLY = "readonly"
NOTFOUND = "notfound"
OTHER = "other"

_CLASS_BY_ERRNO = {
    errno.EPERM: PERMISSION,
    errno.EACCES: PERMISSION,
    errno.EBUSY: BUSY,
    errno.ENOTEMPTY: BUSY,
    errno.EROFS: READONLY,
    errno.ENOENT: RACED,
}


def classify_oserror(exc: OSError) -> str:
    """Map an OSError to a stable class name used in reports and tests."""
    return _CLASS_BY_ERRNO.get(exc.errno or 0, OTHER)


class Refusal(Exception):
    """A guard or validation rejection. ``code`` is stable and asserted by tests."""

    __slots__ = ("code", "detail", "path")

    def __init__(self, code: str, path: object = "", detail: str = "") -> None:
        self.code = code
        self.path = str(path)
        self.detail = detail
        msg = f"{code}: {self.path}" if self.path else code
        super().__init__(f"{msg} ({detail})" if detail else msg)


@dataclass(frozen=True)
class ScanError:
    """A directory or file that could not be read during a scan. Never discarded."""

    path: str
    errno_: int
    strerror: str
    cls: str


@dataclass(frozen=True)
class DeleteError:
    """A path that could not be removed. ``cls`` drives the rendered wording."""

    path: str
    errno_: int
    strerror: str
    cls: str
