"""Run reporting, and the path-free artifact schema.

Three numbers, never summed
---------------------------
``freed`` is what the walk accounted for. ``disk_delta`` is what ``statvfs`` observed. They
disagree routinely and in both directions, and the gap is informative rather than
embarrassing -- Phase 0 measured ``du`` claiming 5.855 GiB for a pnpm subtree that released
1.734 GiB (APFS clones), and ``simctl`` under-reporting a runtime by 1.0 GiB (deleting it
also released the volume's container allocation). Printing one number would have hidden both.

Artifacts contain no paths
--------------------------
Committed measurements are published, and this machine's iCloud Documents hold court
filings, a garnishment order and a lease. A hash-based denylist cannot catch a filename it
was never given a hash for, so the schema is made structurally incapable of carrying one and
:func:`assert_path_free` is run in CI.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from mac_cleanup.paths import home as real_home
from mac_cleanup.render import human_bytes

_ABSOLUTE = re.compile(r"^/")


@dataclass
class TargetOutcome:
    target_id: str
    status: str
    freed_bytes: int = 0
    linked_bytes: int = 0
    files: int = 0
    errors: int = 0
    error_class: str = ""
    freed_source: str = "accounted_walk"
    """One of ``accounted_walk``, ``tool_report``, ``watch_path_delta``. Never mixed."""

    note: str = ""


@dataclass
class RunReport:
    outcomes: list[TargetOutcome] = field(default_factory=list)
    free_before: int = 0
    free_after: int = 0
    snapshots: tuple[str, ...] = ()
    dry_run: bool = False

    @property
    def accounted(self) -> int:
        return sum(o.freed_bytes for o in self.outcomes)

    @property
    def disk_delta(self) -> int:
        return self.free_after - self.free_before

    @property
    def unaccounted(self) -> int:
        return self.accounted - self.disk_delta

    def exit_code(self) -> int:
        """One code per meaning, so a wrapper can act without parsing prose."""
        if any(o.status == "failed" for o in self.outcomes):
            return 3
        if any(o.status == "partial" for o in self.outcomes):
            return 2
        if not self.outcomes:
            return 4
        return 0

    def human(self) -> str:
        if self.dry_run:
            # No statvfs line in a dry run. Nothing was removed, so any movement in free
            # space is other processes, and printing it as a result would be a lie of
            # exactly the kind this tool exists to stop.
            lines = [
                f"Would free:         {human_bytes(self.accounted):>12}"
                f"  across {len(self.outcomes)} targets (upper bound)",
            ]
        else:
            lines = [
                f"Freed (accounted):  {human_bytes(self.accounted):>12}"
                f"  across {len(self.outcomes)} targets",
                f"Disk free delta:    {human_bytes(max(self.disk_delta, 0)):>12}"
                "  (statvfs before/after)",
            ]
        gap = 0 if self.dry_run else self.unaccounted
        if gap > 0:
            why = []
            if self.snapshots:
                why.append(f"{len(self.snapshots)} APFS local snapshots are holding space")
            why.append("copy-on-write clones share extents and are invisible to stat")
            lines.append(f"Unaccounted:        {human_bytes(gap):>12}  -- {'; '.join(why)}")
        linked = sum(o.linked_bytes for o in self.outcomes)
        if linked:
            lines.append(
                f"Links removed:      {human_bytes(linked):>12}"
                "  (other names remain; no space released)"
            )
        for o in self.outcomes:
            if o.status not in ("ok", "nothing_to_do"):
                detail = f"{o.errors} error(s)" + (f", {o.error_class}" if o.error_class else "")
                lines.append(
                    f"  {o.target_id:<28} {human_bytes(o.freed_bytes):>12} freed, "
                    f"{o.status.upper()}: {detail}"
                )
        return "\n".join(lines)

    def to_artifact(self) -> dict[str, Any]:
        """A path-free record. See :func:`assert_path_free`."""
        return {
            "schema": "mac-cleanup/run/1",
            "dry_run": self.dry_run,
            "accounted_bytes": self.accounted,
            # Null rather than 0 in a dry run: the number is not meaningful, and a 0 would
            # read as "measured no change".
            "disk_delta_bytes": None if self.dry_run else self.disk_delta,
            "unaccounted_bytes": None if self.dry_run else self.unaccounted,
            "local_snapshot_count": len(self.snapshots),
            "targets": [
                {
                    "target_id": o.target_id,
                    "status": o.status,
                    "freed_bytes": o.freed_bytes,
                    "linked_bytes": o.linked_bytes,
                    "files": o.files,
                    "errors": o.errors,
                    "error_class": o.error_class,
                    "freed_source": o.freed_source,
                }
                for o in self.outcomes
            ],
        }


def assert_path_free(obj: Any, *, home: str | None = None) -> None:
    """Raise if any string in ``obj`` looks like a path or names the user's home.

    Run in CI over everything under ``results/``. Mechanism, not habit: the serializer
    refuses rather than relying on a reviewer to notice.
    """
    needle = os.path.basename(home or real_home())
    offenders: list[str] = []

    def walk(node: Any, where: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{where}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{where}[{i}]")
        elif isinstance(node, str) and (_ABSOLUTE.match(node) or (needle and needle in node)):
            offenders.append(f"{where}={node!r}")

    walk(obj, "$")
    if offenders:
        raise ValueError("artifact contains path-like values: " + "; ".join(offenders))


def dump_artifact(obj: Any, path: str, *, home: str | None = None) -> None:
    assert_path_free(obj, home=home)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
        fh.write("\n")
