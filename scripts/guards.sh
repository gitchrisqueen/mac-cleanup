#!/usr/bin/env bash
# Structural guards. Each one is a mechanism, not a habit: it fails the build rather than
# relying on a reviewer to notice.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

fail=0
note() { printf '  %s\n' "$*"; }

echo "[guard] the bootstrap launcher cannot delete or truncate anything"
# /dev/null is not a file anyone cares about, so redirects to it are exempt. What is banned
# is a removal verb, or a redirect that would truncate a real absolute path.
if grep -nE '(^|[^[:alnum:]_-])(rm|rmdir|unlink|shred|truncate)([^[:alnum:]_-]|$)' bin/mac-cleanup \
   || grep -nE '>[[:space:]]*/(?!dev/null)' -P bin/mac-cleanup 2>/dev/null \
   || grep -nE '>[[:space:]]*/' bin/mac-cleanup | grep -v '/dev/null'; then
  note "FAIL: bin/mac-cleanup contains a deletion or truncation verb"
  fail=1
else
  note "ok"
fi

echo "[guard] exactly one deletion call site, and one subprocess importer"
offenders=$(grep -rnE '\b(shutil\.rmtree|os\.remove|os\.unlink|os\.rmdir|os\.removedirs)\b' src/ \
  | grep -v '^src/mac_cleanup/fs/deleter.py:' || true)
if [ -n "$offenders" ]; then
  note "FAIL: deletion outside fs/deleter.py:"; printf '%s\n' "$offenders"; fail=1
else
  note "ok: deletion confined to fs/deleter.py"
fi
offenders=$(grep -rnE '^\s*(import subprocess|from subprocess)' src/ \
  | grep -v '^src/mac_cleanup/ext/runner.py:' || true)
if [ -n "$offenders" ]; then
  note "FAIL: subprocess imported outside ext/runner.py:"; printf '%s\n' "$offenders"; fail=1
else
  note "ok: subprocess confined to ext/runner.py"
fi

echo "[guard] committed artifacts contain no paths"
if ls results/*.json >/dev/null 2>&1; then
  python3 - <<'PY' || fail=1
import glob, json, os, re, sys
home = os.path.basename(os.path.expanduser("~"))
bad = []
def walk(node, where):
    if isinstance(node, dict):
        for k, v in node.items(): walk(v, f"{where}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node): walk(v, f"{where}[{i}]")
    elif isinstance(node, str) and (re.match(r"^/", node) or (home and home in node)):
        bad.append(f"{where}={node!r}")
for path in glob.glob("results/*.json"):
    walk(json.load(open(path)), path)
if bad:
    print("  FAIL: path-like values in artifacts:"); [print("   ", b) for b in bad]; sys.exit(1)
print("  ok: no absolute paths, no home directory name")
PY
else
  note "ok: no artifacts yet"
fi

echo "[guard] shell syntax and shellcheck"
bash -n bin/mac-cleanup scripts/*.sh && note "bash -n ok"
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -S warning bin/mac-cleanup scripts/*.sh && note "shellcheck ok"
else
  note "shellcheck not installed locally; CI runs it"
fi

exit "$fail"
