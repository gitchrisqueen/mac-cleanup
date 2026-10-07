"""Per-target scan budgets.

The policy lives in one place (the pump evaluates it) but **enforcement is a flag workers
read**, once per directory pop. The alternative -- removing a blown subtree's entries from
a contended work stack by predicate -- is both the slowest option and unable to stop workers
already inside that subtree.

A budgeted result is a lower bound and must be rendered as ``>= X``, never as a bare number
and never with an invented coverage percentage: directory counts do not translate to byte
counts.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class Budget:
    """Time and inode ceilings for one target. ``None`` means unlimited."""

    seconds: float | None = 8.0
    inodes: int | None = 400_000

    @staticmethod
    def unlimited() -> Budget:
        return Budget(seconds=None, inodes=None)

    @property
    def is_unlimited(self) -> bool:
        return self.seconds is None and self.inodes is None


class BudgetFlag:
    """A one-way flag the pump sets and workers read."""

    __slots__ = ("_blown",)

    def __init__(self) -> None:
        self._blown = threading.Event()

    def blow(self) -> None:
        self._blown.set()

    @property
    def blown(self) -> bool:
        return self._blown.is_set()


def exceeded(budget: Budget, *, elapsed: float, inodes: int) -> bool:
    """Whether a budget has been spent. Pure, so the pump stays trivial to test."""
    if budget.seconds is not None and elapsed >= budget.seconds:
        return True
    return budget.inodes is not None and inodes >= budget.inodes
