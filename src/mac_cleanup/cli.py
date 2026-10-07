"""Command line surface.

Two decisions worth stating, because the obvious alternatives are worse:

**Dry-run keys on ``--yes``, not on ``isatty``.** A tty is not a human:
``tmux new-session -d 'mac-cleanup --yes'`` has one, so tty-detection would classify an
unattended run as interactive, skip the ``--apply`` requirement, and let ``--yes`` answer
every prompt. Keying on ``--yes`` costs an interactive user nothing, because a human at a
prompt never needs it, and it makes the destructive word mandatory in every unattended
invocation -- visible in a crontab, visible to a reviewer. ``isatty`` only picks the
progress sink.

**Selectors are stable ids, never positional numbers.** The predecessor sorted targets by a
size list it recomputed on every render, so index 7 at display time and index 7 at selection
time could be different rows -- on a tool that calls ``rm -rf``.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from mac_cleanup import __version__, service
from mac_cleanup.errors import Refusal
from mac_cleanup.paths import home, state_dir
from mac_cleanup.progress import make_sink
from mac_cleanup.render import human_bytes, size_cell, truncate_middle
from mac_cleanup.targets.builtin import PROFILES, by_id, profile

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_PARTIAL = 2
EXIT_FAILED = 3
EXIT_NOTHING = 4
EXIT_DRY_RUN_DEFAULT = 5
EXIT_GUARD_SKIPPED = 6
EXIT_CANCELLED = 7
EXIT_INTERRUPTED = 130

# Paths v0.1 knows it must never place its own state inside. Deliberately a literal tuple:
# an earlier design validated the state directory against allowlist roots and risk tiers
# that only exist in v0.2, i.e. against the empty set, so it would have refused nothing --
# and an advertised check that refuses nothing is worse than no check, because a reader
# counts it as coverage. XDG_STATE_HOME is user-controlled, so this is reachable with one
# `export`.
_STATE_CONFLICTS = (
    "~/Library/Developer/Xcode/DerivedData",
    "~/Library/pnpm/store",
    "~/.npm/_cacache",
    "~/.gradle/caches",
    "~/Library/Caches",
    "~/Library/Developer/CoreSimulator",
)

# Enumerating these makes a provider fetch content from the server and materialise
# placeholders -- a network transfer and a write, from something billed as read-only.
_CLOUD_PREFIXES = (
    "~/Library/Mobile Documents",
    "~/Library/CloudStorage",
)


@dataclass
class Preflight:
    ok: bool
    message: str = ""


def _expand(p: str) -> str:
    return os.path.join(home(), p[2:]) if p.startswith("~/") else p


def check_environment() -> Preflight:
    """Refusals that apply before any subcommand runs."""
    if os.geteuid() == 0:
        return Preflight(
            False,
            "mac-cleanup refuses to run as root.\n"
            "Every path it manages belongs to one user, and sudo would let it reach another "
            "user's data or system files. Run it as yourself.",
        )
    if os.getuid() != os.geteuid():
        return Preflight(False, "refusing to run setuid (uid != euid)")

    env_home = os.environ.get("HOME")
    if env_home and os.path.realpath(env_home) != os.path.realpath(home()):
        return Preflight(
            False,
            f"$HOME ({env_home}) disagrees with the password database ({home()}).\n"
            "Refusing to guess which one you meant.",
        )

    state = os.path.realpath(state_dir())
    for raw in _STATE_CONFLICTS:
        conflict = os.path.realpath(_expand(raw))
        if state == conflict or state.startswith(conflict + os.sep):
            return Preflight(
                False,
                f"E_STATE_DIR_CONFLICT: the state directory ({state}) is inside {raw}, "
                "which this tool cleans -- it would delete its own state mid-run.\n"
                "Unset XDG_STATE_HOME or point it somewhere else.",
            )
    return Preflight(True)


def is_cloud_path(path: str) -> bool:
    """Whether walking ``path`` would enumerate a cloud provider."""
    real = os.path.realpath(path)
    return any(
        real == os.path.realpath(_expand(p))
        or real.startswith(os.path.realpath(_expand(p)) + os.sep)
        for p in _CLOUD_PREFIXES
    )


def resolve_selectors(selectors: Sequence[str]) -> list[str]:
    """Map selectors to target ids.

    Accepts ids, ``group:<name>``, ``all``, and ``<prefix>.*`` globs. Rejects bare numbers
    outright, with a suggestion -- the predecessor's positional indexes are the hazard.
    """
    registry = by_id()
    out: list[str] = []
    for sel in selectors:
        if sel.isdigit():
            example = next(iter(registry), "xcode.derived-data")
            raise Refusal(
                "E_NUMERIC_SELECTOR",
                sel,
                f"--select takes target ids, not numbers. Did you mean --select {example}?",
            )
        if sel == "all":
            out.extend(registry)
        elif sel.startswith("group:"):
            group = sel.split(":", 1)[1]
            out.extend(i for i, t in registry.items() if getattr(t, "group", "") == group)
        elif sel.endswith(".*"):
            prefix = sel[:-1]
            out.extend(i for i in registry if i.startswith(prefix))
        elif sel in registry:
            out.append(sel)
        else:
            raise Refusal("E_UNKNOWN_TARGET", sel, "see `mac-cleanup list`")
    seen: set[str] = set()
    unique: list[str] = []
    for target_id in out:
        if target_id not in seen:
            seen.add(target_id)
            unique.append(target_id)
    return unique


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mac-cleanup",
        description="Inspect and reclaim disk space on macOS, carefully.",
        epilog=(
            "Exit codes: 0 ok | 1 usage | 2 partial | 3 failed | 4 nothing matched | "
            "5 dry-run default | 6 skipped by a guard | 7 cancelled | 130 interrupted"
        ),
    )
    p.add_argument("--version", action="version", version=f"mac-cleanup {__version__}")
    sub = p.add_subparsers(dest="command")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output on stdout")
    common.add_argument("--no-color", action="store_true", help="disable ANSI colour")

    lst = sub.add_parser("list", parents=[common], help="list targets and cached sizes")
    lst.add_argument("--all", action="store_true", help="include absent and non-default targets")
    lst.add_argument("--group", help="only this group")

    size = sub.add_parser("size", parents=[common], help="measure targets")
    size.add_argument("selectors", nargs="*", help="target ids; default all")
    size.add_argument(
        "-s",
        "--select",
        action="append",
        default=[],
        dest="select",
        metavar="ID",
        help="target id, group:NAME, or ID.* glob; repeatable",
    )
    size.add_argument("--exact", action="store_true", help="ignore budgets; measure fully")
    size.add_argument("--threads", type=int, default=8)

    du = sub.add_parser("du", parents=[common], help="measure an arbitrary path (read-only)")
    du.add_argument("path")
    du.add_argument("--threads", type=int, default=8)
    du.add_argument(
        "--cloud",
        action="store_true",
        help="permit walking iCloud/CloudStorage paths (fetches content; off by default)",
    )
    du.add_argument("--cross-device", action="store_true", help="follow mount boundaries")
    du.add_argument("--full-paths", action="store_true", help="print full paths, not basenames")

    clean = sub.add_parser("clean", parents=[common], help="reclaim space")
    clean.add_argument("selectors", nargs="*", help="target ids (see `mac-cleanup list`)")
    clean.add_argument(
        "-s",
        "--select",
        action="append",
        default=[],
        dest="select",
        metavar="ID",
        help="target id, group:NAME, or ID.* glob; repeatable",
    )
    clean.add_argument("-p", "--profile", default=None, help="safe | dev")
    clean.add_argument("-n", "--dry-run", action="store_true", help="plan only; never mutates")
    clean.add_argument("--apply", action="store_true", help="actually do it")
    clean.add_argument(
        "-y", "--yes", action="store_true", help="skip prompts; always requires --apply"
    )
    clean.add_argument("--threads", type=int, default=8)

    prof = sub.add_parser("profile", parents=[common], help="inspect profiles")
    prof.add_argument("action", choices=["list", "show"], nargs="?", default="list")
    prof.add_argument("name", nargs="?")

    sub.add_parser("doctor", parents=[common], help="check the environment")
    return p


def validate_clean_flags(args: argparse.Namespace) -> Preflight:
    """The ``--yes``/``--apply`` contract, and the refusal to let flag order decide."""
    if args.dry_run and args.apply:
        return Preflight(
            False,
            "--dry-run and --apply are mutually exclusive. Refusing to let flag order "
            "decide: that is how editing a shell history line turns a safe command "
            "destructive.",
        )
    if args.yes and not args.apply:
        return Preflight(
            False,
            "--yes requires --apply.\n"
            "A human at a prompt never needs --yes, so this costs interactive use nothing "
            "and makes the destructive word mandatory in an unattended run, where it is "
            "visible in a crontab.",
        )
    return Preflight(True)


def main(argv: Sequence[str] | None = None) -> int:
    signal.signal(signal.SIGINT, lambda *_: sys.exit(EXIT_INTERRUPTED))

    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return EXIT_USAGE

    env = check_environment()
    if not env.ok:
        print(env.message, file=sys.stderr)
        return EXIT_USAGE

    try:
        if args.command == "profile":
            return _cmd_profile(args)
        if args.command == "doctor":
            return _cmd_doctor(args)
        if args.command == "list":
            return _cmd_list(args)
        if args.command == "size":
            return _cmd_size(args)
        if args.command == "du":
            return _cmd_du(args)
        if args.command == "cache":
            return _cmd_cache(args)
        if args.command == "clean":
            return _cmd_clean(args)
        print(f"unknown command: {args.command}", file=sys.stderr)
        return EXIT_USAGE
    except Refusal as exc:
        print(f"{exc.code}: {exc.detail or exc.path}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:  # pragma: no cover - raced with the handler above
        return EXIT_INTERRUPTED


def _cmd_profile(args: argparse.Namespace) -> int:
    if args.action == "list":
        for p in PROFILES:
            print(f"{p.name} v{p.version}  {len(p.target_ids)} targets  {p.description}")
        return EXIT_OK
    if not args.name:
        print("profile show needs a name", file=sys.stderr)
        return EXIT_USAGE
    chosen = profile(args.name)
    if chosen is None:
        print(f"unknown profile: {args.name}", file=sys.stderr)
        return EXIT_USAGE
    registry = by_id()
    print(f"{chosen.name} v{chosen.version} -- {chosen.description}")
    for tid in chosen.target_ids:
        t = registry[tid]
        print(f"  {tid:<30} {getattr(t, 'label', '')}")
    return EXIT_OK


def _cmd_doctor(_args: argparse.Namespace) -> int:
    """Field-by-field, never a dump. ``doctor`` output is what a user pastes into a bug
    report, and ``~/.npmrc`` holds a plaintext auth token on this machine."""
    checks = [
        ("python", f"{sys.version_info.major}.{sys.version_info.minor}"),
        ("state_dir_ok", "yes" if check_environment().ok else "no"),
        ("running_as_root", "no" if os.geteuid() != 0 else "yes"),
        ("targets", str(len(by_id()))),
        ("profiles", ",".join(p.name for p in PROFILES)),
    ]
    for name, value in checks:
        print(f"{name:<18} {value}")
    return EXIT_OK


def _emit(payload: object, as_json: bool, human: str) -> None:
    """stdout carries the payload; stderr carries chrome. JSON mode is uncorruptible by
    construction rather than by remembering to suppress things."""
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(human)


def _cmd_list(args: argparse.Namespace) -> int:
    rows = service.detect(include_absent=args.all)
    if args.group:
        rows = [r for r in rows if r.group == args.group]
    if not rows:
        print("no targets matched", file=sys.stderr)
        return EXIT_NOTHING

    rows.sort(key=lambda r: (-r.size.alloc, r.target_id))
    if args.json:
        _emit(
            {
                "targets": [
                    {
                        "target_id": r.target_id,
                        "group": r.group,
                        "risk": r.risk,
                        "present": r.present,
                        "alloc_bytes": r.size.alloc,
                        "quality": r.size.quality,
                        "stale": r.stale,
                        "non_default": r.non_default,
                    }
                    for r in rows
                ]
            },
            True,
            "",
        )
        return EXIT_OK

    print(f"{'TARGET':<30} {'SIZE':>22}  {'RISK':<14} NOTE")
    total = 0
    for r in rows:
        cell = size_cell(r.size.alloc, r.size.quality, reason=r.reason, age_s=r.age_s)
        mark = " *" if r.non_default else ""
        print(f"{r.target_id:<30} {cell:>22}  {r.risk:<14} {r.reason}{mark}")
        total += r.size.alloc
    print(f"\n{len(rows)} targets, {human_bytes(total)} known (cached where available).")
    print("Sizes are an upper bound; run `size` to measure. * = not in any profile.")
    return EXIT_OK


def _cmd_size(args: argparse.Namespace) -> int:
    chosen = list(args.selectors or []) + list(getattr(args, "select", []) or [])
    ids = resolve_selectors(chosen or ["all"])
    sink = make_sink(json_mode=args.json)
    sizes = service.measure(ids, workers=args.threads, sink=sink)
    if not sizes:
        print("nothing measurable in that selection", file=sys.stderr)
        return EXIT_NOTHING

    ordered = sorted(sizes.items(), key=lambda kv: -kv[1].alloc)
    if args.json:
        _emit(
            {
                "targets": [
                    {
                        "target_id": tid,
                        "alloc_bytes": s.alloc,
                        "logical_bytes": s.logical,
                        "files": s.files,
                        "quality": s.quality,
                        "dataless_files": s.dataless_files,
                    }
                    for tid, s in ordered
                ]
            },
            True,
            "",
        )
        return EXIT_OK

    total = 0
    for tid, s in ordered:
        print(f"{tid:<30} {size_cell(s.alloc, s.quality):>22}  {s.files:>9,} files")
        total += s.alloc
    print(f"\n{human_bytes(total)} across {len(ordered)} targets (upper bound).")
    return EXIT_OK


def _cmd_du(args: argparse.Namespace) -> int:
    if is_cloud_path(args.path) and not args.cloud:
        print(
            f"Refusing to walk {args.path}: it is inside a cloud provider, and enumerating "
            "one makes it fetch content from the server -- a network transfer and a write "
            "from a read-only command. Pass --cloud to override.",
            file=sys.stderr,
        )
        return EXIT_GUARD_SKIPPED

    sink = make_sink(json_mode=args.json)
    children = service.du(
        args.path,
        workers=args.threads,
        cross_device=args.cross_device,
        include_cloud=args.cloud,
        sink=sink,
    )
    if not children:
        print("nothing under that path", file=sys.stderr)
        return EXIT_NOTHING

    if args.json:
        _emit(
            {
                "children": [
                    {
                        "name": c.name if not args.full_paths else c.path,
                        "alloc_bytes": c.alloc,
                        "files": c.files,
                    }
                    for c in children
                ]
            },
            True,
            "",
        )
        return EXIT_OK

    total = sum(c.alloc for c in children)
    for c in children:
        name = c.path if args.full_paths else c.name
        print(f"{human_bytes(c.alloc):>12}  {truncate_middle(name, 60)}")
    print(f"\n{human_bytes(total)} total. Symlinks are not followed; device boundaries are")
    print("not crossed without --cross-device. Basenames only unless --full-paths.")
    return EXIT_OK


def _cmd_cache(args: argparse.Namespace) -> int:
    from mac_cleanup.scan.cache import SizeCache

    cache = SizeCache()
    cache.load()
    if args.action == "clear":
        n = cache.clear()
        cache.save()
        print(f"cleared {n} cached measurements")
        return EXIT_OK
    print(f"{len(cache)} cached measurements at {cache.path}")
    return EXIT_OK


def _cmd_clean(args: argparse.Namespace) -> int:
    flags = validate_clean_flags(args)
    if not flags.ok:
        print(flags.message, file=sys.stderr)
        return EXIT_USAGE

    selectors = list(args.selectors or []) + list(getattr(args, "select", []) or [])
    if args.profile:
        chosen = profile(args.profile)
        if chosen is None:
            print(f"unknown profile: {args.profile}", file=sys.stderr)
            return EXIT_USAGE
        selectors.extend(chosen.target_ids)
    if not selectors:
        print("nothing selected. Use --profile safe or --select <id>.", file=sys.stderr)
        return EXIT_NOTHING

    ids = resolve_selectors(selectors)
    clean_plan = service.plan(ids)
    dry = not args.apply

    for tid, why in clean_plan.refused:
        print(f"  skip {tid}: {why}", file=sys.stderr)

    if not clean_plan.path_targets and not clean_plan.delegated:
        print("nothing to do", file=sys.stderr)
        return EXIT_NOTHING

    if dry:
        print("DRY RUN -- nothing will be removed. Re-run with --apply.", file=sys.stderr)
    elif clean_plan.delegated:
        tools = ", ".join(sorted({d.tool for d in clean_plan.delegated}))
        print(
            f"About to run: {tools}. These cannot be interrupted once started -- they are "
            "spawned in their own session so Ctrl-C cannot leave a half-rewritten package "
            "tree.",
            file=sys.stderr,
        )

    report = service.execute(clean_plan, dry_run=dry)
    if args.json:
        _emit(report.to_artifact(), True, "")
    else:
        print(report.human())
    return EXIT_DRY_RUN_DEFAULT if dry else report.exit_code()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
