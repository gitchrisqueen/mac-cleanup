"""Work-stealing directory walker.

Design notes, because the obvious implementations are all subtly wrong
---------------------------------------------------------------------
*Not* ``ThreadPoolExecutor``: tasks spawn tasks, so ``map``/``as_completed`` cannot express
termination, a queued future cannot be cancelled, and there is no way to prune a subtree
whose budget blew.

**Lock discipline.** Two critical sections per *directory*, not per file:

1. pop a directory and ``active += 1`` together, so a worker is never invisible to the
   termination test while it holds work;
2. push children and ``active -= 1`` together, ending with ``notify_all()`` when the stack
   is empty and nobody is active.

Termination is ``stack empty AND active == 0``. Sentinel values do not work here: under
work stealing a sentinel can be drawn while another worker is mid-``scandir`` and about to
push more.

**Cancellation latency is ``max(one scandir, poll interval)``, not "one scandir".** An idle
worker is parked in ``wait()`` and by definition is not popping, so it is not checking the
token either. Cancelling therefore sets the flag *and* notifies.

**Descent uses ``is_dir(follow_symlinks=False)``.** The default follows, and on this author's
machine ``/Volumes/Macintosh HD`` is a symlink to ``/`` -- so a walker that pins
``stat(follow_symlinks=False)`` for sizing but leaves ``is_dir`` at its default recurses
forever on ``du /``.

**A global visited set of directory ``(st_dev, st_ino)``** closes the firmlink double-walk:
``/Users`` and ``/System/Volumes/Data/Users`` are one inode with two unrelated spellings
(verified ``ino=703016`` both ways), and ``/usr/share/firmlinks`` lists six such pairs.

This walker **reads only; it never unlinks.** It is deliberately not TOCTOU-hardened,
because a races here costs an inaccurate number rather than a wrong deletion. That is a
narrower claim than "it only reads": measurement can still cause writes elsewhere -- running
``xcrun simctl runtime list`` was observed making CoreSimulatorService rewrite a root-owned
plist -- so the honest statement is the one in this paragraph's first sentence.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass

from mac_cleanup.errors import ScanError, classify_oserror
from mac_cleanup.forest import Forest, attribute
from mac_cleanup.scan.budget import BudgetFlag
from mac_cleanup.scan.cancel import CancelToken
from mac_cleanup.scan.sizes import ScanTotals, is_dataless

FLUSH_EVERY = 64
POLL_SECONDS = 0.25


@dataclass
class WalkResult:
    totals: ScanTotals
    errors: list[ScanError]
    pending_dirs: int
    cancelled: bool
    elapsed_s: float
    dir_count: int


class Walker:
    """One shared LIFO stack over the whole forest, drained by N threads."""

    def __init__(
        self,
        forest: Forest,
        *,
        workers: int = 8,
        cancel: CancelToken | None = None,
        skip: frozenset[str] = frozenset(),
        budget_flags: dict[int, BudgetFlag] | None = None,
        cross_device: bool = False,
        follow_firmlinks: bool = False,
        sink: object | None = None,
    ) -> None:
        self.forest = forest
        self.workers = max(1, workers)
        self.cancel = cancel or CancelToken()
        self.skip = skip
        self.budget_flags = budget_flags or {}
        self.cross_device = cross_device
        self.follow_firmlinks = follow_firmlinks
        self.sink = sink

        self._cv = threading.Condition()
        self._stack: deque[tuple[str, int, int]] = deque()  # (path, node_id, root_dev)
        self._active = 0
        self._done = False

        self._totals = ScanTotals()
        self._errors: list[ScanError] = []
        self._seen_inodes: set[tuple[int, int]] = set()
        self._seen_dirs: set[tuple[int, int]] = set()
        self._dir_count = 0
        self.current_path = ""
        self._finished = threading.Event()

        self.cancel.wake = self._cv

    # ------------------------------------------------------------------ public

    def run(self) -> WalkResult:
        started = time.monotonic()
        with self._cv:
            for node_id in self.forest.roots:
                key = self.forest.nodes[node_id].key
                try:
                    dev = os.lstat(key).st_dev
                except OSError as exc:
                    self._record(key, exc)
                    continue
                self._stack.append((key, node_id, dev))
            if not self._stack:
                self._done = True

        threads = [
            threading.Thread(target=self._worker, daemon=True, name=f"walk-{i}")
            for i in range(self.workers)
        ]
        for t in threads:
            t.start()

        # One pump thread owns all progress output. Workers never print, so there is
        # exactly one writer and no interleaving. Without this a long walk is
        # indistinguishable from a hang -- which is the complaint this project started from.
        pump = None
        if self.sink is not None:
            pump = threading.Thread(target=self._pump, daemon=True, name="walk-pump")
            pump.start()

        for t in threads:
            t.join()
        self._finished.set()
        if pump is not None:
            pump.join(timeout=1.0)

        with self._cv:
            pending = len(self._stack)
        return WalkResult(
            totals=self._totals,
            errors=list(self._errors),
            pending_dirs=pending,
            cancelled=self.cancel.cancelled,
            elapsed_s=time.monotonic() - started,
            dir_count=self._dir_count,
        )

    def _pump(self) -> None:
        """Publish progress at a fixed cadence until the walk finishes."""
        sink = self.sink
        assert sink is not None
        while not self._finished.wait(0.1):
            with self._cv:
                total = sum(self._totals.alloc.values())
                files = sum(self._totals.files.values())
                pending = len(self._stack)
                current = self.current_path
            sink.update(  # type: ignore[attr-defined]
                bytes_done=total, files=files, pending=pending, current=current
            )

    # ------------------------------------------------------------------ worker

    def _worker(self) -> None:
        local = ScanTotals()
        since_flush = 0
        while True:
            with self._cv:
                while not self._stack and not self._done:
                    if self._active == 0:
                        self._done = True
                        self._cv.notify_all()
                        break
                    self._cv.wait(POLL_SECONDS)
                if self.cancel.cancelled:
                    self._done = True
                    self._cv.notify_all()
                    break
                if not self._stack:
                    if self._done:
                        break
                    continue
                path, node_id, root_dev = self._stack.pop()
                self._active += 1

            try:
                self._scan_one(path, node_id, root_dev, local)
            finally:
                with self._cv:
                    self._active -= 1
                    if not self._stack and self._active == 0:
                        self._done = True
                    self._cv.notify_all()

            since_flush += 1
            if since_flush >= FLUSH_EVERY:
                self._flush(local)
                since_flush = 0

        self._flush(local)

    def _scan_one(self, path: str, node_id: int, root_dev: int, local: ScanTotals) -> None:
        if self.budget_flags.get(node_id, _NEVER).blown:
            return

        alloc = logical = files = 0
        children: list[tuple[str, int, int]] = []
        try:
            entries = os.scandir(path)
        except OSError as exc:
            self._record(path, exc)
            return

        with entries as it:
            for entry in it:
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    self._record(entry.path, exc)
                    continue

                # follow_symlinks=False is load-bearing: the default would recurse through
                # /Volumes/Macintosh HD -> / forever.
                if entry.is_dir(follow_symlinks=False):
                    child = entry.path
                    if child in self.skip:
                        continue
                    if not self.cross_device and st.st_dev != root_dev:
                        continue  # a mount point; never crossed implicitly
                    if not self.follow_firmlinks:
                        ident = (st.st_dev, st.st_ino)
                        with self._cv:
                            if ident in self._seen_dirs:
                                continue
                            self._seen_dirs.add(ident)
                    alloc += st.st_blocks * 512
                    children.append((child, attribute(self.forest, child, node_id), root_dev))
                    continue

                if is_dataless(st):
                    local.add_dataless(node_id, count=1, logical=st.st_size)
                    files += 1
                    continue

                if st.st_nlink > 1:
                    ident = (st.st_dev, st.st_ino)
                    with self._cv:
                        if ident in self._seen_inodes:
                            continue
                        self._seen_inodes.add(ident)

                alloc += st.st_blocks * 512
                logical += st.st_size
                files += 1

        local.add(node_id, alloc=alloc, logical=logical, files=files, dirs=1)
        self._dir_count += 1
        if children:
            with self._cv:
                self._stack.extend(children)
                self.current_path = path
                self._cv.notify(min(len(children), self.workers))

    # ------------------------------------------------------------------ helpers

    def _flush(self, local: ScanTotals) -> None:
        with self._cv:
            local.merge_into(self._totals)

    def _record(self, path: str, exc: OSError) -> None:
        err = ScanError(
            path=path,
            errno_=exc.errno or 0,
            strerror=exc.strerror or str(exc),
            cls=classify_oserror(exc),
        )
        with self._cv:
            self._errors.append(err)


class _NeverBlown:
    blown = False


_NEVER = _NeverBlown()
