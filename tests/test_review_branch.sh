#!/usr/bin/env bash
# Integration test of bin/review-branch (the gate) without Forgejo or a model: a local bare repo stands in for the forge, the tests run
# in the real gate container. Checks the measured findings (scope, weakened, duplicate, unused import) and their `== FLAGS:` line,
# the fast pass (a red changed test ends the gate without running the slow suite), GATE_FAST=0, and a clean branch (exit 0).
# Needs Docker and the worker image. ~2 min.
# Run: tests/test_review_branch.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMG="${GATE_IMAGE:-sandbox-worker:1.2.11}"
docker image inspect "$IMG" >/dev/null 2>&1 || { echo "SKIP: image $IMG not built"; exit 0; }
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
fail=0
check() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; tail -n 25 "$T/out" | sed 's/^/     | /'; fail=1; fi; }
mkdir -p "$T/forge/sandbox" "$T/secrets" "$T/w"; echo t > "$T/secrets/monitor.token"
git init -q --bare -b main "$T/forge/sandbox/playground.git"
W="$T/w"; cd "$W" || exit 1; git init -q -b main; git config user.email w@w; git config user.name worker-1
printf '"""Util."""\n\ndef clamp_value(value, low, high):\n    result = value\n    if result < low:\n        result = low\n    if result > high:\n        result = high\n    return result\n' > util.py
printf 'import util\n\ndef test_clamp():\n    assert util.clamp_value(5, 0, 3) == 3\n    assert util.clamp_value(-1, 0, 3) == 0\n    assert util.clamp_value(2, 0, 3) == 2\n' > test_util.py
printf 'import time\n\ndef test_slow():\n    time.sleep(40)\n    assert True is True\n' > test_slow.py
git add -A; git commit -qm base; git remote add origin "$T/forge/sandbox/playground.git"; git push -q origin main
branch() { git checkout -q -f -B "$1" main; git clean -fdq; }
gate() { # branch [env...] -> $T/out, $T/rc, $T/secs
  local b="$1"; shift; local t0=$SECONDS
  env FORGE_URL="file://$T/forge" FORGE_REPO=sandbox/playground SANDBOX_SECRETS="$T/secrets" GATE_IMAGE="$IMG" "$@" python3 "$ROOT/bin/review-branch" "$b" > "$T/out" 2>&1
  echo $? > "$T/rc"; echo $((SECONDS - t0)) > "$T/secs"
}
rc() { cat "$T/rc"; }
has() { grep -q -- "$1" "$T/out"; }

# 1. a branch with a file outside its list, a weakened test, a copied helper and an unused import: all measured, flagged, exit 1 (warnings)
branch feat/findings
printf '"""Other."""\nX = 1\n' > other.py
printf 'import util\n\ndef test_clamp():\n    assert util.clamp_value(5, 0, 3) == 3\n' > test_util.py
printf '"""Limits."""\n\ndef clamp_value(value, low, high):\n    result = value\n    if result < low:\n        result = low\n    if result > high:\n        result = high\n    return result\n' > limits.py
printf '"""Imp."""\nimport os\n\nY = 2\n' > imp.py
git add -A; git commit -qm findings; git push -q origin feat/findings
gate feat/findings GATE_ALLOWED_FILES=util.py,test_util.py
check "gate: files outside the task's list are named" 'has "scope: files changed that are not in the task.s file list: .*other.py"'
check "gate: a test that lost assertions is named" 'has "weakened: test_util.py"'
check "gate: a copied helper names the original" 'has "duplicate: limits.py defines .clamp_value. .*util.py"'
check "gate: a new unused import is named" 'has "unused-import: imp.py imports os"'
check "gate: the flags line lists them all" 'grep -q "^== FLAGS: .*scope" "$T/out" && grep "^== FLAGS:" "$T/out" | grep -q weakened && grep "^== FLAGS:" "$T/out" | grep -q duplicate && grep "^== FLAGS:" "$T/out" | grep -q unused-import'
check "gate: warnings only, exit 1" '[[ "$(rc)" == 1 ]]'
check "gate: the same branch without a file list has no scope finding" '! { gate feat/findings; has "scope:"; }'

# 2. a red changed test: the fast pass ends the gate within seconds, the 40 s test is not run
branch feat/red
printf '"""Red."""\nZ = 1\n' > red.py
printf 'import red\n\ndef test_red():\n    assert red.Z == 2\n' > test_red.py
git add -A; git commit -qm red; git push -q origin feat/red
gate feat/red GATE_ALLOWED_FILES=red.py
check "gate: a red changed test fails the gate" '[[ "$(rc)" == 2 ]]'
check "gate: the whole suite was not run" 'has "whole suite NOT run (fast pass is red)" && [[ "$(cat "$T/secs")" -lt 35 ]]'
gate feat/red GATE_ALLOWED_FILES=red.py GATE_FAST=0
check "gate: GATE_FAST=0 runs the whole suite" 'has "full test suite" && ! has "fast pass" && [[ "$(rc)" == 2 ]]'

# 3. a clean branch: its own module (wired into util.py) and its test, nothing flagged
branch feat/green
printf '"""Good."""\n\ndef double(x):\n    return x * 2\n' > good.py
printf 'import good\n\ndef test_double():\n    assert good.double(2) == 4\n' > test_good.py
sed -i '1a import good' util.py; printf '\n\ndef doubled_clamp(value, low, high):\n    return good.double(clamp_value(value, low, high))\n' >> util.py
git add -A; git commit -qm good; git push -q origin feat/green
gate feat/green GATE_ALLOWED_FILES=good.py,util.py
check "gate: a clean branch passes with no warnings" '[[ "$(rc)" == 0 ]] && has "== warnings: none" && ! has "== FLAGS"'
check "gate: its fast pass ran on the changed test and the importer of the changed module" 'has "== fast pass, 2 changed/related test file"'
echo "review-branch test: $([ $fail = 0 ] && echo ALL OK || echo FAILED)"
exit $fail
