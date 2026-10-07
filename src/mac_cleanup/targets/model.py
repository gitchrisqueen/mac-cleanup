"""What a cleanup target is.

Two kinds, and the distinction is the whole v0.1 safety boundary:

``PathTarget``
    A **frozen, hardcoded** path this tool removes itself. Not an arbitrary path: the set is
    a literal tuple in :mod:`mac_cleanup.targets.builtin`, reviewable in one screen. Each
    still gets per-child ``O_NOFOLLOW`` + ``st_dev`` checks from the deleter, because
    "it is only one well-known path" is not a containment check -- ``DerivedData/*`` is a
    glob over whatever happens to be there.

``DelegatedTarget``
    A vendor command (``brew cleanup``, ``simctl runtime delete``). The vendor's own GC does
    the removal. That is genuinely safer than reimplementing it, but it is **not** a safety
    argument on its own: it relocates the risk to the precondition and the verification, so
    each one carries a free-space floor and an after-the-fact check.

Arbitrary-path deletion, risk tiers, Trash and the full containment guard are v0.2.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Risk labels. v0.1 displays them and uses them for profile membership; the tier *policy*
# (typed confirmations, Trash routing) arrives with the guard in v0.2.
REGENERABLE = "regenerable"
REBUILDABLE = "rebuildable"
REPORT_ONLY = "report-only"


@dataclass(frozen=True)
class PathTarget:
    """A hardcoded path whose contents this tool removes."""

    target_id: str
    path: str
    label: str
    risk: str = REGENERABLE
    group: str = "misc"
    contents_only: bool = True
    """True: empty the directory, keep it. The predecessor deleted parents outright."""

    bundle_ids: tuple[str, ...] = ()
    """Apps whose live process should block this target. A heuristic, not a proof: it
    misses launchd daemons with no .app bundle."""

    note: str = ""
    non_default: bool = False
    """Excluded from every profile; must be named explicitly. Used for shared caches whose
    rebuild cost is larger than the label suggests."""


@dataclass(frozen=True)
class DelegatedTarget:
    """A target reclaimed by running the vendor's own command."""

    target_id: str
    label: str
    tool: str
    plan_argv: tuple[str, ...]
    apply_argv: tuple[str, ...]
    risk: str = REGENERABLE
    group: str = "misc"
    min_free_bytes: int = 2 * 1024**3
    """Refuse below this. `brew cleanup` rewrites Cellar kegs while /usr/local/opt symlinks
    point into them; interrupted by ENOSPC it leaves broken links needing a download."""

    verify_argv: tuple[str, ...] = ()
    """Run after applying. `simctl runtime delete` can update its own bookkeeping while the
    volume stays mounted, so "exit 0" is not evidence the space came back."""

    note: str = ""
    uninterruptible: bool = True
    """Spawned with start_new_session=True so Ctrl-C cannot kill it mid-write. The caller
    must say so before starting."""


Target = PathTarget | DelegatedTarget


@dataclass(frozen=True)
class Profile:
    """A named selection. Versioned, so an upgrade that adds targets cannot silently widen
    an unattended run."""

    name: str
    version: int
    target_ids: tuple[str, ...]
    description: str = ""


@dataclass
class Detected:
    """A target plus what the machine says about it right now."""

    target: Target
    present: bool
    reason: str = ""
    extras: dict[str, str] = field(default_factory=dict)
