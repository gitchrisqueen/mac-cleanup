"""Orchestration: what the CLI calls.

Kept separate from ``cli.py`` so argument handling stays about arguments, and so the
behaviour is testable without constructing an argv.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from mac_cleanup.errors import Refusal
from mac_cleanup.ext.runner import Runner
from mac_cleanup.forest import build_forest
from mac_cleanup.fs.deleter import Deleter
from mac_cleanup.paths import resolve_parent
from mac_cleanup.progress import ProgressSink
from mac_cleanup.report import RunReport, TargetOutcome
from mac_cleanup.scan.cache import TTL_COLD, TTL_NORMAL, SizeCache
from mac_cleanup.scan.cancel import CancelToken
from mac_cleanup.scan.sizes import EXACT, TOOL, UNKNOWN, SizeResult, resolve_quality
from mac_cleanup.scan.walker import Walker
from mac_cleanup.targets.builtin import all_targets, by_id
from mac_cleanup.targets.model import DelegatedTarget, PathTarget


@dataclass
class Row:
    """One line of the user-facing table."""

    target_id: str
    label: str
    risk: str
    group: str
    present: bool
    size: SizeResult
    age_s: float | None = None
    stale: bool = False
    reason: str = ""
    non_default: bool = False


@dataclass
class CleanPlan:
    path_targets: list[PathTarget] = field(default_factory=list)
    delegated: list[DelegatedTarget] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)


def _ttl_for(target: object) -> float:
    gid = getattr(target, "group", "")
    return TTL_COLD if gid in ("ios", "cloud") else TTL_NORMAL


# Paths a read-only walk must never enter, whatever it was pointed at.
#
# Found by running `du /`, which timed out: the argument-level cloud check is not enough,
# because the walk *reaches* cloud roots on its own. `/` descends `/System/Volumes/Data`,
# which is a firmlink to the entire data volume, and from there into iCloud -- so a command
# billed as read-only would make a FileProvider fetch content from the server.
#
# `/.vol` is the volfs inode-lookup filesystem; traversing it is pathological.
ALWAYS_SKIP = (
    "~/Library/Mobile Documents",
    "~/Library/CloudStorage",
    "/.vol",
    "/dev",
    "/System/Volumes",
    "/private/var/vm",
    "/Volumes",
)


def walk_skip_set(*, include_cloud: bool = False) -> frozenset[str]:
    """Absolute paths the walker refuses to enter. Resolved once, per run."""
    out: set[str] = set()
    for raw in ALWAYS_SKIP:
        if (include_cloud and "Documents" in raw) or (include_cloud and "CloudStorage" in raw):
            continue
        p = _expand_user(raw)
        out.add(p)
        real = os.path.realpath(p)
        if real != p:
            out.add(real)
    return frozenset(out)


def _expand_user(p: str) -> str:
    if p.startswith("~/"):
        from mac_cleanup.paths import home

        return os.path.join(home(), p[2:])
    return p


def free_bytes(path: str = "/System/Volumes/Data") -> int:
    try:
        st = os.statvfs(path)
    except OSError:
        return 0
    return st.f_bavail * st.f_frsize


# --------------------------------------------------------------------------- detect


def detect(*, include_absent: bool = False, runner: Runner | None = None) -> list[Row]:
    """Presence and cached sizes for every target. No walking, so this is instant."""
    run = runner or Runner()
    cache = SizeCache()
    cache.load()
    rows: list[Row] = []

    for target in all_targets():
        if isinstance(target, DelegatedTarget):
            found = run.which(target.tool) is not None
            rows.append(
                Row(
                    target_id=target.target_id,
                    label=target.label,
                    risk=target.risk,
                    group=target.group,
                    present=found,
                    size=SizeResult(quality=TOOL if found else UNKNOWN),
                    reason="" if found else f"{target.tool} not installed",
                )
            )
            continue

        try:
            key = resolve_parent(target.path)
        except Refusal as exc:
            rows.append(
                Row(
                    target_id=target.target_id,
                    label=target.label,
                    risk=target.risk,
                    group=target.group,
                    present=False,
                    size=SizeResult(quality=UNKNOWN),
                    reason=exc.code,
                    non_default=target.non_default,
                )
            )
            continue

        if not os.path.lexists(key):
            rows.append(
                Row(
                    target_id=target.target_id,
                    label=target.label,
                    risk=target.risk,
                    group=target.group,
                    present=False,
                    size=SizeResult(quality=UNKNOWN),
                    reason="not present",
                    non_default=target.non_default,
                )
            )
            continue

        size = SizeResult(quality=UNKNOWN)
        age: float | None = None
        stale = False
        try:
            st = os.lstat(key)
            hit = cache.get(key, st, _ttl_for(target))
            if hit is not None:
                size = SizeResult(
                    alloc=hit.alloc,
                    logical=hit.logical,
                    files=hit.files,
                    quality=hit.quality,
                    duration_s=hit.duration_s,
                )
                age, stale = hit.age_s, hit.stale
        except OSError:
            pass

        rows.append(
            Row(
                target_id=target.target_id,
                label=target.label,
                risk=target.risk,
                group=target.group,
                present=True,
                size=size,
                age_s=age,
                stale=stale,
                reason="" if size.quality != UNKNOWN else "not measured yet",
                non_default=target.non_default,
            )
        )

    return rows if include_absent else [r for r in rows if r.present]


# --------------------------------------------------------------------------- measure


def measure(
    target_ids: list[str],
    *,
    workers: int = 8,
    cancel: CancelToken | None = None,
    write_cache: bool = True,
    sink: ProgressSink | None = None,
) -> dict[str, SizeResult]:
    """Walk the given path targets once, sharing one traversal across the forest."""
    registry = by_id()
    pairs: list[tuple[str, str]] = []
    for tid in target_ids:
        target = registry.get(tid)
        if isinstance(target, PathTarget):
            pairs.append((tid, target.path))

    if not pairs:
        return {}

    forest = build_forest(pairs)
    started = time.monotonic()
    result = Walker(forest, workers=workers, cancel=cancel, skip=walk_skip_set(), sink=sink).run()
    if sink is not None:
        sink.finish()
    elapsed = time.monotonic() - started

    cache = SizeCache()
    cache.load()
    out: dict[str, SizeResult] = {}

    for tid, node_id in forest.by_target.items():
        dataless = result.totals.dataless.get(node_id, 0)
        quality = resolve_quality(
            budget_blown=result.pending_dirs > 0,
            cancelled=result.cancelled,
            dataless_files=dataless,
            had_errors=any(e.cls != "raced" for e in result.errors),
        )
        size = SizeResult(
            alloc=result.totals.alloc.get(node_id, 0),
            logical=result.totals.logical.get(node_id, 0),
            files=result.totals.files.get(node_id, 0),
            dirs=result.totals.dirs.get(node_id, 0),
            quality=quality,
            duration_s=elapsed,
            pending_dirs=result.pending_dirs,
            dataless_files=dataless,
            dataless_logical=result.totals.dataless_logical.get(node_id, 0),
            errors=tuple(result.errors),
        )
        out[tid] = size

        if write_cache and quality == EXACT:
            try:
                st = os.lstat(forest.nodes[node_id].key)
                cache.put(
                    forest.nodes[node_id].key,
                    st,
                    alloc=size.alloc,
                    logical=size.logical,
                    files=size.files,
                    quality=quality,
                    duration_s=elapsed,
                )
            except OSError:
                pass

    if write_cache:
        cache.save()
    return out


# -------------------------------------------------------------------------------- du


@dataclass
class DuChild:
    name: str
    path: str
    alloc: int
    files: int
    quality: str


def du(
    path: str,
    *,
    workers: int = 8,
    cross_device: bool = False,
    include_cloud: bool = False,
    sink: ProgressSink | None = None,
) -> list[DuChild]:
    """Sizes of the immediate children of an arbitrary path, largest first.

    Safe to point at anything because it only reads, never descends a symlink, does not
    cross a device boundary unless asked, and refuses to enter the paths in
    :data:`ALWAYS_SKIP` -- which is what stops a walk of ``/`` from reaching iCloud through
    the ``/System/Volumes/Data`` firmlink.
    """
    root = resolve_parent(path) if path not in ("/",) else "/"
    if not os.path.isdir(root):
        raise Refusal("E_NOT_A_DIRECTORY", root)

    skip = walk_skip_set(include_cloud=include_cloud)
    children: list[tuple[str, str]] = []
    loose = 0
    skipped: list[str] = []
    with os.scandir(root) as it:
        for entry in it:
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if entry.is_dir(follow_symlinks=False):
                if entry.path in skip:
                    skipped.append(entry.name)
                    continue
                children.append((entry.name, entry.path))
            else:
                loose += st.st_blocks * 512

    out: list[DuChild] = []
    if children:
        forest = build_forest([(name, child) for name, child in children])
        result = Walker(
            forest, workers=workers, cross_device=cross_device, skip=skip, sink=sink
        ).run()
        if sink is not None:
            sink.finish()
        for name, node_id in forest.by_target.items():
            out.append(
                DuChild(
                    name=name,
                    path=forest.nodes[node_id].key,
                    alloc=result.totals.alloc.get(node_id, 0),
                    files=result.totals.files.get(node_id, 0),
                    quality=EXACT,
                )
            )
    if loose:
        out.append(DuChild(name="(files here)", path=root, alloc=loose, files=0, quality=EXACT))
    for name in skipped:
        out.append(DuChild(name=f"{name} (skipped)", path="", alloc=0, files=0, quality=UNKNOWN))

    out.sort(key=lambda c: c.alloc, reverse=True)
    return out


# ----------------------------------------------------------------------------- clean


def plan(target_ids: list[str], *, runner: Runner | None = None) -> CleanPlan:
    """Split a selection into path work and delegated work, refusing what cannot run."""
    run = runner or Runner()
    registry = by_id()
    result = CleanPlan()

    for tid in target_ids:
        target = registry.get(tid)
        if target is None:
            result.refused.append((tid, "unknown target"))
        elif isinstance(target, DelegatedTarget):
            if run.which(target.tool) is None:
                result.refused.append((tid, f"{target.tool} not installed"))
            elif free_bytes() < target.min_free_bytes:
                result.refused.append((tid, "below the free-space floor for this cleaner"))
            else:
                result.delegated.append(target)
        elif isinstance(target, PathTarget):
            if target.risk == "report-only":
                result.refused.append((tid, "report-only; this tool never deletes it"))
            elif not os.path.lexists(resolve_parent(target.path)):
                result.refused.append((tid, "not present"))
            else:
                result.path_targets.append(target)

    return result


def execute(clean_plan: CleanPlan, *, dry_run: bool, runner: Runner | None = None) -> RunReport:
    """Run the plan. Path work first, then delegated, which is slower and uninterruptible."""
    run = runner or Runner()
    before = free_bytes()
    report = RunReport(free_before=before, dry_run=dry_run)

    # A cleaned target's cached size is now a lie, and `list` renders from cache. Drop the
    # entries we are about to invalidate rather than letting the next render report bytes
    # that are no longer there.
    cache = SizeCache()
    cache.load()
    invalidated = 0

    deleter = Deleter(dry_run=dry_run)
    for target in clean_plan.path_targets:
        root = resolve_parent(target.path)
        result = (
            deleter.delete_contents(root) if target.contents_only else deleter.delete_tree(root)
        )
        if not dry_run:
            try:
                st = os.lstat(root)
                if cache.drop(root, st):
                    invalidated += 1
            except OSError:
                pass
        errors = result.real_errors
        report.outcomes.append(
            TargetOutcome(
                target_id=target.target_id,
                status=result.status,
                freed_bytes=result.freed_bytes,
                linked_bytes=result.linked_bytes,
                files=result.files,
                errors=len(errors),
                error_class=errors[0].cls if errors else "",
                freed_source="accounted_walk",
            )
        )

    for delegated in clean_plan.delegated:
        argv = list(delegated.plan_argv if dry_run else delegated.apply_argv)
        timeout = Runner.PROBE_TIMEOUT if dry_run else Runner.ACTION_TIMEOUT
        outcome = run.run(argv, timeout=timeout)
        # In a dry run the command is only a probe. `npm cache verify` exits non-zero on a
        # cache it considers imperfect, which says nothing about whether cleaning would
        # work -- so a probe's exit status is reported, never promoted to a failure.
        if dry_run:
            status = "ok" if outcome.ok else "nothing_to_do"
            error_count = 0
        else:
            status = "ok" if outcome.ok else "failed"
            error_count = 0 if outcome.ok else 1
        report.outcomes.append(
            TargetOutcome(
                target_id=delegated.target_id,
                status=status,
                freed_bytes=0,
                files=0,
                errors=error_count,
                error_class="" if not error_count else "tool",
                freed_source="tool_report",
                note=" ".join(argv),
            )
        )

    # Delegated cleaners work through paths we do not track, so after any real run the
    # safest thing is to treat every cached size as suspect.
    if not dry_run and clean_plan.delegated:
        invalidated += cache.clear()
    if not dry_run and invalidated:
        cache.save()

    report.free_after = free_bytes()
    return report
