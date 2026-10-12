#!/usr/bin/env bash
# Integration test for qwen-code runtime in bin/lib/runtimes.py against fake-qwen-server.js.
# Public interface: bash tests/test_runtime_qwen.sh (runs with no real network call, exits 0 on success).
# Invariants: drives command() and scan()/scan_file(), verifies {tokens, tools, conversation_id, result}
# shape, verifies exact token and cost calculations, and asserts command() never puts prompts or secrets in argv.

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

# 1. Metadata and predicate checks for qwen-code
check "qwen-code runtime: registered in RUNTIMES" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
entry = runtimes.RUNTIMES[\"qwen-code\"]
assert entry[\"image\"] == runtimes.AGY_IMAGE
assert entry[\"key\"] == \"qwen.key\"
assert entry[\"hosts\"] == [\"dashscope.aliyuncs.com\", \"dashscope-intl.aliyuncs.com\"]
assert entry[\"api\"] is False
"'

check "qwen-code runtime: is_api() returns False" \
  'python3 -c "import sys; sys.path.insert(0, \"$ROOT/bin/lib\"); import runtimes; assert runtimes.is_api(\"qwen-code\") is False"'

check "qwen-code runtime: uses_endpoint() returns False" \
  'python3 -c "import sys; sys.path.insert(0, \"$ROOT/bin/lib\"); import runtimes; assert runtimes.uses_endpoint(\"qwen-code\") is False"'

check "qwen-code runtime: hosts() returns DashScope endpoints" \
  'python3 -c "import sys; sys.path.insert(0, \"$ROOT/bin/lib\"); import runtimes; assert runtimes.hosts(\"qwen-code\") == [\"dashscope.aliyuncs.com\", \"dashscope-intl.aliyuncs.com\"]"'

# 2. command('qwen-code', ...) contract and argv security checks
check "qwen-code command(): starts with qwen binary and quiet flags" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
argv, stdin = runtimes.command(\"qwen-code\", \"prompt text\", \"qwen-max\")
assert argv[0] == \"qwen\"
for flag in runtimes.QWEN_QUIET:
    assert flag in argv
assert \"-p\" in argv and runtimes.QWEN_TRAILER in argv
"'

check "qwen-code command(): prompt passed on stdin and omitted from argv" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
p = \"Very long prompt content for testing: 修复 DashScope 漏洞\" * 100
argv, stdin = runtimes.command(\"qwen-code\", p, \"qwen-max\")
assert stdin == p
assert p not in argv
"'

check "qwen-code command(): never leaks secrets in argv" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
argv, stdin = runtimes.command(\"qwen-code\", \"task\", \"qwen-max\", budget_usd=5.0)
argv_str = \" \".join(argv)
assert \"qwen.key\" not in argv_str
assert \"DASHSCOPE_API_KEY\" not in argv_str
assert \"sk-\" not in argv_str
"'

check "qwen-code command(): handles model and conversation parameters" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
# With model and conversation
argv, _ = runtimes.command(\"qwen-code\", \"task\", \"qwen-max\", conversation=\"ses_123\")
assert \"-m\" in argv and \"qwen-max\" in argv
assert \"--resume\" in argv and \"ses_123\" in argv

# Without model and conversation
argv_empty, _ = runtimes.command(\"qwen-code\", \"task\", None, conversation=None)
assert \"-m\" not in argv_empty
assert \"--resume\" not in argv_empty
"'

# 3. Start fake Qwen / DashScope server
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
export QWEN_SERVER_PORT="$PORT"

if command -v node >/dev/null 2>&1; then
  node "$ROOT/tests/fixtures/fake-qwen-server.js" "$PORT" > "$T/server.log" 2>&1 &
  SERVER_PID=$!
else
  # Python fallback server providing identical behavior when node is absent
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
        sid = j.get('session_id') or j.get('sessionId') or j.get('thread_id') or j.get('conversation_id') or j.get('sessionID') or 'ses_qwen_test_01'
        sys.stderr.write(f'{self.command} {self.path} model={j.get(\"model\", \"\")} session={sid} bytes={len(body)}\n')
        sys.stderr.flush()
        if self.path in ('/health', '/healthz'):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{\"status\":\"ok\"}')
            return
        if '/error' in self.path or j.get('error'):
            err = j.get('error') or 'DashScope API error: invalid API key'
            events = [
                {'type': 'init', 'session_id': sid},
                {'type': 'error', 'message': err},
                {'type': 'result', 'status': 'error', 'error': err, 'stats': {'total_tokens': 100, 'input_tokens': 100, 'output_tokens': 0, 'cached': 0}}
            ]
        elif '/failed' in self.path or '/turn-failed' in self.path or j.get('turn_failed'):
            events = [
                {'type': 'init', 'session_id': sid},
                {'type': 'turn.failed', 'error': {'message': j.get('turn_failed') or 'DashScope task execution failed'}}
            ]
        else:
            events = [
                {'type': 'init', 'session_id': sid},
                {'type': 'tool_use', 'name': 'shell', 'command': 'git status'},
                {'type': 'message', 'role': 'assistant', 'content': 'Completed task successfully.'},
                {'type': 'result', 'status': 'success', 'stats': {'total_tokens': 1500, 'input_tokens': 1200, 'output_tokens': 300, 'cached': 200}}
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
sys.stderr.write(f'fake Qwen server listening on 127.0.0.1:{port}\n')
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

# 4. Create stub CLI standing in for qwen binary
cat > "$T/bin/qwen" <<'EOF'
#!/usr/bin/env python3
"""Stub standing in for Alibaba's qwen CLI binary during tests."""
import json
import os
import sys
import urllib.request

port = os.environ.get("QWEN_SERVER_PORT", "8097")
url = f"http://127.0.0.1:{port}/run"

prompt = sys.stdin.read()
args = sys.argv[1:]

session_id = None
model = None

for i, arg in enumerate(args):
    if arg in ("--resume", "-r") and i + 1 < len(args):
        session_id = args[i + 1]
    elif arg in ("-m", "--model") and i + 1 < len(args):
        model = args[i + 1]

if "TRIGGER_ERROR" in prompt:
    url = f"http://127.0.0.1:{port}/error"
elif "TRIGGER_TURN_FAILED" in prompt:
    url = f"http://127.0.0.1:{port}/turn-failed"

payload = json.dumps({
    "prompt": prompt,
    "args": args,
    "session_id": session_id,
    "model": model,
}).encode("utf-8")

req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req) as resp:
        for line in resp:
            sys.stdout.write(line.decode("utf-8"))
except Exception as e:
    sys.stderr.write(f"qwen stub error: {e}\n")
    sys.exit(1)
EOF
chmod +x "$T/bin/qwen"
export PATH="$T/bin:$PATH"

# 5. Drive qwen command() through CLI stub against fake server, feed lines to scan()
RUN_JSON=$(python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, '$ROOT/bin/lib')
import runtimes

prompt = 'Implement Qwen Code runtime integration tests'
argv, stdin_text = runtimes.command('qwen-code', prompt, 'qwen-max')
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
if proc.returncode != 0:
    sys.stderr.write(f'stub execution error: {stderr}\n')
    sys.exit(proc.returncode)

lines = stdout.splitlines()
parsed = runtimes.scan('qwen-code', lines, prices='1.5,0.2,3.0')
print(json.dumps(parsed))
")

check "qwen-code scan(): tokens counts processed tokens excluding cache (1500 - 200 = 1300)" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"tokens\"] == 1300, f\"expected 1300 tokens, got {r[\"tokens\"]}\"" "$RUN_JSON"'

check "qwen-code scan(): tools count increments on tool_use" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"tools\"] == 1, f\"expected tools=1, got {r[\"tools\"]}\"" "$RUN_JSON"'

check "qwen-code scan(): conversation_id parsed from init event" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"conversation_id\"] == \"ses_qwen_test_01\", f\"got {r[\"conversation_id\"]}\"" "$RUN_JSON"'

check "qwen-code scan(): result status is SUCCESS" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"result\"][\"status\"] == \"SUCCESS\"" "$RUN_JSON"'

check "qwen-code scan(): response text extracted from assistant message" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"result\"][\"response\"] == \"Completed task successfully.\"" "$RUN_JSON"'

check "qwen-code scan(): cost_usd computed accurately from prices formula" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"result\"][\"cost_usd\"] == 0.00244, f\"got {r[\"result\"][\"cost_usd\"]}\"" "$RUN_JSON"'

# 6. Rework / resume existing conversation session
REWORK_JSON=$(python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, '$ROOT/bin/lib')
import runtimes

prompt = 'Follow-up task on same session'
conv = 'ses_qwen_rework_99'
argv, stdin_text = runtimes.command('qwen-code', prompt, 'qwen-max', conversation=conv)
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
lines = stdout.splitlines()
parsed = runtimes.scan('qwen-code', lines)
print(json.dumps(parsed))
")

check "qwen-code rework: command() preserves session and scan() extracts resumed conversation_id" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"conversation_id\"] == \"ses_qwen_rework_99\"" "$REWORK_JSON"'

# 7. Error handling via stub and fake server
ERROR_JSON=$(python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, '$ROOT/bin/lib')
import runtimes

prompt = 'TRIGGER_ERROR'
argv, stdin_text = runtimes.command('qwen-code', prompt, 'qwen-max')
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
lines = stdout.splitlines()
parsed = runtimes.scan('qwen-code', lines)
print(json.dumps(parsed))
")

check "qwen-code scan(): error events parse as status ERROR with error message" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"result\"][\"status\"] == \"ERROR\"; assert \"DashScope API error\" in r[\"result\"][\"error\"]" "$ERROR_JSON"'

TURN_FAILED_JSON=$(python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, '$ROOT/bin/lib')
import runtimes

prompt = 'TRIGGER_TURN_FAILED'
argv, stdin_text = runtimes.command('qwen-code', prompt, 'qwen-max')
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
lines = stdout.splitlines()
parsed = runtimes.scan('qwen-code', lines)
print(json.dumps(parsed))
")

check "qwen-code scan(): turn.failed events parse as status ERROR" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"result\"][\"status\"] == \"ERROR\"; assert \"DashScope task execution failed\" in r[\"result\"][\"error\"]" "$TURN_FAILED_JSON"'

# 8. scan() edge cases and boundary tests
check "qwen-code scan(): empty stream yields default structure" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
r = runtimes.scan(\"qwen-code\", [])
assert r[\"tokens\"] == 0
assert r[\"tools\"] == 0
assert r[\"conversation_id\"] is None
assert r[\"result\"] is None
"'

check "qwen-code scan(): incomplete stream without result returns result=None" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
stream = [
    json.dumps({\"type\": \"init\", \"session_id\": \"ses_inc\"}),
    json.dumps({\"type\": \"tool_use\", \"name\": \"bash\"})
]
r = runtimes.scan(\"qwen-code\", stream)
assert r[\"tokens\"] == 0
assert r[\"tools\"] == 1
assert r[\"conversation_id\"] == \"ses_inc\"
assert r[\"result\"] is None
"'

check "qwen-code scan(): ignores malformed, non-JSON, and non-dict lines safely" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
stream = [
    \"not a json line\",
    \"   \",
    \"[1, 2, 3]\",
    \"42\",
    json.dumps({\"type\": \"init\", \"session_id\": \"ses_clean\"}),
    json.dumps({\"type\": \"result\", \"status\": \"success\"})
]
r = runtimes.scan(\"qwen-code\", stream)
assert r[\"conversation_id\"] == \"ses_clean\"
assert r[\"result\"][\"status\"] == \"SUCCESS\"
"'

check "qwen-code scan(): token calculation when total_tokens missing from stats" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
stream = [
    json.dumps({\"type\": \"result\", \"status\": \"success\", \"stats\": {\"input_tokens\": 500, \"output_tokens\": 150, \"cached_tokens\": 50}})
]
r = runtimes.scan(\"qwen-code\", stream)
assert r[\"tokens\"] == 600, f\"expected 600, got {r[\"tokens\"]}\"
"'

check "qwen-code scan(): token calculation boundary when cached > total" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
stream = [
    json.dumps({\"type\": \"result\", \"status\": \"success\", \"stats\": {\"total_tokens\": 100, \"cached\": 200}})
]
r = runtimes.scan(\"qwen-code\", stream)
assert r[\"tokens\"] == 0, f\"expected 0, got {r[\"tokens\"]}\"
"'

check "qwen-code scan(): alternative session ID field names (sessionId, thread_id, conversation_id)" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
for field, val in [(\"sessionId\", \"sid_1\"), (\"thread_id\", \"sid_2\"), (\"conversation_id\", \"sid_3\")]:
    r = runtimes.scan(\"qwen-code\", [json.dumps({field: val})])
    assert r[\"conversation_id\"] == val, f\"failed for {field}: expected {val}, got {r[\"conversation_id\"]}\"
"'

check "qwen-code scan(): duplicate session IDs preserve first occurrence" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
stream = [
    json.dumps({\"type\": \"init\", \"session_id\": \"first_session\"}),
    json.dumps({\"type\": \"message\", \"session_id\": \"second_session\"})
]
r = runtimes.scan(\"qwen-code\", stream)
assert r[\"conversation_id\"] == \"first_session\"
"'

check "qwen-code scan(): tool_call and tool event types increment tools count" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
stream = [
    json.dumps({\"type\": \"tool_use\"}),
    json.dumps({\"type\": \"tool_call\"}),
    json.dumps({\"type\": \"tool\"})
]
r = runtimes.scan(\"qwen-code\", stream)
assert r[\"tools\"] == 3, f\"expected tools=3, got {r[\"tools\"]}\"
"'

check "qwen-code scan(): response text truncation to last 4000 chars" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
long_text = \"A\" * 5000
stream = [
    json.dumps({\"type\": \"text\", \"text\": long_text}),
    json.dumps({\"type\": \"result\", \"status\": \"success\"})
]
r = runtimes.scan(\"qwen-code\", stream)
assert len(r[\"result\"][\"response\"]) == 4000
"'

# 9. scan_file('qwen-code', path) helper
check "qwen-code scan_file(): parses events from file and handles missing file" \
  'python3 -c "
import json, os, sys, tempfile
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
with tempfile.NamedTemporaryFile(\"w+\", encoding=\"utf-8\") as tf:
    tf.write(json.dumps({\"type\": \"init\", \"session_id\": \"ses_f\"}) + \"\n\")
    tf.write(json.dumps({\"type\": \"result\", \"status\": \"success\", \"stats\": {\"total_tokens\": 800, \"cached\": 100}}) + \"\n\")
    tf.flush()
    res = runtimes.scan_file(\"qwen-code\", tf.name)
    assert res[\"conversation_id\"] == \"ses_f\"
    assert res[\"tokens\"] == 700
    assert res[\"result\"][\"status\"] == \"SUCCESS\"

# Missing file returns default state
missing = runtimes.scan_file(\"qwen-code\", \"/path/does/not/exist.jsonl\")
assert missing[\"tokens\"] == 0
assert missing[\"result\"] is None
"'

# 10. Fake server logged requests
check "fake server saw and logged requests" \
  'grep -q "session=" "$T/server.log"'

# 11. Run Python unittest suite against the running fake server
python3 "$ROOT/tests/test_runtime_qwen.py" || exit 1

echo "qwen runtime tests: $([ $fail = 0 ] && echo ALL OK || echo FAILED)"
exit $fail

