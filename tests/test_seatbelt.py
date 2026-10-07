"""The seatbelt is itself tested. A guard that has only been configured is a claim."""

from __future__ import annotations

import os
import shutil

import pytest


def test_removal_outside_the_sandbox_is_blocked(tmp_path):
    """The guarantee that matters: nothing in a real home directory can be touched."""
    outside = os.path.expanduser("~/.mac-cleanup-seatbelt-canary")
    with pytest.raises(AssertionError, match=r"without @pytest\.mark\.destructive"):
        os.remove(outside)
    assert not os.path.exists(outside), "the canary must never have been created either"


def test_removal_inside_the_sandbox_is_allowed(tmp_path):
    victim = tmp_path / "f"
    victim.write_text("x")
    os.remove(victim)
    assert not victim.exists()


def test_os_system_is_never_permitted():
    with pytest.raises(AssertionError, match=r"os\.system is never permitted"):
        os.system("true")


@pytest.mark.destructive
def test_destructive_tests_may_delete_inside_their_own_tmp_path(tmp_path):
    victim = tmp_path / "f"
    victim.write_text("x")
    os.remove(victim)
    assert not victim.exists()


@pytest.mark.destructive
def test_destructive_tests_cannot_escape_their_own_tmp_path(tmp_path):
    with pytest.raises(AssertionError, match=r"escaped tmp_path"):
        shutil.rmtree(tmp_path.parent)
    assert tmp_path.parent.exists()
