# Authoritative file tree

Every ruff `per-file-ignores` entry, every coverage floor, and every guard grep path is
derived from this list. A CI floor naming a path that is absent here must fail loudly, not
pass vacuously.

| Path | v | Responsibility | Coverage floor |
|---|---|---|---|
| `src/mac_cleanup/__init__.py` | 0.1 | `__version__`, `ENGINE_V`. No side-effecting imports | — |
| `src/mac_cleanup/__main__.py` | 0.1 | `python -m mac_cleanup` | — |
| `src/mac_cleanup/cli.py` | 0.1 | argparse, exit codes, signal wiring | — |
| `src/mac_cleanup/errors.py` | 0.1 | `Refusal`, `ScanError`, `DeleteError`, `classify_oserror` | — |
| `src/mac_cleanup/paths.py` | 0.1 | `home()`, `expand()`, `resolve_parent()`, `state_dir()` | 95 |
| `src/mac_cleanup/forest.py` | 0.1 | target forest, cut-point attribution, upper-bound accounting | 95 |
| `src/mac_cleanup/render.py` | 0.1 | `human_bytes`, size cells, tables, middle-truncation | — |
| `src/mac_cleanup/progress.py` | 0.1 | `ProgressSink`, `TTYProgress`, `PlainProgress` | — |
| `src/mac_cleanup/report.py` | 0.1 | three labelled numbers; path-free JSON artifacts | 95 |
| `src/mac_cleanup/fs/stat.py` | 0.1 | `FsStat` protocol — the injection seam | — |
| `src/mac_cleanup/fs/deleter.py` | 0.1 | **the only module that may remove anything**; owns the dirfd walker | 100 |
| `src/mac_cleanup/ext/runner.py` | 0.1 | **the only module that may import `subprocess`** | 95 |
| `src/mac_cleanup/ext/{brew,npm,pnpm,simctl}.py` | 0.1 | pure parsers over `bytes`; no `subprocess` | 95 |
| `src/mac_cleanup/scan/walker.py` | 0.1 | work-stealing `os.scandir` walk | 90 |
| `src/mac_cleanup/scan/cache.py` | 0.1 | size cache; stale-but-returned | 95 |
| `src/mac_cleanup/scan/sizes.py` | 0.1 | quality states incl. `dataless` | 95 |
| `src/mac_cleanup/scan/budget.py` | 0.1 | time/inode budget; partial flag | 95 |
| `src/mac_cleanup/scan/cancel.py` | 0.1 | `CancelToken` — the only cancellation mechanism | 95 |
| `src/mac_cleanup/targets/*.py` | 0.1 | target data + `profiles.py` | — |
| `src/mac_cleanup/safety/containment.py` | **0.2** | guard R1–R12 | 100 branch |
| `src/mac_cleanup/safety/{denylist,policy}.py` | **0.2** | deny tuples, tier policy | 100 branch |
| `bin/mac-cleanup` | 0.1 | bootstrap; finds python3, execs module. **No deletion verb** | — |

Floors live with their code: `safety/*` and its 100-branch floor arrive in v0.2, because a
100% floor on an absent or near-empty module passes vacuously.
