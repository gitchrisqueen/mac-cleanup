"""Deleter behaviour.

Every test here is ``destructive``, so the seatbelt holds them to their own ``tmp_path``.
These are the tests that would catch a regression removing the wrong thing.
"""

from __future__ import annotations

import os

import pytest

from mac_cleanup.errors import Refusal
from mac_cleanup.fs.deleter import FAILED, NOTHING, OK, PARTIAL, Deleter

pytestmark = [pytest.mark.destructive, pytest.mark.realfs]


def tree(root, spec):
    root.mkdir(parents=True, exist_ok=True)
    for name, val in spec.items():
        if isinstance(val, dict):
            tree(root / name, val)
        else:
            (root / name).write_bytes(b"x" * val)
    return root


# ------------------------------------------------------------------- happy paths


def test_delete_contents_empties_but_keeps_the_directory(tmp_path):
    target = tree(tmp_path / "caches", {"a": 100, "sub": {"b": 100}})
    report = Deleter().delete_contents(str(target))
    assert report.status == OK
    assert target.exists(), "the target directory itself must survive"
    assert list(target.iterdir()) == []
    assert report.files == 2
    assert report.dirs == 1
    assert report.freed_bytes > 0


def test_delete_tree_removes_the_root_too(tmp_path):
    target = tree(tmp_path / "doomed", {"a": 50})
    report = Deleter().delete_tree(str(target))
    assert report.status == OK
    assert not target.exists()


def test_empty_target_reports_nothing_to_do_not_success(tmp_path):
    target = tmp_path / "empty"
    target.mkdir()
    assert Deleter().delete_contents(str(target)).status == NOTHING


def test_missing_target_is_recorded_as_a_raced_error_not_a_crash(tmp_path):
    report = Deleter().delete_contents(str(tmp_path / "absent"))
    assert report.errors and report.errors[0].cls == "raced"
    assert report.real_errors == [], "a vanished entry is benign, not a failure"


# ---------------------------------------------------------------- protection rules


def test_unselected_descendants_in_the_skip_set_survive(tmp_path):
    caches = tree(tmp_path / "caches", {"junk": 10, "keep": {"precious": 10}})
    keep = str(caches / "keep")
    report = Deleter().delete_contents(str(caches), skip=frozenset({keep}))
    assert report.status == OK
    assert not (caches / "junk").exists()
    assert (caches / "keep" / "precious").exists(), "skip set must be honoured"
    assert report.skipped_protected == [keep]


def test_a_symlink_is_unlinked_and_its_target_survives(tmp_path):
    outside = tree(tmp_path / "outside", {"precious": 5000})
    inside = tmp_path / "inside"
    inside.mkdir()
    (inside / "link").symlink_to(outside)

    report = Deleter().delete_contents(str(inside))
    assert report.links == 1
    assert report.dirs == 0, "a symlink to a directory is not descended"
    assert outside.joinpath("precious").exists(), "the link's target must be untouched"


def test_a_vcs_worktree_inside_the_target_stops_the_subtree(tmp_path):
    caches = tree(tmp_path / "caches", {"junk": 10, "proj": {".git": {"HEAD": 20}}})
    report = Deleter().delete_contents(str(caches))
    assert any(".git" in p for p in report.skipped_vcs)
    assert (caches / "proj" / ".git" / "HEAD").exists(), "stop and tell, never destroy"
    assert not (caches / "junk").exists(), "unrelated entries are still cleaned"


def test_delete_contents_refuses_a_non_directory(tmp_path):
    f = tmp_path / "file"
    f.write_bytes(b"x")
    with pytest.raises(Refusal) as ei:
        Deleter().delete_contents(str(f))
    assert ei.value.code == "E_NOT_A_DIRECTORY"


# ------------------------------------------------------------------- accounting


def test_a_hardlinked_file_is_removed_but_not_counted_as_freed(tmp_path):
    """Unlinking one name of two frees nothing. Counting it would push 'freed' past the
    statvfs delta and invite blaming the gap on snapshots."""
    keeper = tmp_path / "keeper"
    keeper.write_bytes(b"x" * 8192)
    target = tmp_path / "target"
    target.mkdir()
    os.link(keeper, target / "second_name")

    report = Deleter().delete_contents(str(target))
    assert report.files == 1
    assert report.linked_files == 1
    assert report.linked_bytes > 0
    assert report.freed_bytes == 0, "no space was released; the other name still holds it"
    assert keeper.exists()


def test_the_last_link_does_count_as_freed(tmp_path):
    target = tree(tmp_path / "t", {"only": 8192})
    report = Deleter().delete_contents(str(target))
    assert report.linked_files == 0
    assert report.freed_bytes > 0


# ------------------------------------------------------------------- honest errors


def test_an_unreadable_subdirectory_yields_partial_not_success(tmp_path):
    """The regression test for the predecessor's unconditional `Done.`"""
    if os.geteuid() == 0:
        pytest.skip("running as root; permissions are not enforced")
    caches = tree(tmp_path / "caches", {"ok": 10, "blocked": {"inner": 10}})
    blocked = caches / "blocked"
    os.chmod(blocked, 0o000)
    try:
        report = Deleter().delete_contents(str(caches))
        assert report.status == PARTIAL, "bytes were freed AND something failed"
        assert report.real_errors, "the failure must be reported, not discarded"
        assert report.real_errors[0].cls == "permission"
        assert not (caches / "ok").exists(), "the parts that could be removed were"
    finally:
        os.chmod(blocked, 0o755)


def test_total_failure_reports_failed_not_partial(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("running as root; permissions are not enforced")
    caches = tree(tmp_path / "caches", {"blocked": {"inner": 10}})
    blocked = caches / "blocked"
    os.chmod(blocked, 0o000)
    try:
        report = Deleter().delete_contents(str(caches))
        assert report.status == FAILED, "nothing was freed and something failed"
    finally:
        os.chmod(blocked, 0o755)


# ------------------------------------------------------------------------ dry run


def test_dry_run_walks_the_same_code_path_and_changes_nothing(tmp_path):
    """The dry-run figure must come from the same walk as the real one, so a clean plan is
    evidence about the real thing rather than a separate optimistic simulation."""
    spec = {"a": 100, "sub": {"b": 200}}
    dry_target = tree(tmp_path / "dry", dict(spec))
    wet_target = tree(tmp_path / "wet", dict(spec))

    dry = Deleter(dry_run=True).delete_contents(str(dry_target))
    wet = Deleter().delete_contents(str(wet_target))

    assert dry.dry_run is True
    assert (dry.files, dry.dirs, dry.freed_bytes) == (wet.files, wet.dirs, wet.freed_bytes)
    assert (dry_target / "a").exists(), "dry run must not remove anything"
    assert not (wet_target / "a").exists()
