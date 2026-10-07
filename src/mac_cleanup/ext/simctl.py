"""``xcrun simctl`` output parsing. Pure functions over text/JSON.

Simulator runtimes are **mounted read-only APFS volumes**, not directories, so the only
correct removal is ``simctl runtime delete``. ``rm -rf`` on one fails EPERM on its
root-owned parent and the predecessor script printed "Removed." anyway.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from mac_cleanup.errors import Refusal


@dataclass(frozen=True)
class Runtime:
    build: str
    version: str
    platform: str
    size_bytes: int
    state: str


def parse_runtime_list_json(text: str) -> list[Runtime]:
    """Parse ``simctl runtime list -j``.

    Note ``simctl`` reports every runtime as ``Ready`` and exposes no staleness field --
    which build is current is a judgement from version numbers, not tool output.
    """
    if not text.strip():
        return []
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Refusal("E_PARSE_SIMCTL", "", f"runtime list is not JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise Refusal("E_PARSE_SIMCTL", "", "runtime list was not a JSON object")

    out: list[Runtime] = []
    for entry in raw.values():
        if not isinstance(entry, dict):
            continue
        try:
            out.append(
                Runtime(
                    build=str(entry["build"]),
                    version=str(entry.get("version", "")),
                    platform=str(entry.get("platformIdentifier", "")).rsplit(".", 1)[-1],
                    size_bytes=int(entry.get("sizeBytes", 0)),
                    state=str(entry.get("state", "")),
                )
            )
        except KeyError as exc:
            raise Refusal("E_PARSE_SIMCTL", "", f"runtime entry missing {exc}") from exc
    return out


def parse_device_availability(text: str) -> tuple[int, int]:
    """``(available, unavailable)`` device counts from ``simctl list devices -j``."""
    if not text.strip():
        return (0, 0)
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Refusal("E_PARSE_SIMCTL", "", f"device list is not JSON: {exc}") from exc
    available = unavailable = 0
    for devices in (raw.get("devices") or {}).values():
        for dev in devices or []:
            if dev.get("isAvailable"):
                available += 1
            else:
                unavailable += 1
    return (available, unavailable)
