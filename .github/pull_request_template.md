## What & why

## Test evidence

- [ ] `make lint`
- [ ] `make guards`
- [ ] `make test-all`
- [ ] If this touches sizing or deletion: a test with a **literal** expected byte count

## Checklist

- [ ] No new deletion call site outside `fs/deleter.py`
- [ ] No new `subprocess` importer outside `ext/runner.py`
- [ ] No absolute path or home-directory name added to `results/`
- [ ] No new runtime dependency (the runtime is stdlib-only, by design)
- [ ] Any new size figure comes from the delegate or a walk, and says which
