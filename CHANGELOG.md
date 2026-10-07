# Changelog

Hand-written on purpose. For a tool that deletes files, an entry explaining *why* a
threshold moved is the only kind worth having, and a generator cannot produce it from commit
subjects.

## 0.1.0 — 2026-10-07

First release. https://pypi.org/project/mac-cleanup-cli/0.1.0/

Replaces a 497-line bash script with three measured defects: a menu
that issued 816 `du -sk` invocations per render, an audit that redirected all its output into
a file and so printed nothing for the duration, and `find … -exec rm -rf {} + 2>/dev/null`
followed by an unconditional `Done.`

### Engine

* **Target forest with cut-point attribution** so a parent and its child targets are walked
  once rather than once each. The largest overlap in the predecessor was `CoreSimulator` and
  its own child `CoreSimulator/Devices`, both measuring 10.861 GiB.
* **Accounting states an upper bound**, `sum(st_blocks * 512) >= physical`, because APFS
  clones report the full allocation on both copies with `st_nlink == 1` on each and no
  `stat(2)` field distinguishes them. Hardlinks *are* detectable and are deduplicated.
* **Work-stealing walker** over one shared LIFO stack. Descent uses
  `is_dir(follow_symlinks=False)`; the default would recurse forever through
  `/Volumes/Macintosh HD -> /`. A visited directory-inode set closes the firmlink
  double-walk, since `/Users` and `/System/Volumes/Data/Users` are one inode.
* **Quality markers on every size**, including `dataless` for evicted iCloud placeholders,
  which report `st_blocks == 0`. A bare `0 B` reads as "nothing there, safe to delete" — the
  exact misread that made the predecessor's unreadable runtime volumes look disposable.

### Safety

* **One deletion call site**, `fs/deleter.py`, enforced by `ruff` and a grep backstop.
* **Status is derived from the error list**, never asserted, so a partial delete cannot
  render as success.
* **Unlinking one name of a hardlinked inode is reported as freeing nothing**, because it
  does. Charging those blocks would push "freed" past the `statvfs` delta and invite blaming
  the gap on APFS snapshots.
* **`--yes` always requires `--apply`.** Keying the dry-run default on `isatty` would have
  been wrong: `tmux new-session -d 'mac-cleanup --yes'` has a tty.
* **`E_STATE_DIR_CONFLICT`** refuses to run when `XDG_STATE_HOME` points inside a path this
  tool cleans — one `export` away from the tool deleting its own state mid-run.
* **`du` refuses cloud paths** unless `--cloud`: enumerating a FileProvider domain makes it
  fetch from the server.
* **Probes get a 5s timeout, actions 120s.** A dead daemon makes a client wait forever
  rather than fail; the predecessor called `docker system df` with no timeout at all, so its
  Docker menu hung indefinitely with no output.

### Artifacts

* `results/*.json` is **structurally incapable of holding a path**, and the serializer
  raises rather than relying on review. Committed measurements are published, and a
  hash-based denylist cannot catch a filename it was never given a hash for.
