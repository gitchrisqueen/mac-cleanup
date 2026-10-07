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
import os
import signal
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from mac_cleanup import __version__
from mac_cleanup.errors import Refusal
from mac_cleanup.paths import home, state_dir
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
    size.add_argument("selectors", nargs="*", default=["all"])
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
    clean.add_argument("selectors", nargs="*")
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
        if args.command == "clean":
            flags = validate_clean_flags(args)
            if not flags.ok:
                print(flags.message, file=sys.stderr)
                return EXIT_USAGE
            if not args.apply:
                print(
                    "Dry run (no --apply): nothing will be removed.",
                    file=sys.stderr,
                )
                return EXIT_DRY_RUN_DEFAULT
            print("clean --apply is not implemented in this commit", file=sys.stderr)
            return EXIT_USAGE
        if args.command == "du" and is_cloud_path(args.path) and not args.cloud:
            print(
                f"Refusing to walk {args.path}: it is inside a cloud provider, and "
                "enumerating one makes it fetch content from the server. Pass --cloud to "
                "override.",
                file=sys.stderr,
            )
            return EXIT_GUARD_SKIPPED
        print(f"{args.command} is not implemented in this commit", file=sys.stderr)
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
