"""Cancellation.

A token, not an exception and not a signal. Signals cannot be delivered to a specific
worker and ``KeyboardInterrupt`` inside a thread is unreliable, so cancellation is explicit
state that workers poll. That also makes it testable without signals: a test sets the token
at a chosen step and asserts the partial result, deterministically.
"""

from __future__ import annotations

import threading


class CancelToken:
    """Cooperative cancellation shared by the pump and every worker."""

    def __init__(self) -> None:
        self._event = threading.Event()
        # Workers park on this condition when the stack is empty, so cancelling must also
        # wake them. Without the notify, daemon threads stay parked and a join never
        # completes -- which would make "a cancelled scan returns a partial result"
        # unachievable.
        self.wake: threading.Condition | None = None

    def cancel(self) -> None:
        self._event.set()
        if self.wake is not None:
            with self.wake:
                self.wake.notify_all()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def __bool__(self) -> bool:
        return self._event.is_set()
