"""Human-facing formatting.

The rendering rules are a safety surface, not decoration. The predecessor displayed ``0 B``
for four mounted runtime volumes it could not read, and ``0 B`` reads as "nothing there,
safe to delete" -- which steered the user toward the one action that could not work. So:

* a bare figure is only ever shown for ``EXACT``;
* a budgeted result shows ``>=``, never a bare number and never an invented percentage;
* an unmeasurable target shows ``?`` **with the reason**, never ``0 B``;
* an evicted cloud tree is labelled, because its local blocks genuinely are near zero.
"""

from __future__ import annotations

from mac_cleanup.scan.sizes import DATALESS, EXACT, PARTIAL, TOOL, UNKNOWN

_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")


def human_bytes(n: int) -> str:
    """Binary units, because that is what ``st_blocks * 512`` counts."""
    if n < 1024:
        return f"{n} B"
    value = float(n)
    for unit in _UNITS[1:]:
        value /= 1024.0
        if value < 1024.0:
            return f"{value:.2f} {unit}"
    return f"{value:.2f} {_UNITS[-1]}"


def size_cell(n: int, quality: str, *, reason: str = "", age_s: float | None = None) -> str:
    """One size cell, with its honesty markers."""
    if quality == UNKNOWN:
        return f"?  ({reason})" if reason else "?"
    if quality == TOOL:
        return "see details"
    if quality == PARTIAL:
        return f">= {human_bytes(n)} (partial)"
    if quality == DATALESS:
        return f"{human_bytes(n)} local (evicted content not counted)"
    text = human_bytes(n)
    if quality == EXACT and age_s is not None and age_s > 3600:
        return f"{text} ·{int(age_s // 3600)}h"
    return text


def truncate_middle(text: str, width: int) -> str:
    """Keep both ends of a path visible; the distinctive part is usually the tail."""
    if width <= 1 or len(text) <= width:
        return text
    if width <= 4:
        return text[:width]
    keep = width - 1
    head = keep // 2
    tail = keep - head
    return f"{text[:head]}…{text[len(text) - tail :]}"


def plural(n: int, singular: str, plural_form: str | None = None) -> str:
    return f"{n} {singular}" if n == 1 else f"{n} {plural_form or singular + 's'}"
