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
docker run -d --name "$N" --user "$(id -u):$(id -g)" -v "$T/af:/af" -v "$ROOT/tools/fake-docker:/usr/local/bin/docker:ro" -w /af \
  -e FORGE_DIR=/af -e BIND_PORT=8171 -e PYTHONDONTWRITEBYTECODE=1 -e HOME=/tmp python:3.12-slim python3 web/server.py >/dev/null
for i in $(seq 1 40); do docker exec "$N" python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8171/healthz')" 2>/dev/null && break; sleep 1; done
sleep 8    # first foreman poll + quota scan
RID="$(cat "$T/af/runs/.demo-replay")"     # the finished run the demo has a transcript for
shot() {   # name, url fragment, width, height   (#poll=1: no live stream - headless Chromium waits for open requests)
  timeout 120 docker run --rm --network "container:$N" -v "$OUT:/out" --entrypoint chromium-browser zenika/alpine-chrome:latest \
    --headless --no-sandbox --disable-gpu --hide-scrollbars --force-device-scale-factor=1 --window-size="$3,$4" \
    --virtual-time-budget=9000 --screenshot="/out/$1.png" "http://127.0.0.1:8171/#$2&poll=1" >/dev/null 2>&1
  echo "  $OUT/$1.png"
}
shot overview    "tab=overview" 1440 1500
shot tasks       "tab=tasks&view=board" 1440 900
shot graph       "tab=tasks&view=graph" 1440 760
shot task        "tab=tasks&view=board&task=0010-overdue-color" 1440 1000
shot goal        "tab=overview&goal=G20261008-062000" 1440 1250
shot live        "tab=live" 1440 860
shot replay      "tab=live&run=$RID" 1440 1000
shot timeline    "tab=timeline" 1440 760
shot money       "tab=insights&view=money" 1440 1350
shot experiments "tab=insights&view=experiments" 1440 820
shot settings    "tab=settings&view=project" 1440 1300
shot mobile      "tab=overview" 390 1700
# a short animated tour for the README (Pillow in a throwaway container)
for x in "overview tab=overview" "tasks tab=tasks&view=board" "goal tab=overview&goal=G20261008-062000" "live tab=live" "replay tab=live&run=$RID" \
         "money tab=insights&view=money"; do set -- $x; shot "tour-$1" "$2" 1280 800 >/dev/null; done
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$OUT:/out" python:3.12-slim sh -c "pip install -q --user pillow 2>/dev/null && python3 -c \"
from PIL import Image
names = ('overview', 'tasks', 'goal', 'live', 'replay', 'money')
fr = [Image.open(f'/out/tour-{t}.png').convert('RGB').resize((960, 600)) for t in names]
fr = [f.quantize(colors=128, method=Image.Quantize.MEDIANCUT) for f in fr]
fr[0].save('/out/tour.gif', save_all=True, append_images=fr[1:], duration=2400, loop=0, optimize=True)
\"" && rm -f "$OUT"/tour-*.png && echo "  $OUT/tour.gif"
