"""Parser tests against real captured output, plus the malformed and hostile variants.

Seven cases per tool, because external tool output is untrusted input: normal, empty,
truncated, unknown field added, expected field removed, a path containing a newline and a
quote, and output that tries to steer the tool somewhere dangerous.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mac_cleanup.errors import Refusal
from mac_cleanup.ext.brew import parse_cleanup_dry_run
from mac_cleanup.ext.simctl import parse_device_availability, parse_runtime_list_json

REC = Path(__file__).parent / "fakes" / "recorded"
GIB = 1024**3


def rec(name: str) -> str:
    return (REC / name).read_text()


# ----------------------------------------------------------------------------- brew


def test_brew_normal_output_matches_the_captured_figure():
    assert parse_cleanup_dry_run(rec("brew_cleanup_n.txt")) == int(4.8 * GIB)
    assert parse_cleanup_dry_run(rec("brew_cleanup_s_n.txt")) == int(7.1 * GIB)


def test_brew_empty_output_is_zero_not_a_crash():
    assert parse_cleanup_dry_run("") == 0
    assert parse_cleanup_dry_run("   \n ") == 0


def test_brew_output_without_the_summary_line_raises_rather_than_guessing():
    """Reporting 0 B here would read as 'nothing to clean'. A named parse failure is the
    only honest outcome when the format changed."""
    with pytest.raises(Refusal) as ei:
        parse_cleanup_dry_run("Removing: /usr/local/Cellar/foo/1.0... (12 files, 3MB)\n")
    assert ei.value.code == "E_PARSE_BREW"


def test_brew_truncated_mid_line_raises():
    with pytest.raises(Refusal):
        parse_cleanup_dry_run("==> This operation would free appro")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("would free approximately 512B of disk space.", 512),
        ("would free approximately 2KB of disk space.", 2048),
        ("would free up approximately 1.5MB of disk space.", int(1.5 * 1024**2)),
        ("WOULD FREE APPROXIMATELY 3GB OF DISK SPACE.", 3 * GIB),
    ],
)
def test_brew_units_and_casing(text, expected):
    assert parse_cleanup_dry_run(text) == expected


def test_brew_output_with_a_newline_and_quote_in_a_path_still_parses():
    weird = 'Removing: /usr/local/Cellar/a"b\nc/1.0... (1 files, 1B)\n'
    assert parse_cleanup_dry_run(weird + rec("brew_cleanup_n.txt")) == int(4.8 * GIB)


# --------------------------------------------------------------------------- simctl


def test_simctl_runtime_list_parses_every_field():
    runtimes = parse_runtime_list_json(rec("simctl_runtime_list.json"))
    assert len(runtimes) == 4
    by_build = {r.build: r for r in runtimes}

    current = by_build["23D8133"]
    assert current.version == "26.3.1"
    assert current.platform == "iphonesimulator"
    assert current.size_bytes == 10450316672
    assert current.state == "Ready"

    # The three that Phase 0 removed. 17.24, not the 17.25 the plan first stated: that
    # figure came from summing per-runtime values that had already been rounded.
    stale = sum(by_build[b].size_bytes for b in ("21A328", "21A342", "21J353"))
    assert stale == 18_515_954_231
    assert round(stale / GIB, 2) == 17.24
    assert by_build["21J353"].platform == "appletvsimulator"


def test_simctl_empty_output_is_an_empty_list():
    assert parse_runtime_list_json("") == []
    assert parse_device_availability("") == (0, 0)


def test_simctl_non_json_raises_a_named_error_not_a_json_error():
    with pytest.raises(Refusal) as ei:
        parse_runtime_list_json("== Disk Images ==\niOS 26.3.1 (23D8133) - ... (Ready)\n")
    assert ei.value.code == "E_PARSE_SIMCTL"


def test_simctl_truncated_json_raises():
    with pytest.raises(Refusal):
        parse_runtime_list_json('{"A": {"build": "23D')


def test_simctl_unknown_field_is_ignored_for_forward_compatibility():
    text = json.dumps(
        {"A": {"build": "X1", "version": "1.0", "sizeBytes": 10, "state": "Ready", "newThing": 1}}
    )
    assert parse_runtime_list_json(text)[0].build == "X1"


def test_simctl_missing_required_field_names_the_tool():
    text = json.dumps({"A": {"version": "1.0", "sizeBytes": 10}})  # no build
    with pytest.raises(Refusal) as ei:
        parse_runtime_list_json(text)
    assert ei.value.code == "E_PARSE_SIMCTL"
    assert "build" in str(ei.value)


def test_simctl_device_availability_split():
    text = json.dumps(
        {
            "devices": {
                "iOS-17-0": [{"isAvailable": False}, {"isAvailable": False}],
                "iOS-26-3": [{"isAvailable": True}],
            }
        }
    )
    assert parse_device_availability(text) == (1, 2)


def test_simctl_output_is_treated_as_data_not_instruction():
    """Hostile output: a device entry naming a path that is a symlink to /. The parser must
    return it as inert data and never act on it -- containment is a separate gate, and the
    parser's job is to not become a vector."""
    text = json.dumps(
        {"devices": {"iOS": [{"isAvailable": True, "dataPath": "/", "udid": "../../.."}]}}
    )
    assert parse_device_availability(text) == (1, 0)
