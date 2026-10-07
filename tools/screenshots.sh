#!/usr/bin/env bash
# Usage: tools/screenshots.sh [out-dir]    (default docs/img)  - needs Docker
# Photographs the dashboard on invented demo data (tools/demo_state.py): a copy of this checkout gets the demo state, the
# dashboard runs on it in a throwaway container (with a fake `docker` that lists four workers and their quota), and headless
# Chromium takes the pictures. No real install, account or data is involved.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; OUT="$(cd "$ROOT" && mkdir -p "${1:-docs/img}" && cd "${1:-docs/img}" && pwd)"
T="$(mktemp -d)"; N="af-shots-$$"
trap 'docker rm -f "$N" >/dev/null 2>&1; rm -rf "$T"' EXIT
cp -r "$ROOT" "$T/af"; rm -rf "$T/af/.git" "$T/af/runs" "$T/af/projects"
python3 "$ROOT/tools/demo_state.py" "$T/af" >/dev/null
mkdir -p "$T/bin"
cat > "$T/bin/docker" <<'EOF'
#!/bin/sh
# fake docker for the demo: four workers (worker-4 runs Claude Code with an API key), quota for the Antigravity ones
case "$1 $*" in
  "ps "*Label*State*) printf 'worker-1 running agy\nworker-2 running agy\nworker-3 running agy\nworker-4 running claude\n' ;;
  "ps "*Label*) printf 'worker-1|agy\nworker-2|agy\nworker-3|agy\nworker-4|claude\n' ;;
  "ps "*State*) printf 'worker-1 running\nworker-2 running\nworker-3 running\nworker-4 running\n' ;;
  "ps "*) printf 'worker-1\nworker-2\nworker-3\nworker-4\n' ;;
  "exec "*usage*) case "$*" in *worker-4*) exit 1 ;; *) cat /af/.demo-usage ;; esac ;;
  "inspect "*) echo claude ;;
  *) exit 1 ;;
esac
EOF
chmod +x "$T/bin/docker"
docker run -d --name "$N" --user "$(id -u):$(id -g)" -v "$T/af:/af" -v "$T/bin/docker:/usr/local/bin/docker:ro" -w /af \
  -e FORGE_DIR=/af -e BIND_PORT=8171 -e PYTHONDONTWRITEBYTECODE=1 -e HOME=/tmp python:3.12-slim python3 web/server.py >/dev/null
for i in $(seq 1 40); do docker exec "$N" python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8171/healthz')" 2>/dev/null && break; sleep 1; done
sleep 8    # first foreman poll + quota scan
shot() {   # name, url fragment, width, height
  docker run --rm --network "container:$N" -v "$OUT:/out" --entrypoint chromium-browser zenika/alpine-chrome:latest \
    --headless --no-sandbox --disable-gpu --hide-scrollbars --force-device-scale-factor=1 --window-size="$3,$4" \
    --virtual-time-budget=9000 --screenshot="/out/$1.png" "http://127.0.0.1:8171/#$2" >/dev/null 2>&1
  echo "  $OUT/$1.png"
}
shot overview  "tab=overview" 1440 1500
shot queue     "tab=queue" 1440 900
shot timeline  "tab=timeline" 1440 760
shot live      "tab=live" 1440 860
shot metrics   "tab=metrics" 1440 1300
shot settings  "tab=settings" 1440 1600
shot task      "tab=queue&task=0004-due-store" 1440 1100
shot mobile    "tab=overview" 390 1700
# a short animated tour for the README (Pillow in a throwaway container)
for t in overview queue timeline live metrics; do shot "tour-$t" "tab=$t" 1280 800 >/dev/null; done
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$OUT:/out" python:3.12-slim sh -c "pip install -q --user pillow 2>/dev/null && python3 -c \"
from PIL import Image
fr = [Image.open(f'/out/tour-{t}.png').convert('RGB').resize((960, 600)) for t in ('overview', 'queue', 'timeline', 'live', 'metrics')]
fr = [f.quantize(colors=128, method=Image.Quantize.MEDIANCUT) for f in fr]
fr[0].save('/out/tour.gif', save_all=True, append_images=fr[1:], duration=2200, loop=0, optimize=True)
\"" && rm -f "$OUT"/tour-*.png && echo "  $OUT/tour.gif"
