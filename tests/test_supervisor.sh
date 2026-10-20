#!/usr/bin/env bash
# Integration test of bin/run-supervisor without any model: a throwaway container from the worker image whose agy-run is a
# fake. The fake commits a bug (not pushed) with a test that catches it; the supervisor's self-check must see the red suite,
# resume the SAME conversation with the output, the fake fixes the bug, and the branch must end up pushed - without the
# database file the "tests" left in the clone. Needs Docker and the worker image (bin/worker-add builds it). ~30 s.
# Run: tests/test_supervisor.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMG="${GATE_IMAGE:-sandbox-worker:1.2.11}"
C="af-suptest-$$"
docker image inspect "$IMG" >/dev/null 2>&1 || { echo "SKIP: image $IMG not built"; exit 0; }
T="$(mktemp -d)"; trap 'docker rm -f "$C" >/dev/null 2>&1; rm -rf "$T"' EXIT
cp -r "$ROOT/bin" "$T/"; mkdir -p "$T/etc" "$T/runs/20260101T000000Z-$C"
printf 'RUN_TIMEOUT_MIN=30\nRUN_TOKEN_CAP=1500000\nMAX_PARALLEL=4\nPOLL_SEC=1\nQUOTA_MIN_5H_PCT=0\nQUOTA_MIN_WEEK_PCT=0\n' > "$T/etc/limits.conf"
cat > "$T/fake-agy-run" <<'EOF'
#!/bin/bash
conv=""; for ((i=1;i<=$#;i++)); do [ "${!i}" = "--conversation" ] && { j=$((i+1)); conv="${!j}"; }; done
step() { echo '{"event":"step_update","step_update":{"step_type":"tool","state":"DONE","usage":{"total_tokens":1000}}}'; }
prompt=""; for ((i=1;i<=$#;i++)); do [ "${!i}" = "-p" ] && { j=$((i+1)); prompt="${!j}"; }; done
if [[ "$prompt" == *MARKER-TEST* ]]; then      # ORIENT: the supervisor has already put the clone on the task branch and filled the prompt in
  printf '%s' "$prompt" > /tmp/prompt-seen; git rev-parse --abbrev-ref HEAD > /tmp/head-seen
  step; echo '{"event":"result","result":{"status":"SUCCESS","response":"seen"}}'; exit 0
fi
if [ -z "$conv" ]; then
  echo '{"event":"init","init":{"conversation_id":"conv-123"}}'
  git checkout -q -b feat/0001-x origin/main
  printf '"""Adds numbers."""\ndef add(a, b):\n    return a - b\n' > calc.py
  printf 'import calc\n\ndef test_add():\n    assert calc.add(2, 2) == 4\n' > test_calc.py
  git add -A && git -c user.name=worker-9 -c user.email=w@w commit -qm "add calc"
  printf 'db' > gym.db
  step; step; echo '{"event":"result","result":{"status":"SUCCESS","response":"done"}}'
else
  echo "$conv" > /tmp/fix-called-with
  printf '"""Adds numbers."""\ndef add(a, b):\n    return a + b\n' > calc.py
  git -c user.name=worker-9 -c user.email=w@w commit -qam "fix add"
  step; echo '{"event":"result","result":{"status":"SUCCESS","response":"fixed"}}'
fi
EOF
docker run -d --name "$C" --network none --entrypoint sleep "$IMG" infinity >/dev/null
docker cp "$T/fake-agy-run" "$C:/tmp/agy-run"
docker exec -u root "$C" bash -c 'install -m 755 /tmp/agy-run "$(command -v agy-run)"'
docker exec "$C" bash -c 'set -e; git init -q --bare -b main /tmp/origin.git; git clone -q /tmp/origin.git /home/worker/playground 2>/dev/null
  cd /home/worker/playground; printf "\"\"\"Base.\"\"\"\n" > base.py; git add -A; git -c user.name=o -c user.email=o@o commit -qm init; git push -q origin HEAD:main'
R="$T/runs/20260101T000000Z-$C"
echo "# Task 0001-x" > "$R/prompt.md"
cat > "$R/meta.json" <<J
{"run_id": "20260101T000000Z-$C", "worker": "$C", "task": "0001-x.md", "test": false, "runner": null, "model": "fake", "project": "",
 "repo": "sandbox/playground", "branch": "feat/0001-x", "conversation": "", "variant": "pack-default", "self_check": 1,
 "limits": {"timeout_min": 30, "token_cap": 1500000}, "quota_before": [], "dispatched": "x"}
J
python3 "$T/bin/run-supervisor" "$R" || true
fail=0
check() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }
M="$(cat "$R/meta.json")"
check "run ended ok with 3000 tokens over both invocations" '[[ "$(python3 -c "import json,sys; m=json.loads(sys.argv[1]); print(m[\"reason\"], m[\"tokens\"])" "$M")" == "ok 3000" ]]'
check "self-check: red, then green after the fix" '[[ "$(python3 -c "import json,sys; print([c[\"ok\"] for c in json.loads(sys.argv[1])[\"self_checks\"]])" "$M")" == "[False, True]" ]]'
check "the fix resumed the same conversation" '[[ "$(docker exec "$C" cat /tmp/fix-called-with)" == "conv-123" ]]'
check "the fixed branch was pushed" '[[ "$(docker exec "$C" git -C /tmp/origin.git show feat/0001-x:calc.py | tail -1)" == "    return a + b" ]]'
check "the test database was not committed" '! docker exec "$C" git -C /tmp/origin.git ls-tree -r --name-only feat/0001-x | grep -q gym.db'
# ORIENT: a prompt with the start-state marker gets the clone on its task branch plus the state block (rework: the branch exists on origin;
# new task: it does not; no marker: untouched, as in the run above)
marker_run() {   # $1 = run number, $2 = branch
  local id="20260101T00000${1}Z-$C" d="$T/runs/20260101T00000${1}Z-$C"
  mkdir -p "$d"; printf '# Task MARKER-TEST\n<!-- start-state -->\nEND\n' > "$d/prompt.md"
  cat > "$d/meta.json" <<J
{"run_id": "$id", "worker": "$C", "task": "0002-y.md", "test": false, "runner": null, "model": "fake", "project": "",
 "repo": "sandbox/playground", "branch": "$2", "conversation": "", "variant": "pack-default", "self_check": 0,
 "limits": {"timeout_min": 30, "token_cap": 1500000}, "quota_before": [], "dispatched": "x"}
J
  python3 "$T/bin/run-supervisor" "$d" || true
}
docker exec "$C" bash -c 'set -e; git clone -q /tmp/origin.git /tmp/other; cd /tmp/other; git checkout -q -b feat/0002-y origin/main
  printf "x" > y.txt; git add -A; git -c user.name=o -c user.email=o@o commit -qm "first try"; git push -q origin feat/0002-y'
marker_run 2 feat/0002-y
check "marker, rework: clone is on the task branch" '[[ "$(docker exec "$C" cat /tmp/head-seen)" == "feat/0002-y" ]]'
check "marker, rework: state block says it continues origin, 0 behind / 1 ahead, lists the commit, marker gone" \
  'docker exec "$C" cat /tmp/prompt-seen | tr "\n" " " | grep -q "Start state (prepared by the supervisor.*continues origin/feat/0002-y.*0 commit(s) behind, 1 ahead.*first try.*END" && ! docker exec "$C" grep -q "start-state -->" /tmp/prompt-seen'
marker_run 3 feat/0003-z
check "marker, new task: new branch from origin/main" '[[ "$(docker exec "$C" cat /tmp/head-seen)" == "feat/0003-z" ]] && docker exec "$C" cat /tmp/prompt-seen | grep -q "new, from origin/main.*0 commit(s) behind, 0 ahead"'
# salvage: work left uncommitted in a repo whose .gitignore lists .pytest_cache (the excluded pathspec makes git add exit 1 there) must still be committed and pushed
cat > "$T/fake-agy-run" <<'EOF'
#!/bin/bash
echo '{"event":"init","init":{"conversation_id":"conv-s"}}'
git checkout -q -b feat/0004-s origin/main
mkdir -p .pytest_cache; echo cache > .pytest_cache/v; printf '.pytest_cache/\n' > .gitignore    # as in a real project's .gitignore
printf '"""Left uncommitted."""\n' > left.py
echo '{"event":"step_update","step_update":{"step_type":"tool","state":"DONE","usage":{"total_tokens":1000}}}'
echo '{"event":"result","result":{"status":"SUCCESS","response":"done"}}'
EOF
docker cp "$T/fake-agy-run" "$C:/tmp/agy-run"
docker exec -u root "$C" bash -c 'install -m 755 /tmp/agy-run "$(command -v agy-run)"'
d="$T/runs/20260101T000004Z-$C"; mkdir -p "$d"; echo "# Task 0004-s" > "$d/prompt.md"
cat > "$d/meta.json" <<J
{"run_id": "20260101T000004Z-$C", "worker": "$C", "task": "0004-s.md", "test": false, "runner": null, "model": "fake", "project": "",
 "repo": "sandbox/playground", "branch": "feat/0004-s", "conversation": "", "variant": "pack-default", "self_check": 0,
 "limits": {"timeout_min": 30, "token_cap": 1500000}, "quota_before": [], "dispatched": "x"}
J
python3 "$T/bin/run-supervisor" "$d" || true
check "salvage: uncommitted work beside .pytest_cache is committed and pushed" 'docker exec "$C" git -C /tmp/origin.git show feat/0004-s:left.py | grep -q Left && ! docker exec "$C" git -C /tmp/origin.git ls-tree -r --name-only feat/0004-s | grep -q pytest_cache'
# self-check of a project whose full suite is slow: it is remembered, then only the tests the branch touched run
extract() { python3 - "$T/bin/run-supervisor" <<'PYEOF'
import re, sys
print(re.search(r'SELF_CHECK_PY = r"""(.*?)"""', open(sys.argv[1]).read(), re.S).group(1))
PYEOF
}
docker exec "$C" bash -c 'set -e; rm -rf /tmp/slow; git clone -q /tmp/origin.git /tmp/slow; cd /tmp/slow; git config user.email o@o; git config user.name o
  printf "import time\n\ndef test_other_is_slow_and_red():\n    time.sleep(8)\n    assert False\n" > test_old.py; git add -A; git commit -qm old; git push -q origin HEAD:main; git fetch -q
  git checkout -q -b feat/slow origin/main; printf "def test_mine():\n    assert 1 + 1 == 2\n" > test_mine.py; git add -A; git commit -qm mine'
sc() { extract | docker exec -i -e AF_SELF_CHECK_MODE="$1" -e AF_SELF_CHECK_TIMEOUT="${2:-600}" -w /tmp/slow "$C" python3 - | tail -1; }
field() { python3 -c "import json,sys; print(json.loads(sys.argv[1]).get(sys.argv[2]))" "$1" "$2"; }
R1="$(sc full 3)"
check "self-check, full suite slower than the timeout: reported as slow, left to the gate" '[[ "$(field "$R1" slow)" == "True" && "$(field "$R1" ok)" == "True" ]]'
R2="$(sc changed)"
check "self-check, known slow suite: only the changed test runs (green although the unchanged old test is red)" '[[ "$(field "$R2" ok)" == "True" && "$(field "$R2" mode)" == "changed" && "$(field "$R2" secs)" -lt 8 ]]'
docker exec "$C" bash -c 'cd /tmp/slow; printf "def test_mine():\n    assert 1 == 2\n" > test_mine.py; git commit -qam break'
R3="$(sc changed)"
check "self-check, known slow suite: a red changed test is still reported" '[[ "$(field "$R3" ok)" == "False" && "$(field "$R3" summary)" == *failed* ]]'
# supervisor side: the verdict is remembered in runs/ and trusted for a week
python3 -c "import json, sys, time; json.dump({'cmd': 'python3 -m pytest -q -p no:cacheprovider', 'at': time.time()}, open(sys.argv[1] + '/runs/self-check-slow.json', 'w'))" "$T"
sed -i 's/"self_check": 0/"self_check": 1/' "$T/runs/20260101T000004Z-$C/meta.json"
rm -f "$T/runs/20260101T000004Z-$C"/{started,finished,exitcode}
cat > "$T/fake-agy-run" <<'EOF'
#!/bin/bash
echo '{"event":"init","init":{"conversation_id":"conv-t"}}'
git checkout -q feat/0004-s 2>/dev/null || git checkout -q -b feat/0004-s origin/main
echo '{"event":"step_update","step_update":{"step_type":"tool","state":"DONE","usage":{"total_tokens":1000}}}'
echo '{"event":"result","result":{"status":"SUCCESS","response":"done"}}'
EOF
docker cp "$T/fake-agy-run" "$C:/tmp/agy-run"; docker exec -u root "$C" bash -c 'install -m 755 /tmp/agy-run "$(command -v agy-run)"'
python3 "$T/bin/run-supervisor" "$T/runs/20260101T000004Z-$C" || true
check "supervisor: a remembered slow suite switches the self-check to the changed tests" '[[ "$(python3 -c "import json,sys; print(\",\".join(c.get(\"mode\") for c in json.load(open(sys.argv[1]))[\"self_checks\"]))" "$T/runs/20260101T000004Z-$C/meta.json")" == "changed" ]]'
echo "supervisor test: $([ $fail = 0 ] && echo ALL OK || echo FAILED)"
exit $fail
