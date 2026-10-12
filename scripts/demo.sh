#!/usr/bin/env bash
# scripts/demo.sh — One-command demo launch script for Agent-Forge.
# Public interface: scripts/demo.sh [--port N] [-h|--help]
# Invariants:
# - Self-contained offline demo using synthetic data from tools/demo_state.py.
# - Fails fast with a clear message and help link when Docker or Docker Compose is missing.
# - Cleans up temporary workspace directory on exit.
# - References demo/docker-compose.yml and DEMO_DATA_DIR exactly without touching real infrastructure.
set -euo pipefail
umask 077

ROOT="${FORGE_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$ROOT"

step() { printf '\n==> %s\n' "$*"; }
ok()   { printf '  ✔ %s\n' "$*"; }
die()  { printf '\n✘ %s\n' "$1"; shift; for h in "$@"; do printf '    → %s\n' "$h"; done; exit 1; }

PORT="${BIND_PORT:-${PORT:-8171}}"

while [ $# -gt 0 ]; do
  case "$1" in
    --port)
      [ $# -gt 1 ] || die "--port requires a port argument"
      PORT="$2"
      shift 2
      ;;
    --port=*)
      PORT="${1#*=}"
      shift
      ;;
    -h|--help)
      cat <<'EOF'
Usage: scripts/demo.sh [--port N]

One-command demo launcher for Agent-Forge. Populates a temporary workspace
with synthetic goals, tasks, events, and runs, then starts the demo dashboard
in Docker.

Options:
  --port N       Host port for the demo dashboard (default: 8171)
  -h, --help     Show this help message

Prerequisites:
  Docker and Docker Compose: https://docs.docker.com/get-docker/
EOF
      exit 0
      ;;
    *)
      die "Unknown option: $1" "Use --help for usage information"
      ;;
  esac
done

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
  die "Invalid port number: $PORT (must be between 1 and 65535)"
fi

step "Checking prerequisites"
if ! command -v docker >/dev/null 2>&1; then
  die "Docker is not installed." \
      "Install Docker and Docker Compose before running the demo: https://docs.docker.com/get-docker/"
fi

if ! docker compose version >/dev/null 2>&1; then
  die "Docker Compose is not available." \
      "Install the Docker Compose plugin: https://docs.docker.com/get-docker/"
fi
ok "Docker and Docker Compose are available"

step "Preparing demo workspace"
workdir="$(mktemp -d)"
cleanup() {
  local rc=$?
  if [ -n "${workdir:-}" ] && [ -d "$workdir" ]; then
    rm -rf "$workdir"
  fi
  exit "$rc"
}
trap cleanup EXIT INT TERM

for d in bin etc roles prompts docs examples web; do
  if [ -d "$ROOT/$d" ]; then
    cp -r "$ROOT/$d" "$workdir/"
  fi
done
if [ -f "$ROOT/VERSION" ]; then
  cp "$ROOT/VERSION" "$workdir/"
fi
mkdir -p "$workdir/etc" "$workdir/docs"

for cfg in limits foreman forge; do
  if [ -f "$workdir/etc/$cfg.example.conf" ] && [ ! -f "$workdir/etc/$cfg.conf" ]; then
    cp "$workdir/etc/$cfg.example.conf" "$workdir/etc/$cfg.conf"
  fi
  if [ -f "$workdir/etc/$cfg.conf" ]; then
    chmod 600 "$workdir/etc/$cfg.conf"
  fi
done
if [ -f "$workdir/etc/team-rules.example.md" ] && [ ! -f "$workdir/etc/team-rules.md" ]; then
  cp "$workdir/etc/team-rules.example.md" "$workdir/etc/team-rules.md"
  chmod 600 "$workdir/etc/team-rules.md"
fi
chmod 700 "$workdir/etc"
ok "Workspace prepared at $workdir"

step "Populating synthetic demo state"
python3 tools/demo_state.py "$workdir"
ok "Demo state populated in $workdir/runs/"

step "Starting demo"
if [ "$PORT" = "8171" ]; then
  echo "Demo running at http://localhost:8171 (Ctrl-C to stop)"
else
  echo "Demo running at http://localhost:$PORT (Ctrl-C to stop)"
fi

export BIND_PORT="$PORT"
export PORT="$PORT"
DEMO_DATA_DIR="$workdir" docker compose -f demo/docker-compose.yml up --build
