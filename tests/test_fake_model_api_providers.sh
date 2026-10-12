#!/usr/bin/env bash
# Integration test runner for OpenAI and Gemini orchestrator providers against fake-model-api.js.
#
# Public interface: bash tests/test_fake_model_api_providers.sh
# Invariants:
# - Strictly offline: launches fake model API fixture on local loopback port with exit trap cleanup.
# - If node is not installed, skips cleanly with clear explanation (no duplicate mock server fallback).
# - Configures test environment (OPENAI_API_BASE, OPENAI_API_KEY, GEMINI_API_KEY) pointing to local server.
# - Executes unittest suite propagating test exit status explicitly (|| exit 1).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Verify node availability; per project architecture, do not substitute hand-rolled mock logic
if ! command -v node >/dev/null 2>&1; then
  echo "SKIP: node is not available on host; fake-model-api.js requires node"
  exit 0
fi

FIXTURE="$ROOT/tests/fixtures/fake-model-api.js"
if [ ! -f "$FIXTURE" ]; then
  echo "ERROR: fixture $FIXTURE not found" >&2
  exit 1
fi

# Read fixture's actual port from the source (.listen(8099))
PORT=$(grep -oE '\.listen\([0-9]+' "$FIXTURE" | grep -oE '[0-9]+' || echo "8099")

T="$(mktemp -d)"
SERVER_PID=""

cleanup() {
  if [ -n "${SERVER_PID:-}" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$T"
}
trap cleanup EXIT

# Launch the real fake-model-api.js fixture directly with node
node "$FIXTURE" > "$T/server.log" 2>&1 &
SERVER_PID=$!

# Wait for fake server to accept connections on port
for _ in $(seq 1 50); do
  python3 -c "import socket; s=socket.socket(); s.connect(('127.0.0.1', $PORT)); s.close()" 2>/dev/null && break
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "ERROR: fake-model-api.js process died unexpectedly:" >&2
    cat "$T/server.log" >&2
    exit 1
  fi
  sleep 0.05
done

# Configure test environment pointing at local fake server
export OPENAI_API_BASE="http://127.0.0.1:$PORT/v1"
export OPENAI_API_KEY="test-key"
export GEMINI_API_KEY="test-key"
export OPENAI_MODEL="gpt-4o"
export GEMINI_MODEL="gemini-1.5-pro"
export FAKE_MODEL_PORT="$PORT"

# Execute test suite and propagate exit status explicitly
python3 -m unittest tests/test_fake_model_api_providers.py || exit 1
