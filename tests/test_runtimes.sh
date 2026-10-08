#!/usr/bin/env bash
# End-to-end test of the API runtimes (claude, codex, gemini, compat, aider) without a key, network or cost: for each one, a
# container from the API worker image runs the REAL agent CLI through worker/af-agent against tests/fixtures/fake-model-api.js,
# under the real bin/run-supervisor. compat and aider use the fake API as "the owner's model server" (COMPAT_BASE_URL).
# Checks: the run ends ok, tokens / tool calls / conversation id / cost are read from the CLI's own event stream, the agent's file
# change is committed and pushed to the task branch, a second run resumes the same conversation, and the CLI tried to reach
# nothing else (every other connection goes to tests/fixtures/logging-proxy.py, which logs and refuses it).
# Needs Docker and the image agent-forge-worker-api:1 (bin/worker-up builds it). ~2-3 min.
# Run: tests/test_runtimes.sh [claude codex gemini compat aider]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMG=agent-forge-worker-api:1
docker image inspect "$IMG" >/dev/null 2>&1 || { echo "SKIP: image $IMG not built (docker compose -f worker/docker-compose.api.yml build)"; exit 0; }
if [ $# -gt 0 ]; then RTS=("$@"); else RTS=(claude codex gemini compat aider); fi
T="$(mktemp -d)"; CS=()
cleanup() { [ -n "${KEEP:-}" ] && { echo "kept $T ${CS[*]}"; return; }; for c in "${CS[@]}"; do docker rm -f "$c" >/dev/null 2>&1; done; rm -rf "$T"; }
trap cleanup EXIT
cp -r "$ROOT/bin" "$T/"; mkdir -p "$T/etc" "$T/runs"
printf 'RUN_TIMEOUT_MIN=10\nRUN_TOKEN_CAP=1500000\nMAX_PARALLEL=4\nPOLL_SEC=1\nAPI_RUN_BUDGET_USD=1\n' > "$T/etc/limits.conf"
# the model server of compat / aider: the fake API; a price so the cost estimate is exercised; a test command that passes
printf 'COMPAT_BASE_URL=http://127.0.0.1:8099/v1\nPRICE_COMPAT=1,0.1,10\n' >> "$T/etc/limits.conf"
printf 'TEST_CMD=true\n' > "$T/etc/foreman.conf"
fail=0
check() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }
meta() { python3 -c "import json,sys; m=json.load(open(sys.argv[1])); print(m.get(sys.argv[2]))" "$1/meta.json" "$2"; }

for RT in "${RTS[@]}"; do
  C="af-rt-$RT-$$"; CS+=("$C")
  # codex / compat: an explicit model - against the fake API the CLI has no metadata for its default model and offers no tools
  MODEL=""; [[ "$RT" =~ ^(codex|compat)$ ]] && MODEL=gpt-5.1-codex; [ "$RT" = aider ] && MODEL=qwen3-coder
  docker run -d --name "$C" --network none --label "agent-forge.runtime=$RT" \
    -e HTTPS_PROXY=http://127.0.0.1:3128 -e HTTP_PROXY=http://127.0.0.1:3128 -e https_proxy=http://127.0.0.1:3128 \
    -e http_proxy=http://127.0.0.1:3128 -e NO_PROXY=127.0.0.1,localhost -e no_proxy=127.0.0.1,localhost \
    --entrypoint sleep "$IMG" infinity >/dev/null
  docker cp "$ROOT/tests/fixtures/fake-model-api.js" "$C:/tmp/fake-model-api.js"
  docker cp "$ROOT/tests/fixtures/logging-proxy.py" "$C:/tmp/logging-proxy.py"
  docker exec -u root "$C" sh -c 'mkdir -p /run/secrets && printf fake-key-for-tests > /run/secrets/api-key && chmod 644 /run/secrets/api-key'
  docker exec -d "$C" sh -c 'node /tmp/fake-model-api.js > /tmp/fake.log 2>&1'
  docker exec -d "$C" sh -c 'python3 /tmp/logging-proxy.py'
  docker exec "$C" bash -c 'set -e; git init -q --bare -b main /tmp/origin.git; git clone -q /tmp/origin.git /home/worker/playground 2>/dev/null
    cd /home/worker/playground; printf "\"\"\"Base.\"\"\"\n" > base.py; git add -A; git -c user.name=o -c user.email=o@o commit -qm init; git push -q origin HEAD:main
    git config user.name worker-9; git config user.email w@w'
  conv=""
  for round in 1 2; do
    R="$T/runs/2026010${round}T000000Z-$C"; mkdir -p "$R"
    echo "# Task 0001-x: write hello.txt" > "$R/prompt.md"
    cat > "$R/meta.json" <<J
{"run_id": "$(basename "$R")", "worker": "$C", "task": "0001-x.md", "test": false, "runner": null, "runtime": "$RT", "model": "$MODEL",
 "budget_usd": 1, "project": "", "repo": "sandbox/playground", "branch": "feat/0001-x", "conversation": "$conv", "self_check": 1,
 "limits": {"timeout_min": 10, "token_cap": 1500000}, "quota_before": [], "dispatched": "x"}
J
    SANDBOX_TEST=1 AF_TEST_BASE_URL=http://127.0.0.1:8099 python3 "$T/bin/run-supervisor" "$R" >/dev/null 2>&1 || true
    if [ -n "${DEBUG:-}" ]; then echo "--- $RT round $round: stderr"; tail -5 "$R/stderr.log"; echo "--- stream"; head -c 2500 "$R/stream.jsonl"; echo; tail -3 "$R/supervisor.log" 2>/dev/null || true; fi
    if [ "$round" = 1 ]; then
      check "$RT: run ok, tokens and tool calls read from its stream" '[[ "$(meta "$R" reason)" == ok && "$(meta "$R" tokens)" -gt 0 && "$(meta "$R" tool_calls)" -ge 1 ]]'
      conv="$(meta "$R" conversation_id)"
      check "$RT: conversation id found ($conv)" '[[ -n "$conv" && "$conv" != None ]]'
      check "$RT: cost recorded ($(meta "$R" cost_usd) USD)" 'python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) > 0 else 1)" "$(meta "$R" cost_usd)"'
      check "$RT: the agent's hello.txt was committed and pushed to the task branch" \
        '[[ "$(docker exec "$C" git -C /tmp/origin.git show feat/0001-x:hello.txt 2>/dev/null)" == hello ]]'
    else
      check "$RT: a rework resumes the same conversation" '[[ "$(meta "$R" reason)" == ok && "$(meta "$R" conversation_id)" == "$conv" ]]'
    fi
  done
  check "$RT: the fake API saw the requests (the CLI really ran)" '[[ "$(docker exec "$C" grep -c POST /tmp/fake.log)" -ge 2 ]]'
  tried="$(docker exec "$C" sh -c 'cat /tmp/proxy.log 2>/dev/null; true' | sort -u | tr '\n' ' ')"
  check "$RT: contacted nothing but its API${tried:+ (tried: $tried)}" '[[ -z "$tried" ]]'
done
echo "runtime tests: $([ $fail = 0 ] && echo ALL OK || echo FAILED)"
exit $fail
