"""The only module permitted to import ``subprocess``.

Enforced by ruff ``flake8-tidy-imports.banned-api`` plus a grep backstop, because ruff
resolves bans through imports and cannot see ``getattr`` tricks or bound-method aliases.

Two consequences, both deliberate:

* Every macOS tool parser elsewhere is a pure function over ``bytes``, so the whole suite
  runs on Linux and only the *invocation* is faked.
* Delegated cleaners run with ``start_new_session=True`` and the signal is never forwarded,
  so Ctrl-C cannot kill ``brew`` or ``simctl`` midway through rewriting a Cellar keg. That
  makes them uninterruptible, which is a real cost: the caller must therefore warn before
  starting one, and on interruption mark the item ``unknown`` and re-verify by observation
  rather than assuming either outcome.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    not_found: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.not_found


class Runner:
    """Runs external commands. No shell, ever: argv is passed as a list."""

    # Probes are short because a dead daemon makes a client wait forever, not fail. Docker
    # is the case that proves it: with the VM gone, `docker system df` and `docker info`
    # block indefinitely on a socket nobody is listening to, and the predecessor script
    # (line 338) called it with no timeout at all -- so its Docker menu hung with no output
    # and no way to tell whether it was working. Actions get longer, but still finite.
    PROBE_TIMEOUT = 5.0
    ACTION_TIMEOUT = 120.0

    def __init__(
        self,
        *,
        timeout: float = ACTION_TIMEOUT,
        probe_timeout: float = PROBE_TIMEOUT,
        max_output: int = 4_000_000,
    ) -> None:
        self.timeout = timeout
        self.probe_timeout = probe_timeout
        self.max_output = max_output

    def probe(self, argv: list[str]) -> CommandResult:
        """Run a read-only query under the short timeout. Never blocks a render."""
        return self.run(argv, timeout=self.probe_timeout)

    def which(self, name: str) -> str | None:
        return shutil.which(name)

    def run(self, argv: list[str], *, timeout: float | None = None) -> CommandResult:
        """Run ``argv`` and capture output. Never raises for a non-zero exit."""
        tup = tuple(argv)
        try:
            proc = subprocess.run(  # noqa: S603 - argv is a list; no shell is involved
                argv,
                capture_output=True,
                text=True,
                timeout=timeout if timeout is not None else self.timeout,
                check=False,
                start_new_session=True,
            )
        except FileNotFoundError:
            return CommandResult(tup, 127, "", f"{argv[0]}: not found", not_found=True)
        except subprocess.TimeoutExpired:
            return CommandResult(tup, 124, "", f"{argv[0]}: timed out", timed_out=True)
        return CommandResult(
            tup,
            proc.returncode,
            (proc.stdout or "")[: self.max_output],
            (proc.stderr or "")[: self.max_output],
        )
