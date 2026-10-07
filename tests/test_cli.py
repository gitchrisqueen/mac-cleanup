"""CLI contract: one exit code per meaning, and the flag rules that keep an unattended run
from deleting without saying so."""

from __future__ import annotations

import os

import pytest

from mac_cleanup.cli import (
    EXIT_DRY_RUN_DEFAULT,
    EXIT_GUARD_SKIPPED,
    EXIT_NOTHING,
    EXIT_OK,
    EXIT_USAGE,
    build_parser,
    check_environment,
    is_cloud_path,
    main,
    resolve_selectors,
    validate_clean_flags,
)
from mac_cleanup.errors import Refusal


def parse_clean(*argv):
    return build_parser().parse_args(["clean", *argv])


# ------------------------------------------------------------------- flag contract


def test_yes_always_requires_apply():
    """A tty is not a human: `tmux new-session -d 'mac-cleanup --yes'` has one. Keying the
    requirement on --yes rather than isatty is what stops that being an unattended delete
    with no destructive word in the crontab."""
    result = validate_clean_flags(parse_clean("--yes"))
    assert result.ok is False
    assert "--yes requires --apply" in result.message


def test_yes_with_apply_is_accepted():
    assert validate_clean_flags(parse_clean("--yes", "--apply")).ok is True


def test_dry_run_and_apply_together_are_refused_rather_than_last_flag_wins():
    result = validate_clean_flags(parse_clean("--dry-run", "--apply"))
    assert result.ok is False
    assert "mutually exclusive" in result.message


def test_yes_with_dry_run_is_still_refused_because_apply_is_absent():
    assert validate_clean_flags(parse_clean("--yes", "--dry-run")).ok is False


def test_plain_dry_run_is_fine():
    assert validate_clean_flags(parse_clean("--dry-run")).ok is True


# ----------------------------------------------------------------------- exit codes


def test_clean_with_no_selection_reports_nothing_rather_than_guessing():
    assert main(["clean"]) == EXIT_NOTHING


def test_clean_without_apply_exits_dry_run_default(capsys, monkeypatch, tmp_path):
    """A real selection, no --apply: plans and exits 5, never 0, so a CI step that forgot
    --apply is visibly not-a-success rather than a green no-op.

    The target is injected rather than taken from the built-in list, because every built-in
    path is macOS-specific and the suite runs on Linux. Testing the behaviour beats testing
    the host.
    """
    from mac_cleanup import cli as cli_mod
    from mac_cleanup import service as service_mod
    from mac_cleanup.targets.model import PathTarget

    junk = tmp_path / "junk"
    junk.mkdir()
    (junk / "f").write_bytes(b"x" * 100)
    fake = PathTarget(target_id="test.target", path=str(junk), label="injected", group="test")
    registry = {"test.target": fake}
    monkeypatch.setattr(cli_mod, "by_id", lambda: registry)
    monkeypatch.setattr(service_mod, "by_id", lambda: registry)

    assert main(["clean", "--select", "test.target"]) == EXIT_DRY_RUN_DEFAULT
    assert "DRY RUN" in capsys.readouterr().err
    assert (junk / "f").exists(), "a dry run must not remove anything"


def test_clean_with_yes_but_no_apply_is_a_usage_error():
    assert main(["clean", "--yes"]) == EXIT_USAGE


def test_no_subcommand_prints_help_and_exits_usage(capsys):
    assert main([]) == EXIT_USAGE
    assert "usage" in capsys.readouterr().out.lower()


def test_profile_list_succeeds(capsys):
    assert main(["profile", "list"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "safe v1" in out and "dev v1" in out


def test_profile_show_names_its_targets(capsys):
    assert main(["profile", "show", "safe"]) == EXIT_OK
    assert "xcode.derived-data" in capsys.readouterr().out


def test_unknown_profile_is_a_usage_error():
    assert main(["profile", "show", "nope"]) == EXIT_USAGE


def test_doctor_succeeds(capsys):
    assert main(["doctor"]) == EXIT_OK
    assert "state_dir_ok" in capsys.readouterr().out


def test_doctor_output_contains_no_token_shaped_value(capsys):
    """`doctor` is what a user pastes into a bug report, and ~/.npmrc holds a plaintext
    auth token on the author's machine. Output is allowlisted field-by-field, never dumped."""
    main(["doctor"])
    out = capsys.readouterr().out
    for needle in ("_authToken", "npm_", "ghp_", "Bearer ", "-----BEGIN"):
        assert needle not in out


# ------------------------------------------------------------------------ selectors


def test_bare_numbers_are_refused_with_a_suggestion():
    """Positional indexes are the predecessor's mis-targeting hazard: it re-sorted by a
    recomputed size list, so index 7 could mean two different rows."""
    with pytest.raises(Refusal) as ei:
        resolve_selectors(["7"])
    assert ei.value.code == "E_NUMERIC_SELECTOR"
    assert "Did you mean" in str(ei.value)


def test_unknown_target_is_refused():
    with pytest.raises(Refusal) as ei:
        resolve_selectors(["nope.nope"])
    assert ei.value.code == "E_UNKNOWN_TARGET"


def test_ids_groups_globs_and_all_resolve():
    assert resolve_selectors(["xcode.derived-data"]) == ["xcode.derived-data"]
    assert "cache.pypoetry" in resolve_selectors(["group:python"])
    assert all(i.startswith("cache.") for i in resolve_selectors(["cache.*"]))
    assert len(resolve_selectors(["all"])) >= 18


def test_selectors_are_deduplicated_preserving_order():
    out = resolve_selectors(["xcode.derived-data", "group:xcode", "xcode.derived-data"])
    assert out[0] == "xcode.derived-data"
    assert len(out) == len(set(out))


# ------------------------------------------------------------------- cloud refusal


def test_cloud_paths_are_detected():
    assert is_cloud_path(os.path.expanduser("~/Library/Mobile Documents")) is True
    assert is_cloud_path(os.path.expanduser("~/Library/CloudStorage")) is True
    assert is_cloud_path("/tmp") is False


def test_du_refuses_a_cloud_path_by_default(capsys):
    """Enumerating a provider makes it fetch content from the server -- a network transfer
    and a write, from a command billed as read-only."""
    code = main(["du", os.path.expanduser("~/Library/Mobile Documents")])
    assert code == EXIT_GUARD_SKIPPED
    err = capsys.readouterr().err
    assert "cloud provider" in err and "--cloud" in err


# ------------------------------------------------------------------ preflight gates


def test_environment_check_passes_on_a_normal_account():
    assert check_environment().ok is True


def test_a_spoofed_home_is_refused(monkeypatch):
    monkeypatch.setenv("HOME", "/tmp/not-my-home")
    result = check_environment()
    assert result.ok is False
    assert "disagrees with the password database" in result.message


def test_a_state_dir_inside_a_cleaned_target_is_refused(monkeypatch):
    """Reachable with one `export`. An earlier design validated this against allowlist roots
    that only exist in v0.2, i.e. against the empty set, so it refused nothing."""
    monkeypatch.setenv(
        "XDG_STATE_HOME", os.path.expanduser("~/Library/Developer/Xcode/DerivedData/.state")
    )
    result = check_environment()
    assert result.ok is False
    assert "E_STATE_DIR_CONFLICT" in result.message


def test_a_state_dir_inside_the_caches_tree_is_refused(monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", os.path.expanduser("~/Library/Caches/xdg"))
    assert check_environment().ok is False
