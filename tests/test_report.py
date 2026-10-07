"""Report arithmetic, exit codes, and the artifact path-free guarantee."""

from __future__ import annotations

import pytest

from mac_cleanup.render import human_bytes, size_cell, truncate_middle
from mac_cleanup.report import RunReport, TargetOutcome, assert_path_free, dump_artifact
from mac_cleanup.scan.sizes import DATALESS, EXACT, PARTIAL, TOOL, UNKNOWN

GIB = 1024**3


# --------------------------------------------------------------------- rendering


def test_human_bytes_uses_binary_units():
    assert human_bytes(0) == "0 B"
    assert human_bytes(1023) == "1023 B"
    assert human_bytes(1024) == "1.00 KiB"
    assert human_bytes(int(9.43 * GIB)) == "9.43 GiB"


def test_a_bare_figure_is_shown_only_for_exact():
    assert size_cell(GIB, EXACT) == "1.00 GiB"
    assert size_cell(GIB, PARTIAL) == ">= 1.00 GiB (partial)"
    assert size_cell(0, UNKNOWN, reason="needs Docker running") == "?  (needs Docker running)"
    assert size_cell(0, TOOL) == "see details"


def test_unmeasurable_never_renders_as_zero_bytes():
    """`0 B` reads as 'nothing there, safe to delete' -- the exact misread that made the
    predecessor's unreadable runtime volumes look disposable."""
    assert "0 B" not in size_cell(0, UNKNOWN, reason="no permission")
    assert size_cell(0, UNKNOWN) == "?"


def test_evicted_cloud_content_is_labelled_not_reported_as_tiny():
    cell = size_cell(4096, DATALESS)
    assert "evicted" in cell
    assert cell != human_bytes(4096)


def test_stale_exact_sizes_carry_an_age_marker():
    assert size_cell(GIB, EXACT, age_s=3 * 3600) == "1.00 GiB ·3h"
    assert size_cell(GIB, EXACT, age_s=60) == "1.00 GiB"


def test_truncate_middle_keeps_both_ends():
    assert truncate_middle("abc", 10) == "abc"
    out = truncate_middle("/a/very/long/path/to/something", 15)
    assert len(out) == 15
    assert out.startswith("/a/v") and out.endswith("hing")


# ------------------------------------------------------------------- arithmetic


def _report(**kw):
    return RunReport(
        outcomes=[TargetOutcome("t", "ok", freed_bytes=10 * GIB)],
        free_before=1 * GIB,
        free_after=9 * GIB,
        **kw,
    )


def test_accounted_and_disk_delta_are_separate_numbers():
    r = _report()
    assert r.accounted == 10 * GIB
    assert r.disk_delta == 8 * GIB
    assert r.unaccounted == 2 * GIB, "the gap is reported, not hidden"


def test_the_gap_is_explained_rather_than_blamed_on_one_cause():
    text = _report(snapshots=("snap1",)).human()
    assert "Unaccounted" in text
    assert "snapshots" in text
    assert "clones" in text, "clones are named too; snapshots alone would be a guess"


def test_links_removed_is_reported_separately_from_freed():
    r = RunReport(outcomes=[TargetOutcome("t", "ok", freed_bytes=0, linked_bytes=5 * GIB)])
    assert "Links removed" in r.human()
    assert "no space released" in r.human()


@pytest.mark.parametrize(
    ("statuses", "code"),
    [
        (["ok"], 0),
        (["ok", "partial"], 2),
        (["ok", "failed"], 3),
        (["partial", "failed"], 3),
        ([], 4),
    ],
)
def test_one_exit_code_per_meaning(statuses, code):
    r = RunReport(outcomes=[TargetOutcome(f"t{i}", s) for i, s in enumerate(statuses)])
    assert r.exit_code() == code


def test_a_partial_target_is_named_in_the_summary():
    r = RunReport(
        outcomes=[
            TargetOutcome("quiet", "ok", freed_bytes=GIB),
            TargetOutcome("noisy", "partial", freed_bytes=GIB, errors=2, error_class="permission"),
        ]
    )
    text = r.human()
    assert "noisy" in text and "PARTIAL" in text and "permission" in text
    assert "quiet" not in text, "successful targets do not need a line each"


# --------------------------------------------------------------- artifact safety


def test_artifact_rejects_absolute_paths():
    with pytest.raises(ValueError, match="path-like"):
        assert_path_free({"a": "/Users/someone/Documents/case.pdf"}, home="/Users/someone")


def test_artifact_rejects_the_home_directory_name_anywhere():
    with pytest.raises(ValueError, match="path-like"):
        assert_path_free({"note": "cleared cache for someone"}, home="/Users/someone")


def test_artifact_rejects_a_nested_path(tmp_path):
    bad = {"targets": [{"target_id": "x"}, {"target_id": "y", "note": "/private/var/x"}]}
    with pytest.raises(ValueError) as ei:
        assert_path_free(bad, home="/Users/nobody")
    assert "targets[1].note" in str(ei.value), "the offender is located, not just flagged"


def test_a_real_run_artifact_passes_the_guard():
    r = RunReport(outcomes=[TargetOutcome("brew-cache", "ok", freed_bytes=GIB)])
    assert_path_free(r.to_artifact(), home="/Users/nobody")


def test_dump_refuses_to_write_an_artifact_containing_a_path(tmp_path):
    out = tmp_path / "bad.json"
    with pytest.raises(ValueError, match="path-like"):
        dump_artifact({"p": "/etc/passwd"}, str(out), home="/Users/nobody")
    assert not out.exists(), "nothing is written when the guard fires"


# ----------------------------------------------------------------- dry-run honesty


def test_a_dry_run_never_presents_a_statvfs_delta():
    """Nothing was removed, so any movement in free space is other processes. Printing it
    as a result would be the exact class of lie this project exists to remove."""
    r = RunReport(
        outcomes=[TargetOutcome("t", "ok", freed_bytes=10 * GIB)],
        free_before=1 * GIB,
        free_after=9 * GIB,
        dry_run=True,
    )
    text = r.human()
    assert "Would free" in text
    assert "Disk free delta" not in text
    assert "Unaccounted" not in text


def test_a_dry_run_artifact_nulls_the_delta_rather_than_zeroing_it():
    """A 0 would read as 'measured no change'. null says 'not measured'."""
    r = RunReport(
        outcomes=[TargetOutcome("t", "ok", freed_bytes=GIB)],
        free_before=GIB,
        free_after=2 * GIB,
        dry_run=True,
    )
    art = r.to_artifact()
    assert art["dry_run"] is True
    assert art["disk_delta_bytes"] is None
    assert art["unaccounted_bytes"] is None
    assert art["accounted_bytes"] == GIB


def test_a_real_run_does_report_both_numbers():
    r = RunReport(
        outcomes=[TargetOutcome("t", "ok", freed_bytes=GIB)],
        free_before=GIB,
        free_after=2 * GIB,
        dry_run=False,
    )
    assert "Disk free delta" in r.human()
    assert r.to_artifact()["disk_delta_bytes"] == GIB
