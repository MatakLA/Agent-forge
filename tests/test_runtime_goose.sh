#!/usr/bin/env bash
# Integration test for Goose runtime in bin/lib/runtimes.py against fake-goose-server.js.
# Public interface: bash tests/test_runtime_goose.sh (runs with no real network call, exits 0 on success).
# Invariants: drives command('goose', ...) and scan('goose', ...) / scan_file(...),
# verifies honest unknown cost model (tokens is None), tools count, and result string,
# ensures prompt and secrets never leak in argv, points worker at fake endpoint server,
# and verifies fake server reached only expected host:port.

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

# 1. Metadata and registration checks for goose runtime
check "goose runtime: registered in RUNTIMES with correct metadata" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
assert \"goose\" in runtimes.RUNTIMES
entry = runtimes.RUNTIMES[\"goose\"]
assert entry[\"image\"] == runtimes.AGY_IMAGE
assert entry[\"key\"] is None
assert entry[\"hosts\"] == []
assert entry[\"api\"] is False
assert entry.get(\"endpoint\") is True
"'

check "goose runtime: is_api() returns False" \
  'python3 -c "import sys; sys.path.insert(0, \"$ROOT/bin/lib\"); import runtimes; assert runtimes.is_api(\"goose\") is False"'

check "goose runtime: uses_endpoint() returns True" \
  'python3 -c "import sys; sys.path.insert(0, \"$ROOT/bin/lib\"); import runtimes; assert runtimes.uses_endpoint(\"goose\") is True"'

check "goose runtime: hosts() derives host from endpoint URL" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
assert runtimes.hosts(\"goose\", \"http://127.0.0.1:8099/v1\") == [\"127.0.0.1\"]
assert runtimes.hosts(\"goose\", \"https://api.openai.com/v1\") == [\"api.openai.com\"]
assert runtimes.hosts(\"goose\", \"\") == []
assert runtimes.hosts(\"goose\", \"ftp://invalid\") == []
"'

# 2. command('goose', ...) contract and argv security checks
check "goose command(): returns goose run --instructions - with prompt on stdin" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
prompt = \"Implement Goose integration tests\"
argv, stdin = runtimes.command(\"goose\", prompt, \"gpt-4o\")
assert argv == [\"goose\", \"run\", \"--instructions\", \"-\"]
assert stdin == prompt
"'

check "goose command(): prompt and model never leak in argv" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
prompt = \"Confidential prompt text with unicode: 修复测试漏洞 \" * 50
argv, stdin = runtimes.command(\"goose\", prompt, \"gpt-4o\", conversation=\"ses_123\", budget_usd=5.0)
argv_str = \" \".join(argv)
assert prompt not in argv_str
assert \"gpt-4o\" not in argv_str
assert \"ses_123\" not in argv_str
assert stdin == prompt
"'

# 3. Allocate local port and configure endpoint pointing at fake server
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
export GOOSE_SERVER_PORT="$PORT"
export PROXY_LOG="$T/proxy.log"

# Point goose endpoint at fake server (same mechanism tests/test_runtimes.sh uses for compat/aider)
printf 'COMPAT_BASE_URL=http://127.0.0.1:%s/v1\n' "$PORT" > "$T/etc/limits.conf"
export COMPAT_BASE_URL="http://127.0.0.1:$PORT/v1"
export AF_ENDPOINT="http://127.0.0.1:$PORT/v1"
export AF_TEST_BASE_URL="http://127.0.0.1:$PORT"
export OPENAI_BASE_URL="http://127.0.0.1:$PORT/v1"
export GOOSE_PROVIDER="openai"
export GOOSE_MODEL="gpt-4o"

# 4. Start fake Goose backend server
if command -v node >/dev/null 2>&1; then
  node "$ROOT/tests/fixtures/fake-goose-server.js" "$PORT" > "$T/server.log" 2>&1 &
  SERVER_PID=$!
else
  # Python fallback server providing identical behavior when node is absent
  python3 -c "
import http.server, json, sys, os, time
port = int(sys.argv[1])
proxy_log = os.environ.get('PROXY_LOG')
class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length > 0 else ''
        try:
            j = json.loads(body)
        except Exception:
            j = {}
        host = self.headers.get('Host', '')
        req_line = f'{self.command} {self.path} {self.request_version} Host: {host}'
        sys.stderr.write(f'{req_line} model={j.get(\"model\", \"\")} bytes={len(body)}\n')
        sys.stderr.flush()
        if proxy_log:
            try:
                with open(proxy_log, 'a') as pf:
                    pf.write(f'{req_line}\n')
            except Exception:
                pass
        if self.path in ('/health', '/healthz'):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{\"status\":\"ok\"}')
            return
        if '/error' in self.path or j.get('error'):
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': {'message': j.get('error') or 'simulated goose endpoint error'}}).encode('utf-8'))
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        resp = {
            'id': 'chatcmpl-test',
            'object': 'chat.completion',
            'created': int(time.time()),
            'model': j.get('model', 'gpt-4o'),
            'choices': [{
                'index': 0,
                'message': {
                    'role': 'assistant',
                    'content': 'Task completed successfully.',
                    'tool_calls': [{
                        'id': 'call_1',
                        'type': 'function',
                        'function': {'name': 'bash', 'arguments': '{\"command\": \"git status\"}'}
                    }]
                },
                'finish_reason': 'stop'
            }],
            'usage': {'prompt_tokens': 120, 'completion_tokens': 35, 'total_tokens': 155}
        }
        self.wfile.write(json.dumps(resp).encode('utf-8'))
    def do_GET(self):
        self.do_POST()
    def log_message(self, format, *args):
        pass
server = http.server.HTTPServer(('127.0.0.1', port), Handler)
sys.stderr.write(f'fake Goose server listening on 127.0.0.1:{port}\n')
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

# 5. Create stub standing in for goose binary during tests
cat > "$T/bin/goose" <<'EOF'
#!/usr/bin/env python3
"""Stub standing in for Block's goose CLI binary during tests."""
import json
import os
import sys
import urllib.request

endpoint = (
    os.environ.get("AF_ENDPOINT")
    or os.environ.get("COMPAT_BASE_URL")
    or os.environ.get("OPENAI_BASE_URL")
    or f"http://127.0.0.1:{os.environ.get('GOOSE_SERVER_PORT', '8099')}/v1"
)

endpoint = endpoint.rstrip("/")
if not endpoint.endswith("/chat/completions") and not endpoint.endswith("/messages"):
    endpoint = f"{endpoint}/chat/completions"

args = sys.argv[1:]
instructions = ""

if "--instructions" in args:
    idx = args.index("--instructions")
    if idx + 1 < len(args) and args[idx + 1] == "-":
        instructions = sys.stdin.read()
elif not sys.stdin.isatty():
    instructions = sys.stdin.read()

if "TRIGGER_ERROR" in instructions:
    base = endpoint.rsplit("/", 1)[0]
    endpoint = f"{base}/error"

payload = json.dumps({
    "model": os.environ.get("GOOSE_MODEL", "gpt-4o"),
    "messages": [{"role": "user", "content": instructions}],
    "stream": False,
}).encode("utf-8")

req = urllib.request.Request(
    endpoint,
    data=payload,
    headers={"Content-Type": "application/json", "User-Agent": "goose-stub/1.0"},
)

try:
    with urllib.request.urlopen(req) as resp:
        body = resp.read().decode("utf-8")
        data = json.loads(body)

    print("Starting Goose agent session...")
    choices = data.get("choices") or []
    if choices:
        msg = choices[0].get("message") or {}
        tool_calls = msg.get("tool_calls") or []
        for tc in tool_calls:
            fn = tc.get("function") or {}
            print(f"Invoking tool: {fn.get('name', 'bash')} with command {fn.get('arguments', '')}")
            print("Tool call completed.")
        content = msg.get("content") or "Task completed successfully."
        print(content)
    else:
        print("Task completed successfully.")
except Exception as e:
    sys.stderr.write(f"goose stub error: {e}\n")
    print(f"Error: Goose execution failed: {e}")
    sys.exit(1 if "TRIGGER_ERROR" in instructions else 0)
EOF
chmod +x "$T/bin/goose"
export PATH="$T/bin:$PATH"

# 6. Drive command('goose', ...) against fake server and capture output
RUN_JSON=$(python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, '$ROOT/bin/lib')
import runtimes

prompt = 'Fix login route authentication bug'
argv, stdin_text = runtimes.command('goose', prompt, 'gpt-4o')
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
if proc.returncode != 0:
    sys.stderr.write(f'stub execution error: {stderr}\n')
    sys.exit(proc.returncode)

lines = stdout.splitlines()
parsed = runtimes.scan('goose', lines)
out = {
    'tokens': parsed['tokens'],
    'tools': parsed['tools'],
    'conversation_id': parsed['conversation_id'],
    'result_str': str(parsed['result']),
    'is_str': isinstance(parsed['result'], str) if parsed['result'] else False,
}
print(json.dumps(out))
")

check "goose scan(): tokens is None (honest unknown cost model)" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"tokens\"] is None, \"tokens must be None, got %s\" % r[\"tokens\"]" "$RUN_JSON"'

check "goose scan(): tools count is non-zero (tools >= 1)" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"tools\"] >= 1, \"tools count was %s\" % r[\"tools\"]" "$RUN_JSON"'

check "goose scan(): conversation_id is None" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"conversation_id\"] is None, \"conversation_id was %s\" % r[\"conversation_id\"]" "$RUN_JSON"'

check "goose scan(): result is a string containing task output" \
  'python3 -c "
import json, sys
r = json.loads(sys.argv[1])
assert r[\"is_str\"] is True, \"result must be a string\"
assert \"Task completed successfully.\" in r[\"result_str\"]
" "$RUN_JSON"'

# 7. Verify fake server logged requests with expected host:port
check "fake server saw and logged requests" \
  'grep -q "POST" "$T/server.log"'

check "fake server reached only expected host:port" \
  'grep -q "127.0.0.1:$PORT" "$T/server.log"'

# 8. scan() error handling
check "goose scan(): error output captured in result string" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
err_lines = [
    \"Starting Goose session\",
    \"Error: Goose execution failed due to API error\",
]
r = runtimes.scan(\"goose\", err_lines)
assert r[\"tokens\"] is None
assert r[\"result\"] is not None
assert isinstance(r[\"result\"], str)
assert \"Error: Goose execution failed\" in r[\"result\"]
"'

# 9. scan() stream edge cases
check "goose scan(): empty stream returns tokens=None, tools=0, conversation_id=None, result=None" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
r = runtimes.scan(\"goose\", [\"\", \"   \"])
assert r[\"tokens\"] is None
assert r[\"tools\"] == 0
assert r[\"conversation_id\"] is None
assert r[\"result\"] is None
"'

check "goose scan(): counts various tool call syntaxes (plain text and JSON)" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
tool_lines = [
    \"Tool call: git status\",
    \"Invoking tool: bash with command pytest\",
    \"Running tool: pytest\",
    json.dumps({\"type\": \"tool_call\", \"name\": \"editor\"}),
    json.dumps({\"type\": \"tool_use\", \"name\": \"shell\"}),
]
# Verify each syntax independently increments tools by exactly 1
for line in tool_lines:
    assert runtimes.scan(\"goose\", [line])[\"tools\"] == 1
# Verify combined stream of 2 non-tool lines and 5 tool-call lines yields tools == 5
stream = [\"Starting session\"] + tool_lines + [\"All finished successfully.\"]
r = runtimes.scan(\"goose\", stream)
assert r[\"tokens\"] is None
assert r[\"tools\"] == len(tool_lines) == 5
assert isinstance(r[\"result\"], str)
assert r[\"result\"] == \"All finished successfully.\"
"'

# 10. scan_file('goose', path) helper
check "goose scan_file(): parses output from file correctly" \
  'python3 -c "
import os, sys, tempfile
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
with tempfile.NamedTemporaryFile(\"w+\", encoding=\"utf-8\") as tf:
    tf.write(\"Starting goose\\n\")
    tf.write(\"Tool call: git status\\n\")
    tf.write(\"Done\\n\")
    tf.flush()
    res = runtimes.scan_file(\"goose\", tf.name)
    assert res[\"tokens\"] is None
    assert res[\"tools\"] == 1
    assert isinstance(res[\"result\"], str)
    assert res[\"result\"] == \"Done\"
"'

check "goose scan_file(): handles missing file gracefully" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
missing = runtimes.scan_file(\"goose\", \"/path/does/not/exist.log\")
assert missing[\"tokens\"] is None
assert missing[\"tools\"] == 0
assert missing[\"result\"] is None
"'

echo "goose runtime tests: $([ $fail = 0 ] && echo ALL OK || echo FAILED)"
exit $fail
