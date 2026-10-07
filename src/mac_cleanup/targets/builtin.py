"""The frozen target set.

Deliberately a literal tuple rather than discovery. An earlier design generated targets at
runtime by promoting any large child of ``~/Library/Caches``, which on this machine means
167 candidates including ``CloudKit``, ``FamilyCircle`` and ``com.apple.appleaccountd`` --
live account and sync state, reachable by a tool whose selling point is not destroying
things. Worse, a generated target has no reviewed risk label, so "the risk set is a
reviewable artifact" would have been false for exactly the entries nobody reviewed.

Runtime discovery returns in v0.2, where generated targets are born unreviewed: never in a
profile, never selectable by ``--yes``, and rendered as such.

Sizes in comments are from ``results/baseline.json`` (2026-10-07, MacBookPro15,1, macOS
15.8) and are context for the reader, not values the code relies on.
"""

from __future__ import annotations

from mac_cleanup.targets.model import (
    REBUILDABLE,
    REPORT_ONLY,
    DelegatedTarget,
    PathTarget,
    Profile,
)

GIB = 1024**3

PATH_TARGETS: tuple[PathTarget, ...] = (
    PathTarget(
        target_id="xcode.derived-data",
        path="~/Library/Developer/Xcode/DerivedData",
        label="Xcode DerivedData",
        group="xcode",
        bundle_ids=("com.apple.dt.Xcode",),
        note="Per-project build output. Xcode rebuilds it.",
    ),
    PathTarget(
        target_id="xcode.module-cache",
        path="~/Library/Developer/Xcode/DerivedData/ModuleCache.noindex",
        label="Xcode clang module cache (shared by every project)",
        group="xcode",
        risk=REBUILDABLE,
        bundle_ids=("com.apple.dt.Xcode",),
        non_default=True,
        note=(
            "1.38 GiB of DerivedData's 3.26 GiB when measured, and NOT per-project: "
            "removing it forces a clean module rebuild across all projects. Labelled "
            "separately because 'DerivedData rebuilds automatically' understates this."
        ),
    ),
    PathTarget(
        target_id="xcode.device-support",
        path="~/Library/Developer/Xcode/iOS DeviceSupport",
        label="Xcode iOS DeviceSupport",
        group="xcode",
        risk=REBUILDABLE,
        non_default=True,
        note=(
            "Re-downloaded next time each device is attached. Both entries measured here "
            "were current-generation iOS 26.x, so there were no 'old versions' to drop -- "
            "which is why this is non-default rather than a free win."
        ),
    ),
    PathTarget(
        target_id="pnpm.store-orphan",
        path="~/Library/pnpm/store/v3",
        label="pnpm store, orphaned major version",
        group="node",
        note=(
            "`pnpm store path` points at v10, so `pnpm store prune` never touches v3 and no "
            "vendor GC will ever reclaim it. Note the store uses APFS clones, so du "
            "overstates what removal frees -- measured 5.855 GiB claimed, 1.734 GiB freed."
        ),
    ),
    PathTarget(
        target_id="npm.cacache",
        path="~/.npm/_cacache",
        label="npm content cache",
        group="node",
        note="Re-downloaded on demand.",
    ),
    PathTarget(
        target_id="gradle.caches",
        path="~/.gradle/caches",
        label="Gradle caches",
        group="jvm",
        bundle_ids=("com.jetbrains.intellij",),
        note="Re-downloaded and rebuilt on next build.",
    ),
    PathTarget(
        target_id="cache.ms-playwright",
        path="~/Library/Caches/ms-playwright",
        label="Playwright browser downloads",
        group="web",
        note="Re-downloaded by `playwright install`.",
    ),
    PathTarget(
        target_id="cache.pypoetry",
        path="~/Library/Caches/pypoetry",
        label="Poetry cache",
        group="python",
    ),
    PathTarget(
        target_id="cache.pip",
        path="~/Library/Caches/pip",
        label="pip wheel cache",
        group="python",
    ),
    PathTarget(
        target_id="cache.jetbrains",
        path="~/Library/Caches/JetBrains",
        label="JetBrains IDE caches",
        group="jvm",
        bundle_ids=(
            "com.jetbrains.intellij",
            "com.jetbrains.pycharm",
            "com.jetbrains.WebStorm",
            "com.jetbrains.goland",
            "com.jetbrains.datagrip",
        ),
        note="Indexes rebuild on next open, which takes a while on a large project.",
    ),
    PathTarget(
        target_id="cache.google",
        path="~/Library/Caches/Google",
        label="Google / Chrome caches",
        group="web",
        bundle_ids=("com.google.Chrome",),
        note="Blocked while Chrome runs: clearing a live browser cache corrupts it.",
    ),
    PathTarget(
        target_id="cache.updaters",
        path="~/Library/Caches/com.anthropic.claudefordesktop.ShipIt",
        label="Claude desktop updater cache",
        group="updaters",
        note="Downloaded installer payloads; re-fetched if an update is needed.",
    ),
)

