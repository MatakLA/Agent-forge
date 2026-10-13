#!/usr/bin/env bash
# tests/test_demo_mode.sh — Docker-based end-to-end smoke test for the demo setup.
# Note: This script requires Docker and is not executed by the gate (like ui_smoke.js needs node).
# Only tests/test_demo_mode.py is gate-verified.
#
# Invariants:
# - Starts demo via docker compose -f demo/docker-compose.yml up -d --build.
# - Polls port 8171 for up to 60s until responsive.
# - Asserts HTTP 200 and recognizable marker string in the HTML (e.g. '<title>Agent-Forge</title>').
# - Asserts docker compose -f demo/docker-compose.yml ps lists exactly one running container.
# - Stops and tears down containers cleanly via docker compose -f demo/docker-compose.yml down.
# - Propagates non-zero exit on any failed step without swallowed pipelines.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Gate containers have no Docker (same limitation as node for ui_smoke.js).
# Skip gracefully when Docker or Docker Compose is unavailable.
if ! command -v docker >/dev/null 2>&1; then
  echo "SKIP: Docker is not available. tests/test_demo_mode.sh requires Docker and is not run by the gate."
  exit 0
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "SKIP: Docker Compose is not available. tests/test_demo_mode.sh requires Docker and is not run by the gate."
  exit 0
fi

if ! docker info >/dev/null 2>&1; then
  echo "SKIP: Docker daemon is not accessible. tests/test_demo_mode.sh requires Docker and is not run by the gate."
  exit 0
fi

COMPOSE_FILE="demo/docker-compose.yml"
PORT="${BIND_PORT:-8171}"
TARGET_URL="http://localhost:${PORT}/"

CLEANED_UP=0
cleanup() {
  local rc=$?
  if [ "$CLEANED_UP" -eq 0 ]; then
    CLEANED_UP=1
    echo "==> Cleaning up demo containers on exit..." >&2
    docker compose -f "$COMPOSE_FILE" down >/dev/null 2>&1 || true
  fi
  exit "$rc"
}
trap cleanup EXIT INT TERM

echo "==> Starting demo via docker compose..."
docker compose -f "$COMPOSE_FILE" up -d --build || exit 1

echo "==> Polling port $PORT for up to 60s..."
ready=0
for ((i=1; i<=60; i++)); do
  if curl -fsS "$TARGET_URL" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done

if [ "$ready" -ne 1 ]; then
  echo "FAIL: demo dashboard failed to respond on port $PORT within 60s" >&2
  docker compose -f "$COMPOSE_FILE" logs >&2 || true
  exit 1
fi
echo "  ✔ Demo dashboard is answering on port $PORT"

echo "==> Curling dashboard root and asserting HTTP 200 and marker string..."
TMP_RESP="$(mktemp)"
HTTP_STATUS="$(curl -s -o "$TMP_RESP" -w "%{http_code}" "$TARGET_URL" || exit 1)"
if [ "$HTTP_STATUS" != "200" ]; then
  echo "FAIL: expected HTTP 200 from $TARGET_URL, got $HTTP_STATUS" >&2
  rm -f "$TMP_RESP"
  exit 1
fi

MARKER="<title>Agent-Forge</title>"
if ! grep -q "$MARKER" "$TMP_RESP"; then
  echo "FAIL: HTML response missing recognizable marker string '$MARKER'" >&2
  rm -f "$TMP_RESP"
  exit 1
fi
rm -f "$TMP_RESP"
echo "  ✔ Dashboard root returned HTTP 200 with recognizable marker '$MARKER'"

echo "==> Asserting docker compose ps lists exactly one running container..."
docker compose -f "$COMPOSE_FILE" ps || exit 1
RUNNING_CONTAINERS="$(docker compose -f "$COMPOSE_FILE" ps -q || exit 1)"
CONTAINER_COUNT="$(printf '%s' "$RUNNING_CONTAINERS" | wc -w)"
if [ "$CONTAINER_COUNT" -ne 1 ]; then
  echo "FAIL: expected exactly 1 running container in compose, found $CONTAINER_COUNT" >&2
  exit 1
fi
echo "  ✔ Exactly one running container listed in docker compose ps"

echo "==> Tearing down demo containers..."
CLEANED_UP=1
docker compose -f "$COMPOSE_FILE" down || exit 1
echo "  ✔ Demo containers stopped and removed"

echo "demo mode end-to-end test: ALL OK"
