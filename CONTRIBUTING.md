# Contributing

## Local development

```console
make setup     # uv venv + dev dependencies
make test      # fast unit suite; must stay under 10s
make test-all  # full suite with coverage
make lint      # ruff format --check, ruff check, mypy --strict on src/
make guards    # structural guards
make verify    # everything CI runs, in order
```

## The three seams, and why they are enforced rather than requested

1. **One deleter.** `src/mac_cleanup/fs/deleter.py` is the only module that may remove
   anything.
2. **One runner.** `src/mac_cleanup/ext/runner.py` is the only module that may import
   `subprocess`. Every tool parser is therefore a pure function over text, which is what
   lets the suite run on Linux with only the invocation faked.
3. **One home resolver.** `src/mac_cleanup/paths.py` owns the single `expanduser`
   exemption. `$HOME` is spoofable; the password database is not.

`ruff`'s `flake8-tidy-imports.banned-api` enforces all three, with `per-file-ignores` for
exactly those files, and `scripts/guards.sh` greps as a backstop — `ruff` resolves bans
through imports and cannot see `getattr` tricks or bound-method aliases. Both run; neither
is sufficient alone. This caught a real violation during initial development: `report.py`
called `os.path.expanduser` while the config forbade it.

## Coverage floors live with their code

Per-module, not repo-wide, so easy-to-test code cannot satisfy the floor on behalf of the
accounting and deletion paths. `src/mac_cleanup/safety/*` and its 100%-branch floor arrive
with v0.2 — **a floor on an absent or near-empty module passes vacuously**, which is the
failure this project objects to elsewhere.

## Adding a cleanup target

Targets are a literal tuple in `src/mac_cleanup/targets/builtin.py`, not runtime discovery.
A `PathTarget` needs a stable id, a path, a risk label, and a note that says what is lost.
A `DelegatedTarget` additionally needs `min_free_bytes` and, where the vendor command can
report success without doing the work, a `verify_argv`.

Two rules worth stating because both have bitten:

* **Size figures come from the delegate, never from `du`.** Measured here: `du` claimed
  5.855 GiB for a pnpm store subtree that freed 1.734 GiB (APFS clones share extents), and
  `simctl` under-reported a runtime by 1.0 GiB (deleting it also releases the volume's
  container allocation).
* **Probes get a short timeout.** A dead daemon makes a client wait forever rather than
  fail. `Runner.probe()` uses 5s; actions get 120s.

## Branch protection (repo owner, one time)

GitHub will not offer a status context as a required check until the workflow has posted it
at least once, so push, let CI run, then configure.

**Settings → General:** default branch `main`; squash merging only; auto-delete head
branches; Issues on, Wikis and Projects off.

**Settings → Code security:** Dependabot alerts, security updates and version updates on;
secret scanning and push protection on; **private vulnerability reporting on** — that is the
channel `SECURITY.md` points at.

**Settings → Rules → Rulesets → New branch ruleset** named `main-protection`, Active,
targeting the default branch:

* Restrict deletions, block force pushes, require linear history, require conversation
  resolution.
* **Required approvals: 0.** GitHub forbids self-approval, so a literal "1 approval"
  deadlocks every merge on a solo repo.
* Required status checks (v0.1): `lint`, `test (py3.11)`, `guards`. Add `test (py3.13)`,
  `macos` and `Validate PR title (Conventional Commits)` once each has posted. Do **not**
  require any `codecov/*` context — a third-party upload is a flake class, and the coverage
  that matters is asserted locally by `coverage report --fail-under`.
* Bypass: repository admin only, as an audited escape hatch.

## Known limits of the denylist guard

`scripts/denylist_guard.py` (v0.2) hashes candidate tokens and compares against
`scripts/denylist.sha256`, so the guard file names nothing it protects. It **cannot** see
the repository description, topics, website, social-preview image, or any issue or release
body. Read those by eye before flipping visibility.

Short terms will eventually collide with an innocent word. A hit means *rephrase* — do not
try to learn which term matched, and do not weaken the guard.
