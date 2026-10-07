"""The stat seam.

Every module that measures takes an :class:`FsStat`. The in-memory fake in the test suite
implements it, which is why the accounting tests run in microseconds and are free of real
I/O timing -- and therefore free of flakes.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class FsStat(Protocol):
    """The minimum filesystem surface the scanner needs."""

    def scandir(self, path: str) -> list[DirEntryLike]: ...
    def lstat(self, path: str) -> os.stat_result: ...
    def lexists(self, path: str) -> bool: ...


@runtime_checkable
class DirEntryLike(Protocol):
    """A directory entry. Mirrors the subset of ``os.DirEntry`` the walker uses."""

    name: str
    path: str

    def is_dir(self, *, follow_symlinks: bool = True) -> bool: ...
    def is_symlink(self) -> bool: ...
    def stat(self, *, follow_symlinks: bool = True) -> os.stat_result: ...


class RealFs:
    """The production implementation. Thin by design: it adds no policy."""

    def scandir(self, path: str) -> list[DirEntryLike]:
        with os.scandir(path) as it:
            return list(it)  # type: ignore[arg-type]

    def lstat(self, path: str) -> os.stat_result:
        return os.lstat(path)

    def lexists(self, path: str) -> bool:
        return os.path.lexists(path)
