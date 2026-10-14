#!/usr/bin/env bash
# scripts/demo.sh — One-command demo launch and teardown script for Agent-Forge.
# Public interface: scripts/demo.sh [up|down|-h|--help]
# Invariants:
# - Default action (no args or 'up'): builds and starts only the 'demo' compose service.
# - Polls http://localhost:8171/ once a second up to 60s until responsive, printing URL on success.
# - Action 'down': stops and removes the demo compose service.
# - Fails fast with actionable error and non-zero exit if compose or polling fails.
# - Rejects unknown arguments with usage message and exit code 1.
# - References demo/docker-compose.yml and requires zero host credentials or tokens.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

COMPOSE_FILE="demo/docker-compose.yml"
SERVICE="demo"
PORT="8171"
URL="http://localhost:${PORT}/"
MAX_WAIT="${DEMO_MAX_WAIT:-60}"

usage() {
  cat <<'EOF'
Usage: scripts/demo.sh [up|down]

One-command launcher and teardown for the Agent-Forge Docker demo.

Commands:
  up         Build and start the demo dashboard container (default)
  down       Stop and remove the demo dashboard container

Options:
  -h, --help Show this help message
EOF
}

ACTION="up"
if [ $# -eq 0 ]; then
  ACTION="up"
elif [ $# -eq 1 ]; then
  case "$1" in
    up)
      ACTION="up"
      ;;
    down)
      ACTION="down"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 1
      ;;
  esac
else
  usage >&2
  exit 1
fi

if [ "$ACTION" = "down" ]; then
  docker compose -f "$COMPOSE_FILE" down
  exit 0
fi

# Action is 'up' (default or explicit)
export DEMO_MODE=1
docker compose -f "$COMPOSE_FILE" up -d --build "$SERVICE"

echo "waiting for the demo to start..."
ready=0
for ((i=1; i<=MAX_WAIT; i++)); do
  if "${CURL_BIN:-curl}" -sf "$URL" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done

if [ "$ready" -ne 1 ]; then
  echo "Error: demo dashboard did not start within ${MAX_WAIT} seconds." >&2
  echo "Check container logs with: docker compose -f $COMPOSE_FILE logs" >&2
  exit 1
fi

echo "Demo dashboard ready. Open $URL in your browser."