DELEGATED_TARGETS: tuple[DelegatedTarget, ...] = (
    DelegatedTarget(
        target_id="brew.cleanup",
        label="Homebrew cleanup (vendor GC)",
        tool="brew",
        plan_argv=("brew", "cleanup", "-n"),
        apply_argv=("brew", "cleanup"),
        group="brew",
        note=(
            "Unlinks superseded Cellar kegs. On this machine /usr/local/opt holds 560 "
            "symlinks into Cellar across 391 live formulae, so an interruption mid-write "
            "leaves dangling links that need `brew reinstall` -- hence the free-space floor."
        ),
    ),
    DelegatedTarget(
        target_id="npm.cache-clean",
        label="npm cache clean (vendor GC)",
        tool="npm",
        plan_argv=("npm", "cache", "verify"),
        apply_argv=("npm", "cache", "clean", "--force"),
        group="node",
        min_free_bytes=512 * 1024**2,
    ),
    DelegatedTarget(
        target_id="simctl.runtimes",
        label="Unused simulator runtimes",
        tool="xcrun",
        plan_argv=("xcrun", "simctl", "runtime", "list", "-j"),
        apply_argv=("xcrun", "simctl", "runtime", "delete"),
        verify_argv=("xcrun", "simctl", "runtime", "list", "-j"),
        risk=REBUILDABLE,
        group="xcode",
        note=(
            "Runtimes are mounted read-only APFS volumes, NOT directories: `rm -rf` fails "
            "EPERM on their root-owned parent. Only `simctl runtime delete` unmounts and "
            "releases the volume. Verified after applying, because simctl can update its "
            "bookkeeping while the volume stays mounted."
        ),
    ),
    DelegatedTarget(
        target_id="simctl.unavailable-devices",
        label="Unavailable simulator devices",
        tool="xcrun",
        plan_argv=("xcrun", "simctl", "list", "devices", "-j"),
        apply_argv=("xcrun", "simctl", "delete", "unavailable"),
        group="xcode",
        min_free_bytes=256 * 1024**2,
        note=(
            "Measured 3.61 GiB here, not the 10.86 GiB of the whole Devices directory -- "
            "the rest is live device state that this command does not touch."
        ),
    ),
)

REPORT_ONLY_TARGETS: tuple[PathTarget, ...] = (
    PathTarget(
        target_id="ios.backups",
        path="~/Library/Application Support/MobileSync/Backup",
        label="iOS device backups",
        risk=REPORT_ONLY,
        group="ios",
        contents_only=False,
        non_default=True,
        note=(
            "Reported, never deleted by this tool. Whether a backup is redundant depends on "
            "facts it cannot observe -- iCloud Backup state, whether the device still "
            "exists, whether a copy lives elsewhere -- and a typed confirmation tests only "
            "whether the user can type. Delete it in Finder > Manage Backups."
        ),
    ),
    PathTarget(
        target_id="icloud.local-copies",
        path="~/Library/Mobile Documents/com~apple~CloudDocs",
        label="iCloud Drive local copies",
        risk=REPORT_ONLY,
        group="cloud",
        contents_only=False,
        non_default=True,
        note=(
            "Reported, never deleted: removing a local copy of a synced file can propagate "
            "off-machine. The safe reclaim is eviction (content stays in iCloud), which is "
            "a Finder action. Note that evicted files report st_blocks == 0, so a "
            "blocks-based size understates what is really stored here."
        ),
    ),
)

PROFILES: tuple[Profile, ...] = (
    Profile(
        name="safe",
        version=1,
        description="Regenerable caches and vendor GC. Nothing a running app owns.",
        target_ids=(
            "xcode.derived-data",
            "pnpm.store-orphan",
            "npm.cacache",
            "cache.ms-playwright",
            "cache.pypoetry",
            "cache.pip",
            "cache.updaters",
            "brew.cleanup",
            "npm.cache-clean",
            "simctl.unavailable-devices",
        ),
    ),
    Profile(
        name="dev",
        version=1,
        description="safe, plus developer caches that cost a rebuild or a download.",
        target_ids=(
            "xcode.derived-data",
            "pnpm.store-orphan",
            "npm.cacache",
            "cache.ms-playwright",
            "cache.pypoetry",
            "cache.pip",
            "cache.updaters",
            "cache.jetbrains",
            "cache.google",
            "gradle.caches",
            "brew.cleanup",
            "npm.cache-clean",
            "simctl.unavailable-devices",
            "simctl.runtimes",
        ),
    ),
)


def all_targets() -> tuple[object, ...]:
    return (*PATH_TARGETS, *DELEGATED_TARGETS, *REPORT_ONLY_TARGETS)


def by_id() -> dict[str, object]:
    return {t.target_id: t for t in all_targets()}  # type: ignore[attr-defined]


def profile(name: str) -> Profile | None:
    return next((p for p in PROFILES if p.name == name), None)
