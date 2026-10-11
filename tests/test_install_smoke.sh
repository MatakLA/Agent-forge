#!/usr/bin/env bash
# test_install_smoke.sh — hermetic smoke test for fresh install dashboard startup.
# Verifies a fresh installation layout can launch web/server.py without Docker
# or external infrastructure, poll /api/status, and verify a valid response.
#
# Public interface: bash tests/test_install_smoke.sh [--unreachable]
# Invariants:
# - Runs completely hermetically (SANDBOX_TEST=1) without Docker or network calls.
# - Cleans up server process and temporary directory on EXIT trap.
# - Exits 0 when web/server.py is reachable and non-zero when it is not.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TMP_DIR="$(mktemp -d /tmp/af-smoke-sh.XXXXXX)"
SERVER_PID=""

cleanup() {
  local rc=$?
  if [ -n "$SERVER_PID" ]; then
    kill -TERM "$SERVER_PID" 2>/dev/null || true
    for _ in {1..20}; do
      if ! kill -0 "$SERVER_PID" 2>/dev/null; then break; fi
      sleep 0.1
    done
    kill -9 "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$TMP_DIR" 2>/dev/null || (sleep 0.2; rm -rf "$TMP_DIR" 2>/dev/null) || true
  exit "$rc"
}
trap cleanup EXIT INT TERM

# Mimic install layout: etc/, runs/, web/, bin/
mkdir -p "$TMP_DIR/etc" "$TMP_DIR/runs" "$TMP_DIR/bin"
cp -r "$ROOT/web" "$TMP_DIR/web"
cp -r "$ROOT/bin/lib" "$TMP_DIR/bin/lib"
cp "$ROOT/bin/foreman" "$TMP_DIR/bin/foreman"
chmod +x "$TMP_DIR/bin/foreman"

for cfg in limits foreman forge; do
  if [ -f "$ROOT/etc/$cfg.example.conf" ]; then
    cp "$ROOT/etc/$cfg.example.conf" "$TMP_DIR/etc/$cfg.conf"
  elif [ -f "$ROOT/etc/$cfg.conf" ]; then
    cp "$ROOT/etc/$cfg.conf" "$TMP_DIR/etc/$cfg.conf"
  fi
done
if [ -f "$ROOT/etc/team-rules.example.md" ]; then
  cp "$ROOT/etc/team-rules.example.md" "$TMP_DIR/etc/team-rules.md"
fi

export SANDBOX_TEST=1
export SANDBOX_RUNS_DIR="$TMP_DIR/runs"
export FORGE_DIR="$TMP_DIR"
export DASHBOARD_TOKEN=""

# Select random free port
PORT=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
export BIND_PORT="$PORT"

# Check if deliberate unreachable simulation requested
if [ "${1:-}" = "--unreachable" ]; then
  echo "Simulating unreachable server..."
  # Do not launch web/server.py; poll should fail and exit non-zero
else
  # Start web/server.py using python3 web/server.py
  (cd "$TMP_DIR" && exec python3 web/server.py) >/dev/null 2>&1 &
  SERVER_PID=$!
fi

# Wait up to 10 seconds for web/server.py to answer HTTP
START_TIME=$(date +%s)
READY=0
BODY=""

while [ $(( $(date +%s) - START_TIME )) -lt 10 ]; do
  if [ -n "$SERVER_PID" ] && ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "ERROR: web/server.py process exited prematurely" >&2
    exit 1
  fi
  BODY=$(curl -s --max-time 1 "http://127.0.0.1:$PORT/api/status" 2>/dev/null || true)
  if [ -n "$BODY" ]; then
    READY=1
    break
  fi
  sleep 0.2
done

if [ "$READY" -ne 1 ]; then
  echo "ERROR: web/server.py did not respond on port $PORT within 10 seconds" >&2
  exit 1
fi

# Assert the response is valid JSON with key "ok" or "foreman"
python3 -c '
import json, sys
raw = sys.argv[1]
try:
    data = json.loads(raw)
except Exception as err:
    print(f"ERROR: response is not valid JSON: {err}", file=sys.stderr)
    sys.exit(1)
if not isinstance(data, dict):
    print("ERROR: response JSON is not an object", file=sys.stderr)
    sys.exit(1)
if "ok" not in data and "foreman" not in data:
    print("ERROR: response JSON missing required ok or foreman key", file=sys.stderr)
    sys.exit(1)
' "$BODY" || {
  echo "ERROR: /api/status assertion failed on: $BODY" >&2
  exit 1
}

echo "install smoke test (shell): ALL OK"
exit 0
