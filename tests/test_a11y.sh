#!/usr/bin/env bash
# Accessibility of the dashboard: the real server on invented demo data (tools/demo_state.py, tools/fake-docker), audited
# by axe-core in headless Chromium, plus keyboard checks (tests/a11y_audit.js). Needs Docker; pulls python:3.12-slim and
# node:22-alpine (puppeteer-core is an ES module and needs Node >= 22.12), builds a small image on top of it with Alpine's
# Chromium (once, then reused as af-a11y-chrome) and installs puppeteer-core and axe-core into a throwaway container. ~2 min.
# Run: tests/test_a11y.sh   (AF_REPORT=1 lists every violating node)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; T="$(mktemp -d)"; N="af-a11y-$$"
trap 'docker rm -f "$N" >/dev/null 2>&1; rm -rf "$T"' EXIT
DOCKERFILE='FROM node:22-alpine
RUN apk add --no-cache chromium ttf-freefont font-noto-emoji
'
CHROME_IMG="af-a11y-chrome:$(printf %s "$DOCKERFILE" | cksum | cut -d' ' -f1)"
docker image inspect "$CHROME_IMG" >/dev/null 2>&1 || printf %s "$DOCKERFILE" | docker build -q -t "$CHROME_IMG" - >/dev/null
cp -r "$ROOT" "$T/af"; rm -rf "$T/af/.git" "$T/af/runs" "$T/af/projects"
python3 "$ROOT/tools/demo_state.py" "$T/af" >/dev/null
docker run -d --name "$N" --user "$(id -u):$(id -g)" -v "$T/af:/af" -v "$ROOT/tools/fake-docker:/usr/local/bin/docker:ro" -w /af \
  -e FORGE_DIR=/af -e BIND_PORT=8171 -e PYTHONDONTWRITEBYTECODE=1 -e HOME=/tmp python:3.12-slim python3 web/server.py >/dev/null
for i in $(seq 1 40); do docker exec "$N" python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8171/healthz')" 2>/dev/null && break; sleep 1; done
sleep 6
mkdir -p "$T/js" && cp "$ROOT/tests/a11y_audit.js" "$T/js/t.js" && cp "$ROOT"/tests/node/a11y/package*.json "$T/js/"
docker run --rm --network "container:$N" --user "$(id -u):$(id -g)" -e HOME=/tmp -e AF_REPORT="${AF_REPORT:-}" -e AF_CHROME=/usr/bin/chromium-browser \
  -v "$T/js:/t" -w /t --entrypoint sh "$CHROME_IMG" \
  -c 'npm ci -s --no-audit --no-fund >/dev/null 2>&1 && node t.js'
