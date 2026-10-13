#!/usr/bin/env bash
# Integration test for scripts/quickstart-e2e-setup.sh lifecycle.
#
# Public interface:
#   bash tests/test_quickstart_setup.sh
#
# Invariants:
#   - Needs node on PATH to run tests/fixtures/fake-model-api.js; skips cleanly if missing
#     (same caveat as tests/test_runtime_qwen.sh and tests/test_fake_model_api_providers.sh).
#   - Strictly offline: tests up/down against loopback without real Docker or external network.
#   - Asserts all six KEY=VALUE pairs emitted by up, verifies server response, and confirms down cleans up.
#   - down is verified to be idempotent (second call is a clean exit 0 no-op).
#   - Confirms QUICKSTART_FAKE_DOCKER_DIR only points to tools/fake-docker and invokes no real Docker binary.
#   - All assertions use explicit || exit 1 with set -euo pipefail.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETUP="$ROOT/scripts/quickstart-e2e-setup.sh"

[ -f "$SETUP" ] || exit 1
[ -x "$SETUP" ] || exit 1

# 1. Test --help flag
HELP_OUT=$(bash "$SETUP" --help) || exit 1
echo "$HELP_OUT" | grep -q "Usage:" || exit 1
echo "$HELP_OUT" | grep -q "quickstart-e2e-setup.sh up" || exit 1
echo "$HELP_OUT" | grep -q "quickstart-e2e-setup.sh down" || exit 1

# 2. Test unknown subcommand failure
ERR_SUB=$(bash "$SETUP" unknown_subcmd 2>&1 && exit 1 || true)
echo "$ERR_SUB" | grep -qi "unknown command" || exit 1

# 3. Test down without required arguments failure
ERR_DOWN_ARGS=$(bash "$SETUP" down 2>&1 && exit 1 || true)
echo "$ERR_DOWN_ARGS" | grep -qi "Usage:" || exit 1

# 4. Test missing node failure mode (fails closed with clear stderr)
if ! command -v node >/dev/null 2>&1; then
  ERR_NODE=$(bash "$SETUP" up 2>&1 && exit 1 || true)
  echo "$ERR_NODE" | grep -qi "node is required on PATH" || exit 1
  echo "SKIP: node is not available on host; fake-model-api.js requires node"
  exit 0
fi

# When node is available on PATH:
# 5. Run `up` and capture KEY=VALUE lines
UP_OUT=$(bash "$SETUP" up) || exit 1

# Verify all six KEY=VALUE lines exist in output
echo "$UP_OUT" | grep -qE '^ANTHROPIC_API_BASE=http://127.0.0.1:[0-9]+$' || exit 1
echo "$UP_OUT" | grep -qE '^OPENAI_API_BASE=http://127.0.0.1:[0-9]+/v1$' || exit 1
echo "$UP_OUT" | grep -qE '^GEMINI_API_BASE=http://127.0.0.1:[0-9]+$' || exit 1
echo "$UP_OUT" | grep -qE '^QUICKSTART_RUNS_DIR=' || exit 1
echo "$UP_OUT" | grep -qE '^QUICKSTART_FAKE_API_PID=[0-9]+$' || exit 1
echo "$UP_OUT" | grep -qE '^QUICKSTART_FAKE_DOCKER_DIR=' || exit 1

# Eval the output to populate variables in caller shell
eval "$UP_OUT" || exit 1

# Verify scratch directory exists
[ -n "${QUICKSTART_RUNS_DIR:-}" ] || exit 1
[ -d "$QUICKSTART_RUNS_DIR" ] || exit 1

# Verify fake API PID is currently running
[ -n "${QUICKSTART_FAKE_API_PID:-}" ] || exit 1
kill -0 "$QUICKSTART_FAKE_API_PID" 2>/dev/null || exit 1

# Verify fake-docker directory and executable shim
[ -n "${QUICKSTART_FAKE_DOCKER_DIR:-}" ] || exit 1
[ -d "$QUICKSTART_FAKE_DOCKER_DIR" ] || exit 1
[ -x "$QUICKSTART_FAKE_DOCKER_DIR/docker" ] || exit 1

# Verify that docker on PATH executes fake-docker and returns simulated workers
DOCKER_EXEC=$(PATH="$QUICKSTART_FAKE_DOCKER_DIR:$PATH" which docker) || exit 1
[ "$DOCKER_EXEC" = "$QUICKSTART_FAKE_DOCKER_DIR/docker" ] || exit 1

DOCKER_PS=$(PATH="$QUICKSTART_FAKE_DOCKER_DIR:$PATH" docker ps) || exit 1
echo "$DOCKER_PS" | grep -q "worker-1" || exit 1
echo "$DOCKER_PS" | grep -q "worker-4" || exit 1

# 6. Confirm fake server answers on all configured endpoints
RESP_ANTHROPIC=$(curl -s -X POST -H "Content-Type: application/json" -d '{"messages":[]}' "$ANTHROPIC_API_BASE/v1/messages") || exit 1
echo "$RESP_ANTHROPIC" | grep -q "msg_x" || exit 1

RESP_OPENAI=$(curl -s -X POST -H "Content-Type: application/json" -d '{"messages":[]}' "$OPENAI_API_BASE/chat/completions") || exit 1
echo "$RESP_OPENAI" | grep -q "chatcmpl" || exit 1

RESP_GEMINI=$(curl -s -X POST -H "Content-Type: application/json" -d '{"contents":[]}' "$GEMINI_API_BASE/:generateContent") || exit 1
echo "$RESP_GEMINI" | grep -q "gemini-fake" || exit 1

# 7. Run `down` with printed values
bash "$SETUP" down "$QUICKSTART_RUNS_DIR" "$QUICKSTART_FAKE_API_PID" || exit 1

# Assert pid is no longer running
if kill -0 "$QUICKSTART_FAKE_API_PID" 2>/dev/null; then
  echo "ERROR: fake API process $QUICKSTART_FAKE_API_PID is still running after down" >&2
  exit 1
fi

# Assert scratch directory is gone
if [ -d "$QUICKSTART_RUNS_DIR" ]; then
  echo "ERROR: scratch dir $QUICKSTART_RUNS_DIR still exists after down" >&2
  exit 1
fi

# 8. Assert `down` is idempotent (second invocation exits 0)
bash "$SETUP" down "$QUICKSTART_RUNS_DIR" "$QUICKSTART_FAKE_API_PID" || exit 1

# 9. Test port bind conflict failure
FIXTURE_PORT=$(grep -oE '\.listen\([0-9]+' "$ROOT/tests/fixtures/fake-model-api.js" | grep -oE '[0-9]+' || echo "8099")
python3 -c "
import socket, sys, time
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', int(sys.argv[1])))
s.listen(1)
time.sleep(3)
s.close()
" "$FIXTURE_PORT" &
OCC_PID=$!
sleep 0.1

PORT_FAIL_OUT=$(bash "$SETUP" up 2>&1 && exit 1 || true)
echo "$PORT_FAIL_OUT" | grep -qiE "bound|already in use|died" || exit 1

kill -9 "$OCC_PID" 2>/dev/null || true
wait "$OCC_PID" 2>/dev/null || true

echo "quickstart setup tests: ALL OK"
