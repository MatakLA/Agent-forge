#!/usr/bin/env bash
# The dashboard's page, tested in a real DOM (jsdom) against the real server on invented demo data (tools/demo_state.py,
# tools/fake-docker): every tab and sheet renders and no script error happens (tests/ui_smoke.js). Needs Docker; pulls
# python:3.12-slim and node:22-alpine and installs jsdom into a throwaway container. ~1 min. Run: tests/test_ui.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; T="$(mktemp -d)"; N="af-uitest-$$"
trap 'docker rm -f "$N" >/dev/null 2>&1; rm -rf "$T"' EXIT
cp -r "$ROOT" "$T/af"; rm -rf "$T/af/.git" "$T/af/runs" "$T/af/projects"
python3 "$ROOT/tools/demo_state.py" "$T/af" >/dev/null
docker run -d --name "$N" --user "$(id -u):$(id -g)" -v "$T/af:/af" -v "$ROOT/tools/fake-docker:/usr/local/bin/docker:ro" -w /af \
  -e FORGE_DIR=/af -e BIND_PORT=8171 -e PYTHONDONTWRITEBYTECODE=1 -e HOME=/tmp python:3.12-slim python3 web/server.py >/dev/null
for i in $(seq 1 40); do docker exec "$N" python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8171/healthz')" 2>/dev/null && break; sleep 1; done
sleep 6
mkdir -p "$T/js" && cp "$ROOT/tests/ui_smoke.js" "$T/js/t.js"
docker run --rm --network "container:$N" --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$T/js:/t" -w /t node:22-alpine \
  sh -c 'npm i -s --no-audit --no-fund jsdom@25 >/dev/null 2>&1 && node t.js'
