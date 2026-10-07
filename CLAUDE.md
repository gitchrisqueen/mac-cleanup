# CLAUDE.md — mac-cleanup

## What this is

A macOS disk-space auditor that can delete. v0.1 measures, reports, and reclaims through
each vendor's own tool plus a frozen list of paths. Replaces a 497-line bash script.

## Cross-project context

Handoff packet: `HO-2026-10-07-01-mac-cleanup-repo`. Read it before changing scope — it
records what must not be rebuilt and why.

This repository is **public-bound**. Do not add a pointer to any private note store, and do
not reference an internal path or hostname anywhere in the tree or in a commit message.
`scripts/guards.sh` cannot catch what it was never given a hash for.

## Hard rules

1. **One deleter.** `src/mac_cleanup/fs/deleter.py` is the only module that may remove
   anything. One runner: `src/mac_cleanup/ext/runner.py` is the only `subprocess` importer.
   One home resolver: `src/mac_cleanup/paths.py` owns the single `expanduser` exemption.
   All three are enforced by ruff `banned-api` plus a grep backstop. Do not add a second.

2. **Sizes are an upper bound, never an equality.** `sum(st_blocks * 512) >= physical`.
   APFS clones report the full allocation on both copies with `st_nlink == 1` on each and no
   `stat(2)` field distinguishes them. Two earlier revisions of the design asserted a false
   invariant here; do not restore one. The `statvfs` delta is the only ground truth, which is
   why the run report prints both numbers and never equates them.

3. **Status is derived, never asserted.** `DeleteReport.status` comes from the error list.
   The defect this project exists to remove is `2>/dev/null` followed by `Done.`

4. **Never render a bare `0 B`.** Unmeasurable shows `?` with a reason; budgeted shows
   `>= X (partial)`; evicted cloud content is labelled. `0 B` reads as "nothing there, safe
   to delete", which is what made the predecessor's unreadable runtime volumes look
   disposable.

5. **Size figures come from the delegate, not from `du`.** Measured: `du` claimed 5.855 GiB
   for a pnpm subtree that freed 1.734 GiB; `simctl` under-reported a runtime by 1.0 GiB.

6. **Probes get a short timeout.** A dead daemon makes a client wait forever rather than
   fail. `Runner.probe()` is 5s, actions 120s.

7. **Targets are a literal tuple.** Not runtime discovery. `~/Library/Caches` has 167
   children here, including live account state.

8. **`results/*.json` holds no paths.** The serializer raises; CI greps. Committed
   measurements are published.

## Commands

```console
make test   # fast suite, under 10s
make lint   # ruff format --check, ruff check, mypy --strict on src/
make guards # structural guards (self-tested in CI)
make verify # everything CI runs
```

## Scope

v0.2 brings arbitrary-path deletion, the containment guard, risk tiers, the Trash backend,
the write-before-delete audit trail, and `DynamicChildren`. Coverage floors for
`src/mac_cleanup/safety/*` arrive with that code — a floor on an absent module passes
vacuously.
