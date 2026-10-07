# mac-cleanup

A macOS disk-space auditor that can delete. It measures the developer cache locations that
actually get large, reports what each one costs, and reclaims them — through each vendor's
own tool where one exists, and otherwise from a short frozen list of paths.

> **There is another `mac-cleanup`.** [mac-cleanup/mac-cleanup-py][incumbent] is older,
> more popular, and owns the `mac-cleanup` name on PyPI. If you want a broad cleaner with
> ~50 modules, use that one. This project exists for a narrower reason — see
> [Why this instead](#why-this-instead). It is distributed as `mac-cleanup-cli`.

```console
$ mac-cleanup list                       # what is there, from cache, instantly
TARGET                                           SIZE  RISK           NOTE
xcode.derived-data                          3.26 GiB  regenerable
pnpm.store-orphan                           5.86 GiB  regenerable
icloud.local-copies      19.59 GiB local (evicted content not counted)  report-only  *
ios.backups                                31.90 GiB  report-only                    *

$ mac-cleanup size --select group:xcode  # measure for real, with live progress
$ mac-cleanup du ~/Library/Caches        # where did the space actually go
    7.75 GiB  Homebrew
    1.93 GiB  Google
  895.40 MiB  com.anthropic.claudefordesktop.ShipIt

$ mac-cleanup clean --profile safe       # plans only; exits 5, never 0
DRY RUN -- nothing will be removed. Re-run with --apply.
Would free:             1.49 GiB  across 10 targets (upper bound)

$ mac-cleanup clean --select cache.pip --apply
Freed (accounted):    439.61 MiB  across 1 targets
Disk free delta:      440.03 MiB  (statvfs before/after)
```

Dry run is what you get without `--apply`, and it exits **5**, not 0, so a CI step that
forgot the flag is visibly not-a-success rather than a green no-op. A dry run never prints a
`statvfs` delta: nothing was removed, so any movement in free space is other processes, and
showing it would be a result that is not one.

`--yes` **always** requires `--apply`. Tty detection would have been the wrong gate --
`tmux new-session -d 'mac-cleanup --yes'` has a tty -- so the destructive word is mandatory
in every unattended invocation, where a reviewer can see it in the crontab.

## Install

```console
pipx install mac-cleanup-cli      # or: uv tool install mac-cleanup-cli
```

The distribution is `mac-cleanup-cli`; the command it installs is `mac-cleanup`. The plain
`mac-cleanup` name on PyPI belongs to [the other project][incumbent].

Either installer brings its own Python, which matters more than it sounds: on macOS
`/usr/bin/python3` is **not an interpreter**. It is the `xcrun` shim — byte-identical to
`/usr/bin/git` and `/usr/bin/clang` — and it reports a version only when Xcode or the
Command Line Tools are installed. Without them, running it offers to download several
gigabytes, which is the worst possible failure for a tool you reached for *because the disk
is full*. `bin/mac-cleanup`, the launcher used when running from a clone, checks for that case and
prints an install line instead of triggering the download.

Zero runtime dependencies (`dependencies = []` in `pyproject.toml`); Python 3.11+.

## Why this exists

It replaces a 497-line bash script with three specific defects, each verified in the source
rather than inferred:

| Defect | Where | What was wrong |
|---|---|---|
| Menu took minutes | `get_sorted_line` line 149, called per row from line 160 | Re-ran `du -sk` over **every** target to return **one** row. 33 targets, 34 passes, **816 `du` invocations per render** |
| Audit appeared to hang | lines 250–299 | The whole report body was wrapped in `{ … } > "$REPORT_FILE"`, so the terminal printed nothing for the duration |
| Success reported on failure | `safe_clear_contents` line 63 | `find … -exec rm -rf {} + 2>/dev/null`, then `Done.` printed unconditionally at lines 205/210/220/230 |

The third one is worth seeing in full. Line 226 ran `rm -rf` on
`/Library/Developer/CoreSimulator/Volumes/*` — which are **mounted read-only APFS volumes**,
sitting in a root-owned directory. The removal cannot succeed; it fails `EPERM` on the
parent's permissions. The error was discarded and the script printed `Removed.` The same
report showed all four of those rows as **`0 B`**, which reads as "nothing there, safe to
delete."

So this tool: shows a quality marker on every number, derives success from the error list
instead of asserting it, and removes simulator runtimes with `simctl runtime delete` — the
only thing that actually unmounts and releases the volume.

## Three numbers, never one

```
Freed (accounted):     41.23 GiB  across 4 targets
Disk free delta:       38.91 GiB  (statvfs before/after)
Unaccounted:            2.32 GiB  -- copy-on-write clones share extents and are invisible
                                     to stat; APFS local snapshots may also hold space
```

They disagree routinely, in both directions, and the gap is informative. Measured while
clearing this machine:

| Target | What `du`/`simctl` said | What `statvfs` showed |
|---|---|---|
| pnpm store (clone-based) | 5.855 GiB | **1.734 GiB** |
| Homebrew cache | 9.428 GiB | 7.550 GiB |
| simulator runtime 21A342 | 6.715 GiB | **7.721 GiB** |
| unavailable sim devices | 3.608 GiB | **0.174 GiB** |

Every pre-measured figure was wrong. Reporting one number would have hidden that, so the
accounted figure and the measured delta are printed separately and never equated. The walk
produces an **upper** bound: `sum(st_blocks * 512) >= physical bytes`, because APFS clones
report the full allocation on both copies with `st_nlink == 1` on each, and no `stat(2)`
field distinguishes them.

## Safety

* No arbitrary-path deletion in v0.1. Targets are a frozen list in
  [`src/mac_cleanup/targets/builtin.py`](src/mac_cleanup/targets/builtin.py), reviewable in
  one screen.
* Deletion happens in exactly one module, `fs/deleter.py`, enforced by `ruff` and a grep
  guard. Directories are opened `O_NOFOLLOW` relative to a held descriptor and removals
  issued with `dir_fd=`, so a directory swapped for a symlink mid-walk fails rather than
  redirecting the delete.
* A symlink is unlinked, never followed. A `.git` found inside a target stops that subtree
  and is reported.
* Unlinking one name of a hardlinked file is reported as freeing **nothing**, because it does.
* iOS backups and iCloud local copies are **reported, never deleted**.
* `du` refuses cloud paths by default: enumerating a provider makes it fetch from the server.
* Never runs as root; refuses a `$HOME` that disagrees with the password database.

## Why this instead

Narrow and specific, which is the honest framing against a 2,379-star incumbent:

* **Correct simulator runtime removal.** They are mounted volumes, not directories.
* **Clone-aware accounting** that states an upper bound rather than a false equality.
* **Status derived from the error list**, so a partial delete can never render as success.
* **A quality marker on every size**, so an unreadable or evicted target never shows `0 B`.

## Known limits

* The window between planning and applying is narrowed, not eliminated. The walk is
  descriptor-relative; the identity re-check before it pins only the leaf.
* Measurement is not entirely side-effect-free. Running `xcrun simctl runtime list` was
  observed causing CoreSimulatorService to rewrite a root-owned plist. The accurate claim is
  that the scanner performs no unlink.
* Evicted iCloud files report `st_blocks == 0`. They are labelled, not counted.
* External tool output is parsed, so a format change degrades a feature with a named error.
* No claim is made about how much space *you* will reclaim. That depends on your machine.

## Development

```console
make setup && make test && make lint && make guards
```

MIT. See [NOTICE](NOTICE) for the naming situation and the rewrite provenance.

[incumbent]: https://github.com/mac-cleanup/mac-cleanup-py
