"""Size results and the quality states that keep them honest.

Quality is not decoration. The predecessor script displayed ``0 B`` for four mounted
simulator runtime volumes it could not read, and ``0 B`` reads as "nothing there, safe to
delete" -- so the user is steered toward the one action that cannot work. Every number this
tool prints carries a quality, and the renderer is forbidden from showing a bare figure for
anything but ``EXACT``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# macOS: the file's content lives in the cloud and has been evicted locally. `st_blocks` is
# 0 while `st_size` is the real size, so a blocks-based sizer reports evicted trees as
# empty. Measured on the author's machine before any eviction: 3 of 40,082 files under
# iCloud Drive. After evicting ~19.6 GiB to reclaim space, that proportion inverts -- which
# is exactly when a tool that silently reported 0 B would do the most damage to trust.
SF_DATALESS = 0x40000000

EXACT = "exact"
PARTIAL = "partial"
TOOL = "tool"
UNKNOWN = "unknown"
DATALESS = "dataless"


@dataclass
class SizeResult:
    """What a scan learned about one target."""

    alloc: int = 0
    """Sum of ``st_blocks * 512``. An UPPER bound on what deleting would free."""

    logical: int = 0
    """Sum of ``st_size``. Exceeds ``alloc`` for sparse files, below it for small files."""

    files: int = 0
    dirs: int = 0
    quality: str = UNKNOWN
    duration_s: float = 0.0

    pending_dirs: int = 0
    """Directories left unvisited when a budget blew. Non-zero implies ``PARTIAL``."""

    dataless_files: int = 0
    dataless_logical: int = 0
    """Evicted cloud files: they occupy no local blocks but are not absent."""

    errors: tuple[object, ...] = ()

    @property
    def is_lower_bound(self) -> bool:
        return self.quality in (PARTIAL, DATALESS)


@dataclass
class ScanTotals:
    """Thread-local accumulators, merged under the pump's lock."""

    alloc: dict[int, int] = field(default_factory=dict)
    logical: dict[int, int] = field(default_factory=dict)
    files: dict[int, int] = field(default_factory=dict)
    dirs: dict[int, int] = field(default_factory=dict)
    dataless: dict[int, int] = field(default_factory=dict)
    dataless_logical: dict[int, int] = field(default_factory=dict)

    def add(self, node: int, *, alloc: int, logical: int, files: int, dirs: int) -> None:
        self.alloc[node] = self.alloc.get(node, 0) + alloc
        self.logical[node] = self.logical.get(node, 0) + logical
        self.files[node] = self.files.get(node, 0) + files
        self.dirs[node] = self.dirs.get(node, 0) + dirs

    def add_dataless(self, node: int, *, count: int, logical: int) -> None:
        self.dataless[node] = self.dataless.get(node, 0) + count
        self.dataless_logical[node] = self.dataless_logical.get(node, 0) + logical

    def merge_into(self, other: ScanTotals) -> None:
        for src, dst in (
            (self.alloc, other.alloc),
            (self.logical, other.logical),
            (self.files, other.files),
            (self.dirs, other.dirs),
            (self.dataless, other.dataless),
            (self.dataless_logical, other.dataless_logical),
        ):
            for node, value in src.items():
                dst[node] = dst.get(node, 0) + value
        for d in (
            self.alloc,
            self.logical,
            self.files,
            self.dirs,
            self.dataless,
            self.dataless_logical,
        ):
            d.clear()


def is_dataless(st: object) -> bool:
    """Whether a stat result describes an evicted cloud placeholder."""
    return bool(getattr(st, "st_flags", 0) & SF_DATALESS)


def resolve_quality(
    *, budget_blown: bool, cancelled: bool, dataless_files: int, had_errors: bool
) -> str:
    """Derive quality from what happened. Never asserted by a caller."""
    if budget_blown or cancelled:
        return PARTIAL
    if dataless_files:
        return DATALESS
    if had_errors:
        return PARTIAL
    return EXACT
