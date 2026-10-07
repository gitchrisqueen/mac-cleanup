"""Runner, budget, cancel and paths. Small modules, but the deletion path depends on all
four, so they are covered rather than assumed."""

from __future__ import annotations

import os
import sys

import pytest

from mac_cleanup.errors import Refusal, classify_oserror
from mac_cleanup.ext.runner import Runner
from mac_cleanup.fs.stat import RealFs
from mac_cleanup.paths import expand, home, resolve_parent, state_dir
from mac_cleanup.scan.budget import Budget, BudgetFlag, exceeded
from mac_cleanup.scan.cancel import CancelToken

# ----------------------------------------------------------------------------- runner


def test_runner_reports_a_missing_tool_without_raising():
    result = Runner().run(["definitely-not-a-real-binary-xyz"])
    assert result.not_found is True
    assert result.ok is False
    assert result.returncode == 127


def test_runner_captures_a_nonzero_exit():
    result = Runner().run([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert result.returncode == 3
    assert result.ok is False


def test_runner_captures_stdout_and_stderr_separately():
    result = Runner().run(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"]
    )
    assert result.ok is True
    assert "out" in result.stdout
    assert "err" in result.stderr


def test_runner_times_out_rather_than_hanging():
    result = Runner(timeout=0.2).run([sys.executable, "-c", "import time; time.sleep(5)"])
    assert result.timed_out is True
    assert result.ok is False


def test_runner_never_involves_a_shell():
    """argv is a list, so shell metacharacters are inert data."""
    result = Runner().run([sys.executable, "-c", "print('safe')", ";", "echo", "pwned"])
    assert "pwned" not in result.stdout


def test_runner_truncates_enormous_output():
    result = Runner(max_output=50).run([sys.executable, "-c", "print('x' * 10000)"])
    assert len(result.stdout) <= 50


def test_runner_which_finds_a_real_binary_and_not_a_fake_one():
    r = Runner()
    assert r.which("sh") is not None
    assert r.which("definitely-not-a-real-binary-xyz") is None


# ----------------------------------------------------------------------------- budget


def test_budget_time_and_inode_ceilings():
    b = Budget(seconds=5.0, inodes=100)
    assert exceeded(b, elapsed=1.0, inodes=10) is False
    assert exceeded(b, elapsed=5.0, inodes=10) is True
    assert exceeded(b, elapsed=1.0, inodes=100) is True


def test_an_unlimited_budget_is_never_exceeded():
    b = Budget.unlimited()
    assert b.is_unlimited is True
    assert exceeded(b, elapsed=1e9, inodes=10**9) is False


def test_a_partial_budget_checks_only_what_is_set():
    assert exceeded(Budget(seconds=None, inodes=5), elapsed=1e9, inodes=1) is False
    assert exceeded(Budget(seconds=1.0, inodes=None), elapsed=2.0, inodes=10**9) is True


def test_budget_flag_is_one_way():
    flag = BudgetFlag()
    assert flag.blown is False
    flag.blow()
    assert flag.blown is True
    flag.blow()
    assert flag.blown is True


# ----------------------------------------------------------------------------- cancel


def test_cancel_token_starts_unset_and_latches():
    token = CancelToken()
    assert token.cancelled is False
    assert bool(token) is False
    token.cancel()
    assert token.cancelled is True
    assert bool(token) is True


def test_cancelling_notifies_parked_workers():
    """Without the notify, daemon workers stay in wait() and a join never completes -- so
    'a cancelled scan returns a partial result' would be unachievable."""
    import threading

    token = CancelToken()
    cv = threading.Condition()
    token.wake = cv
    woke = threading.Event()

    def parked():
        with cv:
            cv.wait(5)
        woke.set()

    t = threading.Thread(target=parked, daemon=True)
    t.start()
    token.cancel()
    t.join(timeout=2)
    assert woke.is_set(), "cancel must wake a parked worker"


# ------------------------------------------------------------------------------ paths


def test_home_comes_from_the_password_database_not_the_environment(monkeypatch):
    real = home()
    monkeypatch.setenv("HOME", "/tmp/spoofed")
    assert home() == real, "$HOME is spoofable; the password database is not"


def test_expand_handles_tilde_forms():
    assert expand("~") == home()
    assert expand("~/x") == os.path.join(home(), "x")
    assert expand("/absolute") == "/absolute"
    assert expand("relative") == "relative"


def test_resolve_parent_keeps_the_leaf_lexical(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert resolve_parent(str(link)) == str(link), "the leaf must not be dereferenced"


def test_resolve_parent_resolves_a_symlinked_parent(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "leaf").write_text("x")
    link = tmp_path / "link"
    link.symlink_to(real)
    assert resolve_parent(str(link / "leaf")) == str(real / "leaf")


@pytest.mark.parametrize(
    ("bad", "code"),
    [("", "E_MALFORMED"), ("/a\0b", "E_MALFORMED"), ("rel/x", "E_RELATIVE"), ("/", "E_MALFORMED")],
)
def test_resolve_parent_refusals(bad, code):
    with pytest.raises(Refusal) as ei:
        resolve_parent(bad)
    assert ei.value.code == code


def test_state_dir_honours_xdg_but_stays_namespaced(monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", "/tmp/xdg")
    assert state_dir() == "/tmp/xdg/mac-cleanup"
    monkeypatch.delenv("XDG_STATE_HOME")
    assert state_dir().endswith("/.local/state/mac-cleanup")


def test_state_dir_ignores_a_relative_xdg_value(monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", "not-absolute")
    assert state_dir().endswith("/.local/state/mac-cleanup")


# ------------------------------------------------------------------------------- misc


def test_errno_classification_table():
    import errno

    assert classify_oserror(OSError(errno.EACCES, "x")) == "permission"
    assert classify_oserror(OSError(errno.EPERM, "x")) == "permission"
    assert classify_oserror(OSError(errno.EROFS, "x")) == "readonly"
    assert classify_oserror(OSError(errno.ENOENT, "x")) == "raced"
    assert classify_oserror(OSError(errno.EBUSY, "x")) == "busy"
    assert classify_oserror(OSError(errno.ENOTEMPTY, "x")) == "busy"
    assert classify_oserror(OSError(9999, "x")) == "other"


def test_permission_and_readonly_are_distinct():
    """Conflated during planning: a mounted simulator runtime fails EPERM on its root-owned
    parent, not EROFS, and naming the wrong one sends the user to the wrong fix."""
    import errno

    assert classify_oserror(OSError(errno.EPERM, "x")) != classify_oserror(
        OSError(errno.EROFS, "x")
    )


def test_realfs_implements_the_stat_seam(tmp_path):
    (tmp_path / "f").write_text("x")
    fs = RealFs()
    assert fs.lexists(str(tmp_path / "f")) is True
    assert fs.lexists(str(tmp_path / "nope")) is False
    assert fs.lstat(str(tmp_path / "f")).st_size == 1
    assert [e.name for e in fs.scandir(str(tmp_path))] == ["f"]


def test_refusal_message_includes_code_and_detail():
    exc = Refusal("E_THING", "/some/path", "because reasons")
    assert "E_THING" in str(exc) and "because reasons" in str(exc)
    assert Refusal("E_BARE").code == "E_BARE"
