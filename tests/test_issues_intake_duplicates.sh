#!/usr/bin/env bash
# End-to-end integration test for duplicate issue detection in bin/issues-intake against fake GitLab forge API.
# Public interface: bash tests/test_issues_intake_duplicates.sh [RUNS_DIR]
# Invariants:
# - Strictly offline: starts tests/fixtures/fake-forge-api-gitlab.js on a dynamically chosen free port.
# - Points bin/issues-intake at the fixture with GitLab configuration.
# - Runs intake step twice consecutively against the same temporary offline runs dir.
# - Asserts exactly one goal exists afterward (not zero, not two), failing loudly and non-zero otherwise.
# - Guaranteed cleanup of the fixture server on exit under all termination paths.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Verify node availability; per project architecture, do not substitute hand-rolled mock logic
if ! command -v node >/dev/null 2>&1; then
  echo "SKIP: node is not available on host; fake-forge-api-gitlab.js requires node"
  exit 0
fi

FIXTURE="$ROOT/tests/fixtures/fake-forge-api-gitlab.js"
if [ ! -f "$FIXTURE" ]; then
  echo "ERROR: fixture $FIXTURE not found" >&2
  exit 1
fi

# Dynamically allocate an available ephemeral port
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')

TMP_RUNS="${1:-}"
CLEANUP_RUNS=0
if [ -z "$TMP_RUNS" ]; then
  TMP_RUNS="$(mktemp -d)"
  CLEANUP_RUNS=1
fi
mkdir -p "$TMP_RUNS"

SERVER_LOG="$(mktemp)"
SERVER_PID=""

cleanup() {
  local exit_code=$?
  if [ -n "${SERVER_PID:-}" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -f "$SERVER_LOG" 2>/dev/null || true
  if [ "$CLEANUP_RUNS" -eq 1 ] && [ -d "$TMP_RUNS" ]; then
    rm -rf "$TMP_RUNS" 2>/dev/null || true
  fi
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

# Start the fake GitLab API server fixture
node "$FIXTURE" "$PORT" > "$SERVER_LOG" 2>&1 &
SERVER_PID=$!

# Wait for server to listen
for _ in $(seq 1 50); do
  python3 -c "import socket; s=socket.socket(); s.connect(('127.0.0.1', $PORT)); s.close()" 2>/dev/null && break
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "ERROR: fake-forge-api-gitlab.js process died unexpectedly:" >&2
    cat "$SERVER_LOG" >&2
    exit 1
  fi
  sleep 0.05
done

# Configure intake environment pointing at the fake GitLab fixture and temp runs dir
export SANDBOX_TEST="1"
export SANDBOX_RUNS_DIR="$TMP_RUNS"
export FORGE_KIND="gitlab"
export FORGE_REPO="acme/shop"
export FORGE_UPSTREAM_TOKEN="glpat-dummy-test-token"
export AF_UPSTREAM_API="http://127.0.0.1:$PORT/api/v4"

# First intake run: should fetch the issue and queue it as a goal
echo "Running intake step 1..."
python3 "$ROOT/bin/issues-intake" || {
  echo "ERROR: first intake run failed" >&2
  exit 1
}

# Second intake run: receives identical issue, should detect it as seen and skip it
echo "Running intake step 2..."
python3 "$ROOT/bin/issues-intake" || {
  echo "ERROR: second intake run failed" >&2
  exit 1
}

# Verify goals directory and goal count
GOALS_DIR="$TMP_RUNS/foreman/goals"
if [ ! -d "$GOALS_DIR" ]; then
  echo "ERROR: goals directory $GOALS_DIR was not created" >&2
  exit 1
fi

GOAL_COUNT=$(find "$GOALS_DIR" -maxdepth 1 -name "*.json" | wc -l)
echo "Goals count: $GOAL_COUNT"

if [ "$GOAL_COUNT" -ne 1 ]; then
  echo "ERROR: expected exactly 1 goal, found $GOAL_COUNT (duplicate detection failed)" >&2
  exit 1
fi

# Verify seen file was written and contains the issue ID
SEEN_FILE="$TMP_RUNS/foreman/issues-intake.json"
if [ ! -f "$SEEN_FILE" ]; then
  echo "ERROR: seen file $SEEN_FILE was not created" >&2
  exit 1
fi

python3 -c "
import json, sys
with open('$SEEN_FILE', 'r', encoding='utf-8') as f:
    data = json.load(f)
seen = data.get('seen', [])
if len(seen) != 1 or 5001 not in seen:
    sys.stderr.write(f'ERROR: unexpected seen contents: {seen}\n')
    sys.exit(1)
" || exit 1

echo "ok: duplicate detection passed (exactly 1 goal queued across 2 intake runs)"
