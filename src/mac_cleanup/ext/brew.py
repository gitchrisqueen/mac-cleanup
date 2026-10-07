"""Homebrew output parsing. Pure functions over text.

External tool output is **untrusted input**. A format change must degrade a feature with a
named error, never crash and never silently report zero.
"""

from __future__ import annotations

import re

from mac_cleanup.errors import Refusal

_FREE = re.compile(r"would free (?:up )?approximately ([0-9.]+)\s*([KMGT]?B)", re.IGNORECASE)
_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}


def parse_cleanup_dry_run(text: str) -> int:
    """Bytes ``brew cleanup -n`` says it would free.

    Raises:
        Refusal: ``E_PARSE_BREW`` when the summary line is absent, which means the output
            format changed and the figure must not be guessed.
    """
    m = _FREE.search(text)
    if not m:
        if not text.strip():
            return 0
        raise Refusal("E_PARSE_BREW", "", "no 'would free approximately' line in output")
    value, unit = float(m.group(1)), m.group(2).upper()
    return int(value * _UNITS.get(unit, 1))
