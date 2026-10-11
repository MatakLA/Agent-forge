#!/usr/bin/env bash
# Integration test for OpenCode runtime in bin/lib/runtimes.py against fake-opencode-server.js.
# Public interface: bash tests/test_runtime_opencode.sh (runs with no real network call, exits 0).
# Invariants: drives command() and scan(), verifies honest unknown cost model (tokens: None),
# tools count >= 1, conversation_id preservation, and SUCCESS result status.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)"
fail=0

check() {
  if eval "$2"; then
    echo "ok   $1"
  else
    echo "FAIL $1"
    fail=1
  fi
}

cleanup() {
  if [ -n "${SERVER_PID:-}" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$T"
}
trap cleanup EXIT

mkdir -p "$T/bin"

# Determine an available local port
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
export OPENCODE_SERVER_PORT="$PORT"

# Start the fake OpenCode server
if command -v node >/dev/null 2>&1; then
  node "$ROOT/tests/fixtures/fake-opencode-server.js" "$PORT" > "$T/server.log" 2>&1 &
  SERVER_PID=$!
else
  # Python fallback server providing identical behavior when node is absent on the host
  python3 -c "
import http.server, json, sys, os
port = int(sys.argv[1])
class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length > 0 else ''
        try:
            j = json.loads(body)
        except Exception:
            j = {}
        sid = j.get('sessionID') or j.get('session_id') or j.get('sessionId') or j.get('session') or 'ses_opencode_test_01'
        sys.stderr.write(f'{self.command} {self.path} session={sid} bytes={len(body)}\n')
        sys.stderr.flush()
        if self.path in ('/health', '/healthz'):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{\"status\":\"ok\"}')
            return
        if '/error' in self.path or j.get('error'):
            events = [
                {'type': 'step_start', 'sessionID': sid},
                {'type': 'step_finish', 'sessionID': sid, 'part': {'reason': 'error', 'error': j.get('error') or 'simulated rate limit'}}
            ]
        else:
            events = [
                {'type': 'step_start', 'sessionID': sid, 'part': {'id': 'part_start_01'}},
                {'type': 'tool_use', 'name': 'bash', 'sessionID': sid, 'part': {'type': 'tool', 'tool': 'bash', 'command': 'echo \"hello from opencode\" > hello.txt'}},
                {'type': 'text', 'sessionID': sid, 'part': {'text': 'Done: wrote hello.txt.'}},
                {'type': 'step_finish', 'sessionID': sid, 'part': {'reason': 'stop'}}
            ]
        self.send_response(200)
        self.send_header('Content-Type', 'application/x-ndjson')
        self.end_headers()
        for ev in events:
            self.wfile.write((json.dumps(ev) + '\n').encode('utf-8'))
    def do_GET(self):
        self.do_POST()
    def log_message(self, format, *args):
        pass
server = http.server.HTTPServer(('127.0.0.1', port), Handler)
sys.stderr.write(f'fake OpenCode server listening on 127.0.0.1:{port}\n')
sys.stderr.flush()
server.serve_forever()
" "$PORT" > "$T/server.log" 2>&1 &
  SERVER_PID=$!
fi

# Wait for fake server to accept connections
for _ in $(seq 1 40); do
  python3 -c "import socket; s=socket.socket(); s.connect(('127.0.0.1', $PORT)); s.close()" 2>/dev/null && break
  sleep 0.1
done

# Create stub standing in for the opencode CLI binary
cat > "$T/bin/opencode" <<'EOF'
#!/usr/bin/env python3
"""Stub standing in for SST's opencode CLI binary during tests."""
import json
import os
import sys
import urllib.request

port = os.environ.get("OPENCODE_SERVER_PORT", "8098")
url = f"http://127.0.0.1:{port}/run"

prompt = sys.stdin.read()
args = sys.argv[1:]

session_id = None
model = None
for i, arg in enumerate(args):
    if arg in ("--session", "-s") and i + 1 < len(args):
        session_id = args[i + 1]
    elif arg in ("--model", "-m") and i + 1 < len(args):
        model = args[i + 1]

payload = json.dumps({
    "prompt": prompt,
    "args": args,
    "sessionID": session_id,
    "model": model,
}).encode("utf-8")

req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req) as resp:
        for line in resp:
            sys.stdout.write(line.decode("utf-8"))
except Exception as e:
    sys.stderr.write(f"opencode stub error: {e}\n")
    sys.exit(1)
EOF
chmod +x "$T/bin/opencode"
export PATH="$T/bin:$PATH"

# Test 1: Verify command('opencode', ...) argv and stdin contract
CMD_PAYLOAD=$(python3 -c "
import sys, json, os
sys.path.insert(0, '$ROOT/bin/lib')
import runtimes
argv, stdin = runtimes.command('opencode', 'Fix auth handler', 'qwen-coder')
print(json.dumps({'argv': argv, 'stdin': stdin}))
")

check "opencode command(): starts with opencode run" \
  'python3 -c "import json, sys; d=json.loads(sys.argv[1]); assert d[\"argv\"][:2] == [\"opencode\", \"run\"]" "$CMD_PAYLOAD"'

check "opencode command(): quiet format flag present" \
  'python3 -c "import json, sys; d=json.loads(sys.argv[1]); assert \"--format\" in d[\"argv\"] and \"json\" in d[\"argv\"]" "$CMD_PAYLOAD"'

check "opencode command(): prompt passed on stdin and omitted from argv" \
  'python3 -c "import json, sys; d=json.loads(sys.argv[1]); assert d[\"stdin\"] == \"Fix auth handler\"; assert \"Fix auth handler\" not in d[\"argv\"]" "$CMD_PAYLOAD"'

check "opencode command(): model flag passed" \
  'python3 -c "import json, sys; d=json.loads(sys.argv[1]); assert \"--model\" in d[\"argv\"] and \"qwen-coder\" in d[\"argv\"]" "$CMD_PAYLOAD"'

# Test 2: Drive opencode command() through the CLI stub against fake server, feed lines to scan()
RUN_JSON=$(python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, '$ROOT/bin/lib')
import runtimes

prompt = 'Implement user tokens in auth.py'
argv, stdin_text = runtimes.command('opencode', prompt, 'qwen-coder')
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
if proc.returncode != 0:
    sys.stderr.write(f'stub execution error: {stderr}\n')
    sys.exit(proc.returncode)

lines = stdout.splitlines()
parsed = runtimes.scan('opencode', lines)
print(json.dumps(parsed))
")

check "opencode scan(): tokens is None (honest unknown cost model)" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"tokens\"] is None, f\"tokens must be None, got {r[\"tokens\"]}\"" "$RUN_JSON"'

check "opencode scan(): tools >= 1" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"tools\"] >= 1, f\"tools count was {r[\"tools\"]}\"" "$RUN_JSON"'

check "opencode scan(): conversation_id found" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"conversation_id\"] == \"ses_opencode_test_01\"" "$RUN_JSON"'

check "opencode scan(): result is non-None with SUCCESS status" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"result\"] is not None and r[\"result\"][\"status\"] == \"SUCCESS\"" "$RUN_JSON"'

check "opencode scan(): cost_usd is None" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"result\"][\"cost_usd\"] is None" "$RUN_JSON"'

# Test 3: Rework continuing conversation with --session
CONV_REWORK="ses_opencode_resumed_42"
REWORK_JSON=$(python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, '$ROOT/bin/lib')
import runtimes

prompt = 'Fix failing edge case'
argv, stdin_text = runtimes.command('opencode', prompt, 'qwen-coder', conversation='$CONV_REWORK')
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
lines = stdout.splitlines()
parsed = runtimes.scan('opencode', lines)
print(json.dumps(parsed))
")

check "opencode rework: command preserves session and scan extracts it" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"conversation_id\"] == \"$CONV_REWORK\"" "$REWORK_JSON"'

# Test 4: Fake server logged requests
check "fake server saw and logged requests" \
  'grep -q "session=" "$T/server.log"'

# Test 5: Error handling in scan()
check "opencode scan(): error step_finish parses as ERROR status" \
  'python3 -c "
import json, os, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes

err_stream = [
    \"{\\\"type\\\": \\\"step_start\\\", \\\"sessionID\\\": \\\"ses_err\\\"}\",
    \"{\\\"type\\\": \\\"step_finish\\\", \\\"sessionID\\\": \\\"ses_err\\\", \\\"part\\\": {\\\"reason\\\": \\\"error\\\", \\\"error\\\": \\\"rate limit exceeded\\\"}}\"
]
res = runtimes.scan(\"opencode\", err_stream)
assert res[\"tokens\"] is None
assert res[\"result\"] is not None
assert res[\"result\"][\"status\"] == \"ERROR\"
assert \"rate limit exceeded\" in res[\"result\"][\"error\"]
"'

# Test 6: Incomplete stream handling in scan()
check "opencode scan(): incomplete stream leaves result as None" \
  'python3 -c "
import json, os, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes

mid_stream = [
    \"{\\\"type\\\": \\\"step_start\\\", \\\"sessionID\\\": \\\"ses_mid\\\"}\",
    \"{\\\"type\\\": \\\"tool_use\\\", \\\"name\\\": \\\"bash\\\", \\\"sessionID\\\": \\\"ses_mid\\\"}\"
]
res = runtimes.scan(\"opencode\", mid_stream)
assert res[\"tokens\"] is None
assert res[\"tools\"] == 1
assert res[\"result\"] is None
"'

echo "opencode runtime tests: $([ $fail = 0 ] && echo ALL OK || echo FAILED)"
exit $fail
