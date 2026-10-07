# Security

## Reporting

Use GitHub's **private vulnerability reporting** on this repository. Do not open a public
issue.

This is also the right channel for **"a release removed something I needed."** That is a
security report about a destructive tool, and it must not land in a public issue with your
directory listing attached.

Please include:

* the output of `mac-cleanup --version` and `mac-cleanup doctor`;
* the `--json` plan from the run, if you have it;
* **not** a full directory listing. `mac-cleanup du --json` prints basenames by default for
  exactly this reason; `--full-paths` is opt-in.

Supported version: the latest minor release.

## What this tool deliberately does not protect against

Stated plainly, because a safety claim with an undisclosed gap is worse than no claim:

* **The window between planning and applying.** The delete walk is descriptor-relative:
  directories are opened `O_NOFOLLOW` against a held descriptor and removals are issued with
  `dir_fd=`, so a directory swapped for a symlink mid-walk fails rather than redirecting the
  delete. The identity re-check before the walk pins only the **leaf**. The window is
  narrowed, not closed.
* **Measurement is not entirely side-effect-free.** Running `xcrun simctl runtime list` was
  observed causing `CoreSimulatorService` to rewrite a root-owned plist. The accurate claim
  is that the scanner performs no unlink.
* **Copy-on-write clones are invisible to `stat(2)`.** Reported sizes are an upper bound on
  what a deletion frees. The `statvfs` delta is the only ground truth, which is why the run
  report prints both and never equates them.
* **The running-app guard is a heuristic.** It maps processes to bundle ids from exec paths
  and misses `launchd` daemons with no `.app`. It is defence in depth, not a proof.
* **External tool output is parsed.** A format change degrades a feature with a named
  `E_PARSE_*` error rather than a wrong number — but it does degrade the feature.
