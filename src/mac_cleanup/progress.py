"""Live progress.

One rule governs the whole module: **all progress and human chrome goes to stderr; stdout
carries only the final payload.** JSON mode is therefore uncorruptible by construction
rather than by remembering to suppress things, and `mac-cleanup size --json | jq` still
shows progress on the terminal.

This is the direct fix for the predecessor's silent audit, which wrapped its entire body in
``{ … } > "$REPORT_FILE"`` and so printed nothing for the duration. A long operation that
prints nothing is indistinguishable from a hang, and that was the original complaint.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from typing import TYPE_CHECKING

from mac_cleanup.render import human_bytes, truncate_middle

if TYPE_CHECKING:
    from typing import TextIO


class ProgressSink:
    """Base sink. Only the pump thread calls these, so there is exactly one writer."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream: TextIO = stream if stream is not None else sys.stderr
        self.started = time.monotonic()
        self._last = 0.0

    def update(
        self, *, bytes_done: int = 0, files: int = 0, pending: int = 0, current: str = ""
    ) -> None:
        """Called at the pump's cadence. Implementations rate-limit themselves."""

    def finish(self, summary: str = "") -> None:
        """Called once, after the work completes."""


class NullProgress(ProgressSink):
    """Silent. Used for --quiet, and for JSON output into a pipe."""


class PlainProgress(ProgressSink):
    """Non-TTY: a newline-terminated line every few seconds.

    No carriage returns and no ANSI, so it is readable in a pipe, a log file or CI output.
    """

    INTERVAL = 5.0

    def update(
        self, *, bytes_done: int = 0, files: int = 0, pending: int = 0, current: str = ""
    ) -> None:
        now = time.monotonic()
        if now - self._last < self.INTERVAL:
            return
        self._last = now
        where = f"  {truncate_middle(current, 50)}" if current else ""
        print(
            f"scan  {human_bytes(bytes_done)}  {files:,} files  "
            f"{pending} pending  {int(now - self.started)}s{where}",
            file=self.stream,
            flush=True,
        )

    def finish(self, summary: str = "") -> None:
        if summary:
            print(summary, file=self.stream, flush=True)


class TTYProgress(ProgressSink):
    """Two lines repainted in place at 10 Hz.

    Uses ``\\r`` plus erase-to-end-of-line and never clears the screen or enters the
    alternate buffer, so completed output stays in scrollback. For a tool that deletes
    things, being able to scroll back and re-read what it said is part of the safety story.
    """

    INTERVAL = 0.1

    def update(
        self, *, bytes_done: int = 0, files: int = 0, pending: int = 0, current: str = ""
    ) -> None:
        now = time.monotonic()
        if now - self._last < self.INTERVAL:
            return
        self._last = now
        width = max(20, shutil.get_terminal_size((80, 24)).columns)
        head = (
            f"scan  {human_bytes(bytes_done)}  {files:,} files  "
            f"{pending} pending  {int(now - self.started)}s"
        )
        tail = truncate_middle(current, width - 2) if current else ""
        self.stream.write(f"\r\x1b[K{head[:width]}\n\r\x1b[K{tail}\x1b[1A")
        self.stream.flush()

    def finish(self, summary: str = "") -> None:
        # Clear both lines and leave the cursor on a clean row.
        self.stream.write("\r\x1b[K\n\r\x1b[K")
        self.stream.flush()
        if summary:
            print(summary, file=self.stream, flush=True)


def make_sink(
    *, json_mode: bool = False, quiet: bool = False, stream: TextIO | None = None
) -> ProgressSink:
    """Pick a sink from the environment.

    TTY detection selects the *renderer* only. It never decides anything about safety --
    that lesson came from ``--yes``, where treating a tty as a human would have let
    ``tmux new-session -d`` run unattended without ``--apply``.
    """
    out: TextIO = stream if stream is not None else sys.stderr
    if quiet:
        return NullProgress(out)
    try:
        tty = out.isatty()
    except (AttributeError, ValueError):
        tty = False
    if tty and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb":
        return TTYProgress(out)
    if json_mode and not tty:
        return NullProgress(out)
    return PlainProgress(out)
