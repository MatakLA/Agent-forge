#!/usr/bin/env bash
# Integration test for OpenCode runtime in bin/lib/runtimes.py against fake-opencode-server.js.
# Public interface: bash tests/test_runtime_opencode.sh (runs with no real network call, exits 0 on success).
# Invariants: starts fake-opencode-server.js, points OpenCode runtime command at it,
# drives command() and scan() through the CLI stub, tests error/malformed lines handling,
# runs tests/test_runtime_opencode.py against the environment, and strictly propagates real exit codes.

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

mkdir -p "$T/bin" "$T/etc"

# 1. Metadata and registration checks for opencode runtime
check "opencode runtime: registered in RUNTIMES with correct metadata" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
assert \"opencode\" in runtimes.RUNTIMES
entry = runtimes.RUNTIMES[\"opencode\"]
assert entry[\"image\"] == runtimes.AGY_IMAGE
assert entry[\"key\"] is None
assert entry[\"hosts\"] == []
assert entry[\"api\"] is False
assert entry.get(\"endpoint\") is True
"'

check "opencode runtime: is_api() returns False" \
  'python3 -c "import sys; sys.path.insert(0, \"$ROOT/bin/lib\"); import runtimes; assert runtimes.is_api(\"opencode\") is False"'

check "opencode runtime: uses_endpoint() returns True" \
  'python3 -c "import sys; sys.path.insert(0, \"$ROOT/bin/lib\"); import runtimes; assert runtimes.uses_endpoint(\"opencode\") is True"'

check "opencode runtime: hosts() resolves host from endpoint URL" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
assert runtimes.hosts(\"opencode\", \"http://127.0.0.1:8098/v1\") == [\"127.0.0.1\"]
assert runtimes.hosts(\"opencode\", \"https://api.openai.com/v1\") == [\"api.openai.com\"]
assert runtimes.hosts(\"opencode\", \"\") == []
assert runtimes.hosts(\"opencode\", \"ftp://invalid\") == []
"'

# 2. command('opencode', ...) contract and argv security checks
check "opencode command(): starts with opencode run and quiet flags" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
argv, stdin = runtimes.command(\"opencode\", \"Fix auth handler\", \"qwen-coder\")
assert argv[:2] == [\"opencode\", \"run\"]
for flag in runtimes.OPENCODE_QUIET:
    assert flag in argv
assert \"--model\" in argv and \"qwen-coder\" in argv
assert stdin == \"Fix auth handler\"
assert \"Fix auth handler\" not in argv
"'

check "opencode command(): prompt passed on stdin and omitted from argv" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
prompt = \"Confidential prompt text with unicode: 修复测试漏洞 \" * 50
argv, stdin = runtimes.command(\"opencode\", prompt, \"claude-3-5-sonnet\")
assert stdin == prompt
assert prompt not in argv
assert \"修复测试漏洞\" not in \" \".join(argv)
"'

check "opencode command(): handles model and conversation parameters" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
argv, _ = runtimes.command(\"opencode\", \"task\", \"custom-model\", conversation=\"ses_custom_99\")
assert \"--model\" in argv and \"custom-model\" in argv
assert \"--session\" in argv and \"ses_custom_99\" in argv
"'

check "opencode command(): omits empty model and session flags" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
argv, _ = runtimes.command(\"opencode\", \"task\", \"\", conversation=None)
assert \"--model\" not in argv
assert \"--session\" not in argv
"'

# 3. Determine an available local port and configure endpoint pointing at fake server
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
export OPENCODE_SERVER_PORT="$PORT"
export PORT="$PORT"

printf 'COMPAT_BASE_URL=http://127.0.0.1:%s/v1\n' "$PORT" > "$T/etc/limits.conf"
export COMPAT_BASE_URL="http://127.0.0.1:$PORT/v1"
export AF_ENDPOINT="http://127.0.0.1:$PORT/v1"
export AF_TEST_BASE_URL="http://127.0.0.1:$PORT"

# 4. Start the fake OpenCode server fixture
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

# 5. Create stub standing in for the opencode CLI binary
cat > "$T/bin/opencode" <<'EOF'
#!/usr/bin/env python3
"""Stub standing in for SST's opencode CLI binary during tests."""
import json
import os
import sys
import urllib.request

endpoint = (
    os.environ.get("OPENCODE_SERVER_URL")
    or os.environ.get("AF_ENDPOINT")
    or os.environ.get("COMPAT_BASE_URL")
    or f"http://127.0.0.1:{os.environ.get('OPENCODE_SERVER_PORT', '8098')}/run"
)
endpoint = endpoint.rstrip("/")
if not endpoint.endswith("/run") and not endpoint.endswith("/error"):
    if endpoint.endswith("/v1"):
        endpoint = endpoint[:-3]
    endpoint = f"{endpoint}/run"

prompt = sys.stdin.read()
args = sys.argv[1:]

session_id = None
model = None
for i, arg in enumerate(args):
    if arg in ("--session", "-s") and i + 1 < len(args):
        session_id = args[i + 1]
    elif arg in ("--model", "-m") and i + 1 < len(args):
        model = args[i + 1]

has_error = "TRIGGER_ERROR" in prompt or "--trigger-error" in args or "/error" in endpoint

if has_error:
    base = endpoint.rsplit("/", 1)[0]
    endpoint = f"{base}/error"

payload = json.dumps({
    "prompt": prompt,
    "args": args,
    "sessionID": session_id,
    "model": model,
    "error": "simulated rate limit" if has_error else None,
}).encode("utf-8")

req = urllib.request.Request(endpoint, data=payload, headers={"Content-Type": "application/json"})
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

# 6. Drive opencode command() through the CLI stub against fake server, feed lines to scan()
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

# 7. Rework continuing conversation with --session
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

# 8. Drive error path against fake server via stub
ERR_RUN_JSON=$(python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, '$ROOT/bin/lib')
import runtimes

prompt = 'TRIGGER_ERROR'
argv, stdin_text = runtimes.command('opencode', prompt, 'qwen-coder')
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
lines = stdout.splitlines()
parsed = runtimes.scan('opencode', lines)
print(json.dumps(parsed))
")

check "opencode live stub: error response parses as status ERROR without crashing" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"result\"] is not None and r[\"result\"][\"status\"] == \"ERROR\" and \"rate limit\" in r[\"result\"][\"error\"]" "$ERR_RUN_JSON"'

# 9. Fake server logged requests
check "fake server saw and logged requests" \
  'grep -q "session=" "$T/server.log"'

# 10. scan() error handling and edge cases
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

check "opencode scan(): malformed and corrupt lines skipped safely" \
  'python3 -c "
import json, os, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes

noisy_lines = [
    \"corrupt garbage line\",
    \"{\\\"type\\\": \\\"step_start\\\", \\\"sessionID\\\": \\\"ses_noisy\\\"}\",
    \"{\\\"truncated\\\": \",
    \"[1, 2, 3]\",
    \"{\\\"type\\\": \\\"tool_use\\\", \\\"name\\\": \\\"bash\\\", \\\"sessionID\\\": \\\"ses_noisy\\\"}\",
    \"{\\\"type\\\": \\\"step_finish\\\", \\\"sessionID\\\": \\\"ses_noisy\\\", \\\"part\\\": {\\\"reason\\\": \\\"stop\\\"}}\"
]
res = runtimes.scan(\"opencode\", noisy_lines)
assert res[\"tokens\"] is None
assert res[\"tools\"] == 1
assert res[\"conversation_id\"] == \"ses_noisy\"
assert res[\"result\"] is not None
assert res[\"result\"][\"status\"] == \"SUCCESS\"
"'

check "opencode scan_file(): parses events from file and handles missing file" \
  'python3 -c "
import os, sys, tempfile
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes

with tempfile.NamedTemporaryFile(\"w+\", encoding=\"utf-8\") as tf:
    tf.write(\"{\\\"type\\\": \\\"step_start\\\", \\\"sessionID\\\": \\\"ses_file_01\\\"}\\n\")
    tf.write(\"{\\\"type\\\": \\\"step_finish\\\", \\\"sessionID\\\": \\\"ses_file_01\\\", \\\"part\\\": {\\\"reason\\\": \\\"stop\\\"}}\\n\")
    tf.flush()
    r = runtimes.scan_file(\"opencode\", tf.name)
    assert r[\"conversation_id\"] == \"ses_file_01\"
    assert r[\"result\"][\"status\"] == \"SUCCESS\"

missing = runtimes.scan_file(\"opencode\", \"/nonexistent/file.log\")
assert missing[\"tokens\"] is None
assert missing[\"result\"] is None
"'

# 11. Run tests/test_runtime_opencode.py directly and propagate the real exit code
echo "Running tests/test_runtime_opencode.py against fake server..."
python3 "$ROOT/tests/test_runtime_opencode.py" || exit 1
echo "ok   python test suite (tests/test_runtime_opencode.py) passed"

echo "opencode runtime tests: $([ $fail = 0 ] && echo ALL OK || echo FAILED)"
exit $fail
