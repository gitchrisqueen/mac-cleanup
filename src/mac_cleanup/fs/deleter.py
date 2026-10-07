"""The only module permitted to remove anything.

Enforced by ruff ``banned-api`` everywhere else, plus a grep backstop in
``scripts/guards.sh``. One call site means one place to audit and one seam to test.

Why fd-relative
---------------
Every directory is opened ``O_NOFOLLOW | O_DIRECTORY`` **relative to a descriptor already
held**, its ``st_dev`` re-verified with ``fstat``, and every removal issued as
``unlinkat``/``rmdir`` with ``dir_fd=``. After the initial check no full path is handed back
to the kernel, so if a racing process swaps a directory for a symlink between our listing
and our unlink, the ``open`` fails ``ELOOP`` and we unlink the link itself. ``rm -rf`` cannot
give that property and ``find -exec rm -rf {} +`` certainly cannot.

This closes the *walk*. It does not close the window between a user's confirmation and the
start of that walk, which is why :func:`delete_tree` re-checks identity before touching
anything -- and even that pins only the leaf. The honest statement, which the README repeats
beside the claim, is that the window is narrowed, not eliminated.

Why ``st_nlink`` matters to the byte count
-----------------------------------------
Unlinking one name of a hardlinked inode frees **nothing**; the blocks go when the last link
goes. Charging ``st_blocks`` for a non-final unlink would inflate "freed" past the ``statvfs``
delta and then invite blaming the gap on APFS snapshots -- a confident wrong number with a
confident wrong explanation.
"""

from __future__ import annotations

import errno as _errno
import os
import stat
from dataclasses import dataclass, field

from mac_cleanup.errors import DeleteError, Refusal, classify_oserror

MAX_DEPTH = 128
MAX_RELIST = 8
VCS_MARKERS = frozenset({".git", ".hg", ".svn", ".jj", ".bzr", "_darcs"})

OK = "ok"
PARTIAL = "partial"
FAILED = "failed"
NOTHING = "nothing_to_do"


@dataclass
class DeleteReport:
    """Per-target outcome. ``status`` is derived, never asserted by a caller."""

    files: int = 0
    dirs: int = 0
    links: int = 0
    freed_bytes: int = 0
    """Blocks of entries whose removal actually released space (``st_nlink == 1``)."""

    linked_bytes: int = 0
    linked_files: int = 0
    """Entries with other names elsewhere: removed, but freed nothing."""

    skipped_vcs: list[str] = field(default_factory=list)
    skipped_protected: list[str] = field(default_factory=list)
    errors: list[DeleteError] = field(default_factory=list)
    dry_run: bool = False

    @property
    def real_errors(self) -> list[DeleteError]:
        """Errors excluding benign races: a vanished entry is not a failure."""
        return [e for e in self.errors if e.cls != "raced"]

    @property
    def status(self) -> str:
        """Derived from what happened. This is the fix for the predecessor's unconditional
        ``Done.`` -- there is no code path that reports success without consulting this."""
        if self.real_errors:
            return PARTIAL if (self.files or self.dirs or self.links) else FAILED
        if not (self.files or self.dirs or self.links):
            return NOTHING
        return OK


