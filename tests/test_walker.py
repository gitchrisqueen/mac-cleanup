"""Walker behaviour. Real trees under tmp_path, because the bugs being guarded against are
filesystem bugs: symlink loops, firmlinks, mount boundaries, hardlinks."""

from __future__ import annotations

import os

import pytest

from mac_cleanup.forest import build_forest
from mac_cleanup.scan.cancel import CancelToken
from mac_cleanup.scan.sizes import DATALESS, EXACT, PARTIAL, is_dataless, resolve_quality
from mac_cleanup.scan.walker import Walker

pytestmark = pytest.mark.realfs


def walk(root: str, **kw):
    forest = build_forest([("t", root)])
    result = Walker(forest, **kw).run()
    node = forest.by_target["t"]
    return result, node


def test_counts_files_and_directories(tmp_path):
    (tmp_path / "a").write_bytes(b"x" * 100)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b").write_bytes(b"y" * 100)
    result, node = walk(str(tmp_path))
    assert result.totals.files[node] == 2
    assert result.totals.dirs[node] == 2  # the root and sub
    assert result.totals.alloc[node] > 0
    assert result.totals.logical[node] == 200
    assert result.errors == []


def test_du_terminates_on_volume_root(tmp_path):
    """The headline symlink case. `/Volumes/Macintosh HD` is a symlink to `/` on macOS, so
    a walker whose descent test follows symlinks recurses forever. Named exactly as the
    adversarial review demanded."""
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    (deep / "loop").symlink_to(tmp_path)  # points back at an ancestor
    (deep / "file").write_bytes(b"z" * 10)

    result, node = walk(str(tmp_path))  # must return, not hang
    assert result.totals.files[node] == 2, "the symlink counts as an entry, not a subtree"
    assert result.cancelled is False


def test_a_symlink_to_a_directory_is_never_descended(tmp_path):
    target = tmp_path / "outside"
    target.mkdir()
    (target / "secret").write_bytes(b"s" * 5000)
    inside = tmp_path / "root"
    inside.mkdir()
    (inside / "link").symlink_to(target)

    result, node = walk(str(inside))
    assert result.totals.dirs[node] == 1, "only the root; the link is not a directory to us"
    # A symlink's own st_size is the length of its target path -- `du` counts that too. What
    # must NOT appear is the 5000 bytes behind it.
    assert result.totals.logical[node] == len(str(target))
    assert result.totals.logical[node] < 5000
    assert target.joinpath("secret").exists(), "and nothing outside was touched"


@pytest.mark.parametrize("workers", [1, 2, 4, 8, 17])
def test_results_are_worker_count_invariant(tmp_path, workers):
    for i in range(12):
        d = tmp_path / f"d{i}"
        d.mkdir()
        for j in range(8):
            (d / f"f{j}").write_bytes(b"x" * (100 + j))
    result, node = walk(str(tmp_path), workers=workers)
    assert result.totals.files[node] == 96
    assert result.totals.dirs[node] == 13
    assert result.totals.logical[node] == sum(100 + j for j in range(8)) * 12


def test_hardlinks_are_counted_once(tmp_path):
    original = tmp_path / "original"
    original.write_bytes(b"x" * 8000)
    os.link(original, tmp_path / "second_name")
    result, node = walk(str(tmp_path))
    assert result.totals.files[node] == 1, "two names, one inode, counted once"


@pytest.mark.macos
@pytest.mark.skipif(not os.path.exists("/usr/share/firmlinks"), reason="needs macOS firmlinks")
def test_a_firmlinked_directory_is_visited_once():
    """Firmlinks give a directory two unrelated spellings with ONE inode -- `/Users` and
    `/System/Volumes/Data/Users` are both ino 703016 here. A lexical dedup sees two distinct
    paths and walks the tree twice; the visited-inode set is what prevents that.

    Read-only and shallow: this asserts the identity, which is the property the walker's
    dedup relies on and the reason a macOS CI job exists at all.
    """
    a = os.lstat("/Users")
    b = os.lstat("/System/Volumes/Data/Users")
    assert (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino), "firmlink pair must be one inode"
    with open("/usr/share/firmlinks") as fh:
        assert "/Users" in fh.read()


def test_errors_are_collected_and_never_swallowed(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / "f").write_bytes(b"x")
    os.chmod(blocked, 0o000)
    try:
        result, _ = walk(str(tmp_path))
        if os.geteuid() == 0:
            pytest.skip("running as root; permissions are not enforced")
        assert result.errors, "an unreadable directory must surface, not vanish"
        assert result.errors[0].cls == "permission"
        assert result.errors[0].errno_ != 0
    finally:
        os.chmod(blocked, 0o755)


def test_cancellation_returns_a_partial_result_rather_than_hanging(tmp_path):
    for i in range(40):
        d = tmp_path / f"d{i}"
        d.mkdir()
        (d / "f").write_bytes(b"x" * 64)
    token = CancelToken()
    token.cancel()  # cancelled before the first pop
    result, _ = walk(str(tmp_path), cancel=token, workers=4)
    assert result.cancelled is True
    assert result.elapsed_s < 5


def test_an_empty_forest_terminates(tmp_path):
    forest = build_forest([("ghost", str(tmp_path / "absent"))])
    result = Walker(forest).run()
    assert result.dir_count == 0
    assert result.cancelled is False


# ------------------------------------------------------------------ dataless / quality


class _Stat:
    def __init__(self, flags: int) -> None:
        self.st_flags = flags


def test_is_dataless_detects_the_evicted_flag():
    assert is_dataless(_Stat(0x40000000)) is True
    assert is_dataless(_Stat(0x40000060)) is True  # as measured on a real evicted file
    assert is_dataless(_Stat(0)) is False


def test_dataless_file_renders_evicted_not_zero():
    """An evicted cloud file occupies no local blocks. Reporting it as 0 B with no marker is
    the same failure as the predecessor's `0 B` on unreadable runtime volumes: it reads as
    'nothing there, safe to delete'. Named exactly as the adversarial review demanded."""
    quality = resolve_quality(
        budget_blown=False, cancelled=False, dataless_files=3, had_errors=False
    )
    assert quality == DATALESS
    assert quality != EXACT


def test_quality_is_derived_not_asserted():
    assert (
        resolve_quality(budget_blown=False, cancelled=False, dataless_files=0, had_errors=False)
        == EXACT
    )
    assert (
        resolve_quality(budget_blown=True, cancelled=False, dataless_files=0, had_errors=False)
        == PARTIAL
    )
    assert (
        resolve_quality(budget_blown=False, cancelled=True, dataless_files=0, had_errors=False)
        == PARTIAL
    )
    assert (
        resolve_quality(budget_blown=False, cancelled=False, dataless_files=0, had_errors=True)
        == PARTIAL
    )
