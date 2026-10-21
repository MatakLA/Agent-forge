#!/usr/bin/env bash
# Integration test for mistral-vibe runtime in bin/lib/runtimes.py against fake-mistral-vibe-server.js.
# Public interface: bash tests/test_runtime_mistral_vibe.sh (runs with no real network call, exits 0 on success).
# Invariants: drives command() and scan()/scan_file(), verifies {tokens, tools, conversation_id, result}
# shape, verifies exact token and cost calculations, and asserts command() never puts prompts or secrets in argv.
# Grounded from bin/lib/runtimes.py (Task 0204):
# - RUNTIMES["mistral-vibe"]: key="mistral.key", hosts=["api.mistral.ai"], api=False
# - MISTRAL_VIBE_QUIET: ["--output-format", "stream-json"]
# - cost_model("mistral-vibe"): kind="unknown", detail="Mistral Vibe (mistral-vibe) subscription or API pricing is managed in Mistral AI, not tracked in Agent-Forge"
# - command("mistral-vibe", ...): argv=["mistral-vibe", "--output-format", "stream-json", ...], stdin=prompt
# - scan("mistral-vibe", ...): initial tokens=None, parses session, tool_use, message, step_finish, result, error, turn.failed.

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

# 1. Metadata and predicate checks for mistral-vibe
check "mistral-vibe runtime: registered in RUNTIMES" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
entry = runtimes.RUNTIMES[\"mistral-vibe\"]
assert entry[\"image\"] == runtimes.AGY_IMAGE
assert entry[\"key\"] == \"mistral.key\"
assert entry[\"hosts\"] == [\"api.mistral.ai\"]
assert entry[\"api\"] is False
"'

check "mistral-vibe runtime: is_api() returns False" \
  'python3 -c "import sys; sys.path.insert(0, \"$ROOT/bin/lib\"); import runtimes; assert runtimes.is_api(\"mistral-vibe\") is False"'

check "mistral-vibe runtime: uses_endpoint() returns False" \
  'python3 -c "import sys; sys.path.insert(0, \"$ROOT/bin/lib\"); import runtimes; assert runtimes.uses_endpoint(\"mistral-vibe\") is False"'

check "mistral-vibe runtime: hosts() returns Mistral API endpoint" \
  'python3 -c "import sys; sys.path.insert(0, \"$ROOT/bin/lib\"); import runtimes; assert runtimes.hosts(\"mistral-vibe\") == [\"api.mistral.ai\"]"'

check "mistral-vibe runtime: cost_model() reports unknown pricing honestly" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
cm = runtimes.cost_model(\"mistral-vibe\")
assert cm[\"kind\"] == \"unknown\"
assert \"Mistral Vibe\" in cm[\"detail\"]
assert \"not tracked in Agent-Forge\" in cm[\"detail\"]
assert \"USD\" not in cm[\"detail\"]
"'

# 2. command('mistral-vibe', ...) contract and argv security checks
check "mistral-vibe command(): starts with mistral-vibe binary and quiet flags" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
argv, stdin = runtimes.command(\"mistral-vibe\", \"prompt text\", \"codestral-latest\")
assert argv[0] == \"mistral-vibe\"
for flag in runtimes.MISTRAL_VIBE_QUIET:
    assert flag in argv
assert \"--model\" in argv and \"codestral-latest\" in argv
"'

check "mistral-vibe command(): prompt passed on stdin and omitted from argv" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
p = \"Very long prompt content for testing Mistral Vibe integration: 🚀 修复漏洞\" * 100
argv, stdin = runtimes.command(\"mistral-vibe\", p, \"codestral-latest\")
assert stdin == p
assert p not in argv
"'

check "mistral-vibe command(): never leaks secrets in argv" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
argv, stdin = runtimes.command(\"mistral-vibe\", \"task\", \"codestral-latest\", budget_usd=5.0)
argv_str = \" \".join(argv)
assert \"mistral.key\" not in argv_str
assert \"MISTRAL_API_KEY\" not in argv_str
assert \"sk-\" not in argv_str
"'

check "mistral-vibe command(): handles model and conversation parameters" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
# With model and conversation
argv, _ = runtimes.command(\"mistral-vibe\", \"task\", \"codestral-latest\", conversation=\"ses_123\")
assert \"--model\" in argv and \"codestral-latest\" in argv
assert \"--resume\" in argv and \"ses_123\" in argv

# Without model and conversation
argv_empty, _ = runtimes.command(\"mistral-vibe\", \"task\", None, conversation=None)
assert \"--model\" not in argv_empty
assert \"--resume\" not in argv_empty
"'

# 3. Start fake Mistral Vibe server
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
export MISTRAL_VIBE_SERVER_PORT="$PORT"

if command -v node >/dev/null 2>&1; then
  node "$ROOT/tests/fixtures/fake-mistral-vibe-server.js" "$PORT" > "$T/server.log" 2>&1 &
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
        sid = j.get('conversation_id') or j.get('session_id') or j.get('sessionId') or j.get('thread_id') or j.get('session') or 'ses_mistral_vibe_test_01'
        sys.stderr.write(f'{self.command} {self.path} model={j.get(\"model\", \"\")} session={sid} bytes={len(body)}\n')
        sys.stderr.flush()
        if self.path in ('/health', '/healthz'):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{\"status\":\"ok\"}')
            return
        if '/error' in self.path or j.get('error'):
            err = j.get('error') or 'Mistral API error: invalid API key'
            events = [
                {'type': 'session', 'conversation_id': sid},
                {'type': 'error', 'message': err},
                {'type': 'step_finish', 'part': {'reason': 'error', 'error': err}, 'usage': {'total_tokens': 100, 'input_tokens': 100, 'output_tokens': 0, 'cached_tokens': 0}}
            ]
        elif '/failed' in self.path or '/turn-failed' in self.path or j.get('turn_failed'):
            events = [
                {'type': 'session', 'conversation_id': sid},
                {'type': 'turn.failed', 'error': {'message': j.get('turn_failed') or 'Mistral Vibe task execution failed'}}
            ]
        elif '/result' in self.path or j.get('use_result_event'):
            events = [
                {'type': 'session', 'conversation_id': sid},
                {'type': 'tool_call', 'name': 'bash', 'tool': 'bash'},
                {'type': 'message', 'text': 'Completed task with Mistral Vibe.'},
                {'type': 'result', 'status': 'success', 'response': 'Completed task with Mistral Vibe.', 'usage': {'total_tokens': 1500, 'input_tokens': 1200, 'output_tokens': 300, 'cached_tokens': 200}}
            ]
        else:
            events = [
                {'type': 'session', 'conversation_id': sid},
                {'type': 'tool_use', 'name': 'bash', 'part': {'type': 'tool_use', 'command': 'git status'}},
                {'type': 'message', 'role': 'assistant', 'text': 'Completed task with Mistral Vibe.'},
                {'type': 'step_finish', 'part': {'reason': 'stop'}, 'usage': {'total_tokens': 1500, 'input_tokens': 1200, 'output_tokens': 300, 'cached_tokens': 200}}
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
sys.stderr.write(f'fake Mistral Vibe server listening on 127.0.0.1:{port}\n')
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

# 4. Create stub CLI standing in for mistral-vibe binary
cat > "$T/bin/mistral-vibe" <<'EOF'
#!/usr/bin/env python3
"""Stub standing in for Mistral Vibe CLI binary during tests."""
import json
import os
import sys
import urllib.request

port = os.environ.get("MISTRAL_VIBE_SERVER_PORT", "8099")
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
elif "TRIGGER_RESULT_EVENT" in prompt:
    url = f"http://127.0.0.1:{port}/result"

payload = json.dumps({
    "prompt": prompt,
    "args": args,
    "conversation_id": session_id,
    "model": model,
}).encode("utf-8")

req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req) as resp:
        for line in resp:
            sys.stdout.write(line.decode("utf-8"))
except Exception as e:
    sys.stderr.write(f"mistral-vibe stub error: {e}\n")
    sys.exit(1)
EOF
chmod +x "$T/bin/mistral-vibe"
export PATH="$T/bin:$PATH"

# 5. Drive mistral-vibe command() through CLI stub against fake server, feed lines to scan()
RUN_JSON=$(python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, '$ROOT/bin/lib')
import runtimes

prompt = 'Implement Mistral Vibe runtime integration tests'
argv, stdin_text = runtimes.command('mistral-vibe', prompt, 'codestral-latest')
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
if proc.returncode != 0:
    sys.stderr.write(f'stub execution error: {stderr}\n')
    sys.exit(proc.returncode)

lines = stdout.splitlines()
parsed = runtimes.scan('mistral-vibe', lines)
print(json.dumps(parsed))
")

check "mistral-vibe scan(): tokens counts processed tokens excluding cache (1500 - 200 = 1300)" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"tokens\"] == 1300, \"expected 1300 tokens, got %s\" % r[\"tokens\"]" "$RUN_JSON"'

check "mistral-vibe scan(): tools count increments on tool_use" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"tools\"] == 1, \"expected tools=1, got %s\" % r[\"tools\"]" "$RUN_JSON"'

check "mistral-vibe scan(): conversation_id parsed from session event" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"conversation_id\"] == \"ses_mistral_vibe_test_01\", \"got %s\" % r[\"conversation_id\"]" "$RUN_JSON"'

check "mistral-vibe scan(): result status is SUCCESS" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"result\"][\"status\"] == \"SUCCESS\"" "$RUN_JSON"'

check "mistral-vibe scan(): response text extracted from assistant message" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"result\"][\"response\"] == \"Completed task with Mistral Vibe.\"" "$RUN_JSON"'

check "mistral-vibe scan(): cost_usd is None by default (unknown cost model)" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"result\"][\"cost_usd\"] is None" "$RUN_JSON"'

# 6. Result event with prices calculation
RESULT_JSON=$(python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, '$ROOT/bin/lib')
import runtimes

prompt = 'TRIGGER_RESULT_EVENT'
argv, stdin_text = runtimes.command('mistral-vibe', prompt, 'codestral-latest')
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
lines = stdout.splitlines()
parsed = runtimes.scan('mistral-vibe', lines, prices='2.0,0.5,6.0')
print(json.dumps(parsed))
")

check "mistral-vibe scan(): result event cost_usd computed accurately from prices formula" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"result\"][\"cost_usd\"] == 0.0039, \"got %s\" % r[\"result\"][\"cost_usd\"]" "$RESULT_JSON"'

# 7. Rework / resume existing conversation session
REWORK_JSON=$(python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, '$ROOT/bin/lib')
import runtimes

prompt = 'Follow-up task on same session'
conv = 'ses_mistral_rework_99'
argv, stdin_text = runtimes.command('mistral-vibe', prompt, 'codestral-latest', conversation=conv)
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
lines = stdout.splitlines()
parsed = runtimes.scan('mistral-vibe', lines)
print(json.dumps(parsed))
")

check "mistral-vibe rework: command() preserves session and scan() extracts resumed conversation_id" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"conversation_id\"] == \"ses_mistral_rework_99\"" "$REWORK_JSON"'

# 8. Error handling via stub and fake server
ERROR_JSON=$(python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, '$ROOT/bin/lib')
import runtimes

prompt = 'TRIGGER_ERROR'
argv, stdin_text = runtimes.command('mistral-vibe', prompt, 'codestral-latest')
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
lines = stdout.splitlines()
parsed = runtimes.scan('mistral-vibe', lines)
print(json.dumps(parsed))
")

check "mistral-vibe scan(): error events parse as status ERROR with error message" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"result\"][\"status\"] == \"ERROR\"; assert \"Mistral API error\" in r[\"result\"][\"error\"]" "$ERROR_JSON"'

TURN_FAILED_JSON=$(python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, '$ROOT/bin/lib')
import runtimes

prompt = 'TRIGGER_TURN_FAILED'
argv, stdin_text = runtimes.command('mistral-vibe', prompt, 'codestral-latest')
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
lines = stdout.splitlines()
parsed = runtimes.scan('mistral-vibe', lines)
print(json.dumps(parsed))
")

check "mistral-vibe scan(): turn.failed events parse as status ERROR" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"result\"][\"status\"] == \"ERROR\"; assert \"Mistral Vibe task execution failed\" in r[\"result\"][\"error\"]" "$TURN_FAILED_JSON"'

# 9. scan() edge cases and boundary tests
check "mistral-vibe scan(): empty stream yields tokens=None and default structure" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
r = runtimes.scan(\"mistral-vibe\", [])
assert r[\"tokens\"] is None
assert r[\"tools\"] == 0
assert r[\"conversation_id\"] is None
assert r[\"result\"] is None
"'

check "mistral-vibe scan(): incomplete stream without result returns result=None" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
stream = [
    json.dumps({\"type\": \"session\", \"conversation_id\": \"ses_inc\"}),
    json.dumps({\"type\": \"tool_use\", \"name\": \"bash\"})
]
r = runtimes.scan(\"mistral-vibe\", stream)
assert r[\"tokens\"] is None
assert r[\"tools\"] == 1
assert r[\"conversation_id\"] == \"ses_inc\"
assert r[\"result\"] is None
"'

check "mistral-vibe scan(): ignores malformed, non-JSON, and non-dict lines safely" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
stream = [
    \"not a json line\",
    \"   \",
    \"[1, 2, 3]\",
    \"42\",
    json.dumps({\"type\": \"session\", \"conversation_id\": \"ses_clean\"}),
    json.dumps({\"type\": \"step_finish\", \"part\": {\"reason\": \"stop\"}})
]
r = runtimes.scan(\"mistral-vibe\", stream)
assert r[\"conversation_id\"] == \"ses_clean\"
assert r[\"result\"][\"status\"] == \"SUCCESS\"
"'

check "mistral-vibe scan(): token calculation when total_tokens missing from usage" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
stream = [
    json.dumps({\"type\": \"step_finish\", \"part\": {\"reason\": \"stop\"}, \"usage\": {\"input_tokens\": 500, \"output_tokens\": 150, \"cached_tokens\": 50}})
]
r = runtimes.scan(\"mistral-vibe\", stream)
assert r[\"tokens\"] == 600, \"expected 600, got %s\" % r[\"tokens\"]
"'

check "mistral-vibe scan(): token calculation boundary when cached > total" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
stream = [
    json.dumps({\"type\": \"step_finish\", \"part\": {\"reason\": \"stop\"}, \"usage\": {\"total_tokens\": 100, \"cached\": 200}})
]
r = runtimes.scan(\"mistral-vibe\", stream)
assert r[\"tokens\"] == 0, \"expected 0, got %s\" % r[\"tokens\"]
"'

check "mistral-vibe scan(): token calculation remains None when usage missing" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
stream = [
    json.dumps({\"type\": \"step_finish\", \"part\": {\"reason\": \"stop\"}})
]
r = runtimes.scan(\"mistral-vibe\", stream)
assert r[\"tokens\"] is None, \"expected None, got %s\" % r[\"tokens\"]
"'

check "mistral-vibe scan(): alternative session ID field names (sessionId, thread_id, conversation_id, part-nested)" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
for field, val in [(\"sessionId\", \"sid_1\"), (\"thread_id\", \"sid_2\"), (\"conversation_id\", \"sid_3\"), (\"session\", \"sid_4\")]:
    r = runtimes.scan(\"mistral-vibe\", [json.dumps({field: val})])
    assert r[\"conversation_id\"] == val, \"failed for %s: expected %s, got %s\" % (field, val, r[\"conversation_id\"])

r_part = runtimes.scan(\"mistral-vibe\", [json.dumps({\"part\": {\"sessionId\": \"sid_nested\"}})])
assert r_part[\"conversation_id\"] == \"sid_nested\"
"'

check "mistral-vibe scan(): duplicate session IDs preserve first occurrence" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
stream = [
    json.dumps({\"type\": \"session\", \"conversation_id\": \"first_session\"}),
    json.dumps({\"type\": \"message\", \"conversation_id\": \"second_session\"})
]
r = runtimes.scan(\"mistral-vibe\", stream)
assert r[\"conversation_id\"] == \"first_session\"
"'

check "mistral-vibe scan(): tool event type variants increment tools count" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
stream = [
    json.dumps({\"type\": \"tool_use\"}),
    json.dumps({\"type\": \"tool_call\"}),
    json.dumps({\"type\": \"tool\"}),
    json.dumps({\"type\": \"tool_invocation\"}),
    json.dumps({\"type\": \"tool_execution\"}),
    json.dumps({\"part\": {\"type\": \"tool\"}}),
    json.dumps({\"part\": {\"tool\": \"read_file\"}}),
    json.dumps({\"tool\": \"grep\"})
]
r = runtimes.scan(\"mistral-vibe\", stream)
assert r[\"tools\"] == 8, \"expected tools=8, got %s\" % r[\"tools\"]
"'

check "mistral-vibe scan(): response text truncation to last 4000 chars and unicode preservation" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
long_text = \"M\" * 5000 + \"🚀\"
stream = [
    json.dumps({\"type\": \"text\", \"text\": long_text}),
    json.dumps({\"type\": \"step_finish\", \"part\": {\"reason\": \"stop\"}})
]
r = runtimes.scan(\"mistral-vibe\", stream)
assert len(r[\"result\"][\"response\"]) == 4000
assert \"🚀\" in r[\"result\"][\"response\"]
"'

# 10. scan_file('mistral-vibe', path) helper
check "mistral-vibe scan_file(): parses events from file and handles missing file" \
  'python3 -c "
import json, os, sys, tempfile
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
with tempfile.NamedTemporaryFile(\"w+\", encoding=\"utf-8\") as tf:
    tf.write(json.dumps({\"type\": \"session\", \"conversation_id\": \"ses_f\"}) + \"\n\")
    tf.write(json.dumps({\"type\": \"step_finish\", \"part\": {\"reason\": \"stop\"}, \"usage\": {\"total_tokens\": 800, \"cached\": 100}}) + \"\n\")
    tf.flush()
    res = runtimes.scan_file(\"mistral-vibe\", tf.name)
    assert res[\"conversation_id\"] == \"ses_f\"
    assert res[\"tokens\"] == 700
    assert res[\"result\"][\"status\"] == \"SUCCESS\"

# Missing file returns default state with tokens=None
missing = runtimes.scan_file(\"mistral-vibe\", \"/path/does/not/exist.jsonl\")
assert missing[\"tokens\"] is None
assert missing[\"result\"] is None
"'

# 11. Fake server logged requests
check "fake server saw and logged requests" \
  'grep -q "session=" "$T/server.log"'

# 12. Run Python unittest suite against the running fake server
python3 "$ROOT/tests/test_runtime_mistral_vibe.py" || exit 1

echo "mistral-vibe runtime tests: $([ $fail = 0 ] && echo ALL OK || echo FAILED)"
exit $fail