class Deleter:
    """Removes a tree, symlink-proof and pinned to one device."""

    def __init__(self, *, dry_run: bool = False, allow_vcs: bool = False) -> None:
        self.dry_run = dry_run
        self.allow_vcs = allow_vcs

    # ------------------------------------------------------------------ public

    def delete_contents(self, path: str, *, skip: frozenset[str] = frozenset()) -> DeleteReport:
        """Remove everything *inside* ``path``, leaving ``path`` itself.

        This is the shape almost every cache target wants: ``~/Library/Caches`` must survive
        while its selected children do not. The predecessor deleted parents outright.
        """
        report = DeleteReport(dry_run=self.dry_run)
        try:
            st = os.lstat(path)
        except OSError as exc:
            self._record(report, path, exc)
            return report
        if not stat.S_ISDIR(st.st_mode):
            raise Refusal("E_NOT_A_DIRECTORY", path)

        parent_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            for name in self._listdir(parent_fd, path, report):
                child = os.path.join(path, name)
                if child in skip:
                    report.skipped_protected.append(child)
                    continue
                self._rm_at(parent_fd, name, child, st.st_dev, report, 0)
        finally:
            os.close(parent_fd)
        return report

    def delete_tree(self, path: str) -> DeleteReport:
        """Remove ``path`` and everything under it."""
        report = DeleteReport(dry_run=self.dry_run)
        parent = os.path.dirname(path)
        name = os.path.basename(path)
        try:
            before = os.lstat(path)
        except OSError as exc:
            self._record(report, path, exc)
            return report

        parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            # The check above may be seconds old and may sit across a user confirmation.
            # Prove the object is still the one we vetted. This pins the leaf only.
            now = os.lstat(name, dir_fd=parent_fd)
            if (now.st_dev, now.st_ino) != (before.st_dev, before.st_ino):
                raise Refusal("E_IDENTITY_CHANGED", path, "replaced after it was checked")
            self._rm_at(parent_fd, name, path, before.st_dev, report, 0)
        finally:
            os.close(parent_fd)
        return report

    # ------------------------------------------------------------------ internals

    def _rm_at(
        self,
        dir_fd: int,
        name: str,
        display: str,
        root_dev: int,
        report: DeleteReport,
        depth: int,
    ) -> None:
        if depth > MAX_DEPTH:
            raise Refusal("E_TOO_DEEP", display, f"depth > {MAX_DEPTH}")

        try:
            st = os.lstat(name, dir_fd=dir_fd)
        except OSError as exc:
            self._record(report, display, exc)
            return

        if stat.S_ISLNK(st.st_mode):
            self._unlink(dir_fd, name, display, report, is_dir=False)
            report.links += 1
            return

        if not stat.S_ISDIR(st.st_mode):
            # Blocks only count as freed when this is the last name for the inode.
            if st.st_nlink > 1:
                report.linked_files += 1
                report.linked_bytes += st.st_blocks * 512
                self._unlink(dir_fd, name, display, report, is_dir=False)
            elif self._unlink(dir_fd, name, display, report, is_dir=False):
                report.freed_bytes += st.st_blocks * 512
            report.files += 1
            return

        if not self.allow_vcs and name in VCS_MARKERS:
            # The "does this target CONTAIN a worktree" direction cannot be answered cheaply
            # up front, so it is enforced here: stop and report rather than destroy.
            report.skipped_vcs.append(display)
            return

        try:
            child_fd = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=dir_fd,
            )
        except OSError as exc:
            self._record(report, display, exc)
            return

        try:
            if os.fstat(child_fd).st_dev != root_dev:
                raise Refusal("E_CROSSES_DEVICE", display, "mount appeared mid-walk")
            # MAX_RELIST exists for the case where a live app writes into a directory
            # while it is being emptied. In dry-run nothing is removed, so re-listing would
            # return the same entries forever: count them once and move on.
            passes = 1 if self.dry_run else MAX_RELIST
            for _ in range(passes):
                entries = self._listdir(child_fd, display, report)
                if not entries:
                    break
                for entry in entries:
                    self._rm_at(
                        child_fd,
                        entry,
                        os.path.join(display, entry),
                        root_dev,
                        report,
                        depth + 1,
                    )
                if self.dry_run:
                    break
            else:
                report.errors.append(
                    DeleteError(
                        path=display,
                        errno_=_errno.ENOTEMPTY,
                        strerror=(
                            "directory kept gaining entries after "
                            f"{MAX_RELIST} passes; an app is writing here"
                        ),
                        cls="busy",
                    )
                )
                return
        finally:
            os.close(child_fd)

        if self._unlink(dir_fd, name, display, report, is_dir=True):
            report.dirs += 1

    def _listdir(self, fd: int, display: str, report: DeleteReport) -> list[str]:
        try:
            return os.listdir(fd)
        except OSError as exc:
            self._record(report, display, exc)
            return []

    def _unlink(
        self, dir_fd: int, name: str, display: str, report: DeleteReport, *, is_dir: bool
    ) -> bool:
        """Perform the removal. Dry-run skips exactly this, so both modes share one walk."""
        if self.dry_run:
            return True
        try:
            if is_dir:
                os.rmdir(name, dir_fd=dir_fd)
            else:
                os.unlink(name, dir_fd=dir_fd)
        except OSError as exc:
            self._record(report, display, exc)
            return False
        return True

    @staticmethod
    def _record(report: DeleteReport, path: str, exc: OSError) -> None:
        report.errors.append(
            DeleteError(
                path=path,
                errno_=exc.errno or 0,
                strerror=exc.strerror or str(exc),
                cls=classify_oserror(exc),
            )
        )
