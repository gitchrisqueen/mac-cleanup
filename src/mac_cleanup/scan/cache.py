"""Persistent size cache.

This, not threading, is what makes the menu appear instantly. The predecessor re-measured
every target on every render; the fix is to render from the last known numbers and refresh
behind the scenes.

The mechanism is one deliberate choice: :meth:`SizeCache.get` returns a **stale entry
flagged stale** rather than ``None``. A cache that only answers when fresh forces the caller
to block, which is the behaviour being removed.

Invalidation is TTL **or** a changed root ``mtime_ns`` **or** an engine-version bump. The
mtime check is one ``lstat`` and catches the common case -- entries added or removed at the
top of a cache directory -- regardless of TTL. It is deliberately not treated as sufficient
on its own, because a file growing in place does not touch its parent's mtime.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from typing import Any

from mac_cleanup import ENGINE_V
from mac_cleanup.paths import state_dir
from mac_cleanup.scan.sizes import EXACT

SCHEMA = 1

TTL_VOLATILE = 900.0
TTL_NORMAL = 21_600.0
TTL_COLD = 604_800.0


@dataclass(frozen=True)
class CachedSize:
    alloc: int
    logical: int
    files: int
    quality: str
    scanned_at: float
    duration_s: float
    age_s: float
    stale: bool


class SizeCache:
    """A small JSON map from ``(st_dev, st_ino, realpath)`` to a previous measurement."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.path.join(state_dir(), "sizes.json")
        self._entries: dict[str, dict[str, Any]] = {}
        self._dirty = False

    # ------------------------------------------------------------------ io

    def load(self) -> None:
        """Read the cache. **Any** failure yields an empty cache.

        A corrupt cache must never break the tool: the worst acceptable outcome is a slow
        first render, not an exception on startup.
        """
        try:
            with open(self.path, encoding="utf-8") as fh:
                blob = json.load(fh)
            if isinstance(blob, dict) and blob.get("schema") == SCHEMA:
                entries = blob.get("entries")
                if isinstance(entries, dict):
                    self._entries = entries
        except (OSError, ValueError, TypeError):
            self._entries = {}

    def save(self) -> None:
        """Atomically replace the cache file. No-op when nothing changed."""
        if not self._dirty:
            return
        directory = os.path.dirname(self.path)
        try:
            os.makedirs(directory, mode=0o700, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".sizes-", suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump({"schema": SCHEMA, "entries": self._entries}, fh)
                os.replace(tmp, self.path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
        except OSError:
            return  # a cache that cannot be written is a slow render, not a failure
        self._dirty = False

    # ------------------------------------------------------------------ api

    @staticmethod
    def key(realpath: str, st: os.stat_result) -> str:
        return f"{st.st_dev}:{st.st_ino}:{realpath}"

    def get(self, realpath: str, st: os.stat_result, ttl: float) -> CachedSize | None:
        """A previous measurement, **stale ones included and flagged**."""
        entry = self._entries.get(self.key(realpath, st))
        if not entry or entry.get("engine") != ENGINE_V:
            return None
        try:
            scanned_at = float(entry["scanned_at"])
            age = max(0.0, time.time() - scanned_at)
            fresh = (
                age <= ttl
                and entry.get("root_mtime_ns") == st.st_mtime_ns
                and entry.get("quality") == EXACT
            )
            return CachedSize(
                alloc=int(entry["alloc"]),
                logical=int(entry["logical"]),
                files=int(entry["files"]),
                quality=str(entry["quality"]),
                scanned_at=scanned_at,
                duration_s=float(entry.get("duration_s", 0.0)),
                age_s=age,
                stale=not fresh,
            )
        except (KeyError, TypeError, ValueError):
            return None

    def put(
        self,
        realpath: str,
        st: os.stat_result,
        *,
        alloc: int,
        logical: int,
        files: int,
        quality: str,
        duration_s: float,
    ) -> None:
        self._entries[self.key(realpath, st)] = {
            "alloc": alloc,
            "logical": logical,
            "files": files,
            "quality": quality,
            "scanned_at": time.time(),
            "duration_s": duration_s,
            "root_mtime_ns": st.st_mtime_ns,
            "engine": ENGINE_V,
        }
        self._dirty = True

    def drop(self, realpath: str, st: os.stat_result) -> bool:
        """Forget one measurement. Called after that path is cleaned."""
        if self._entries.pop(self.key(realpath, st), None) is None:
            return False
        self._dirty = True
        return True

    def clear(self) -> int:
        n = len(self._entries)
        self._entries = {}
        self._dirty = True
        return n

    def __len__(self) -> int:
        return len(self._entries)
