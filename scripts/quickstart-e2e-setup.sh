#!/usr/bin/env bash
# Quickstart offline E2E setup and teardown script: fake model API + no-Docker shim.
#
# Public interface:
#   scripts/quickstart-e2e-setup.sh up
#   scripts/quickstart-e2e-setup.sh down <scratch_dir> <pid>
#   scripts/quickstart-e2e-setup.sh --help
#
# Invariants:
#   - Needs node on PATH to run tests/fixtures/fake-model-api.js; fails closed with clear stderr if missing.
#   - Strictly offline: binds fake-model-api.js on localhost loopback port without network egress.
#   - Prepares fake-docker shim directory containing tools/fake-docker as `docker` so no real Docker daemon is invoked.
#   - down is idempotent: stops fake API process and removes scratch dir, exiting 0 even if already stopped.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE="$ROOT/tests/fixtures/fake-model-api.js"
FAKE_DOCKER="$ROOT/tools/fake-docker"

show_help() {
  cat <<'EOF'
Usage:
  scripts/quickstart-e2e-setup.sh up
      Picks a free localhost port, starts fake-model-api.js in the background,
      prepares tools/fake-docker on a shim PATH, and prints six KEY=VALUE lines
      eval-able by the caller.

  scripts/quickstart-e2e-setup.sh down <scratch_dir> <pid>
      Kills the fake-model-api process (if still running) and removes the scratch dir.
      Idempotent; exits 0 even if already stopped.

  scripts/quickstart-e2e-setup.sh --help
      Show this help message.
EOF
}

cmd_up() {
  # 1. Fail closed if node is not available on PATH
  if ! command -v node >/dev/null 2>&1; then
    echo "ERROR: node is required on PATH to run tests/fixtures/fake-model-api.js" >&2
    exit 1
  fi

  # 2. Verify fixture and fake-docker exist
  if [ ! -f "$FIXTURE" ]; then
    echo "ERROR: fixture not found: $FIXTURE" >&2
    exit 1
  fi
  if [ ! -f "$FAKE_DOCKER" ]; then
    echo "ERROR: fake-docker tool not found: $FAKE_DOCKER" >&2
    exit 1
  fi

  # 3. Read port from fixture (.listen(8099))
  local port
  port=$(grep -oE '\.listen\([0-9]+' "$FIXTURE" | grep -oE '[0-9]+' || echo "8099")

  # 4. Verify port can be bound on loopback
  if ! python3 -c "import socket, sys; s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('127.0.0.1', int(sys.argv[1]))); s.close()" "$port" 2>/dev/null; then
    echo "ERROR: port $port cannot be bound on 127.0.0.1 (already in use)" >&2
    exit 1
  fi

  # 5. Create scratch directory
  local scratch_dir
  scratch_dir="$(mktemp -d)"

  # 6. Create fake-docker shim directory
  local fake_docker_dir="$scratch_dir/docker-shim"
  mkdir -p "$fake_docker_dir"
  cp "$FAKE_DOCKER" "$fake_docker_dir/docker"
  chmod +x "$fake_docker_dir/docker"

  # 7. Start fake-model-api.js in the background
  local server_pid
  node "$FIXTURE" > "$scratch_dir/fake-model-api.log" 2>&1 &
  server_pid=$!

  # 8. Wait for fake server to accept connections
  local ready=0
  for _ in $(seq 1 50); do
    if python3 -c "import socket, sys; s = socket.socket(); s.connect(('127.0.0.1', int(sys.argv[1]))); s.close()" "$port" 2>/dev/null; then
      ready=1
      break
    fi
    if ! kill -0 "$server_pid" 2>/dev/null; then
      echo "ERROR: fake-model-api.js process died unexpectedly on port $port:" >&2
      cat "$scratch_dir/fake-model-api.log" >&2 || true
      rm -rf "$scratch_dir"
      exit 1
    fi
    sleep 0.05
  done

  if [ "$ready" -ne 1 ]; then
    echo "ERROR: fake-model-api.js failed to bind port $port within timeout" >&2
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
    rm -rf "$scratch_dir"
    exit 1
  fi

  # 9. Output six eval-able KEY=VALUE lines
  printf 'ANTHROPIC_API_BASE=http://127.0.0.1:%s\n' "$port"
  printf 'OPENAI_API_BASE=http://127.0.0.1:%s/v1\n' "$port"
  printf 'GEMINI_API_BASE=http://127.0.0.1:%s\n' "$port"
  printf 'QUICKSTART_RUNS_DIR=%s\n' "$scratch_dir"
  printf 'QUICKSTART_FAKE_API_PID=%s\n' "$server_pid"
  printf 'QUICKSTART_FAKE_DOCKER_DIR=%s\n' "$fake_docker_dir"
}

cmd_down() {
  local scratch_dir="${1:-}"
  local pid="${2:-}"

  # Kill fake API process if still running
  if [ -n "$pid" ] && [[ "$pid" =~ ^[0-9]+$ ]]; then
    if kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid" 2>/dev/null || true
      for _ in $(seq 1 20); do
        if ! kill -0 "$pid" 2>/dev/null; then
          break
        fi
        sleep 0.05
      done
      kill -9 "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  fi

  # Remove scratch directory
  if [ -n "$scratch_dir" ] && [ "$scratch_dir" != "/" ] && [ -d "$scratch_dir" ]; then
    rm -rf "$scratch_dir"
  fi

  exit 0
}

case "${1:-}" in
  up)
    cmd_up
    ;;
  down)
    if [ $# -lt 2 ]; then
      echo "Usage: $0 down <scratch_dir> <pid>" >&2
      exit 1
    fi
    cmd_down "${2:-}" "${3:-}"
    ;;
  --help|-h|help)
    show_help
    exit 0
    ;;
  *)
    echo "ERROR: unknown command '${1:-}'. See '$0 --help'." >&2
    exit 1
    ;;
esac
