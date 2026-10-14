#!/usr/bin/env bash
# Integration test for copilot runtime in bin/lib/runtimes.py against fake-copilot-server.js.
# Public interface: bash tests/test_runtime_copilot.sh (runs with no real network call, exits 0 on success).
# Invariants: drives command() and scan()/scan_file(), verifies {tokens, tools, conversation_id, result}
# shape, verifies exact token and cost calculations, and asserts command() never puts prompts or secrets in argv.
# Grounded from bin/lib/runtimes.py (Task 0212):
# - RUNTIMES["copilot"]: key=None, hosts=["api.githubcopilot.com", "api.github.com"], api=False
# - COPILOT_QUIET: ["--output-format", "stream-json"]
# - cost_model("copilot"): kind="unknown", detail="GitHub Copilot (copilot) is covered by a GitHub Copilot subscription; token pricing is not tracked in Agent-Forge"
# - command("copilot", ...): argv=["copilot", "--output-format", "stream-json", ...], stdin=prompt
# - scan("copilot", ...): initial tokens=None, parses session, tool, message, step_finish, result, error, turn.failed.

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

# 1. Metadata and predicate checks for copilot
check "copilot runtime: registered in RUNTIMES" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
entry = runtimes.RUNTIMES[\"copilot\"]
assert entry[\"image\"] == runtimes.AGY_IMAGE
assert entry[\"key\"] is None
assert entry[\"hosts\"] == [\"api.githubcopilot.com\", \"api.github.com\"]
assert entry[\"api\"] is False
"'

check "copilot runtime: is_api() returns False" \
  'python3 -c "import sys; sys.path.insert(0, \"$ROOT/bin/lib\"); import runtimes; assert runtimes.is_api(\"copilot\") is False"'

check "copilot runtime: uses_endpoint() returns False" \
  'python3 -c "import sys; sys.path.insert(0, \"$ROOT/bin/lib\"); import runtimes; assert runtimes.uses_endpoint(\"copilot\") is False"'

check "copilot runtime: hosts() returns GitHub Copilot endpoints" \
  'python3 -c "import sys; sys.path.insert(0, \"$ROOT/bin/lib\"); import runtimes; assert runtimes.hosts(\"copilot\") == [\"api.githubcopilot.com\", \"api.github.com\"]"'

check "copilot runtime: cost_model() reports unknown pricing honestly" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
cm = runtimes.cost_model(\"copilot\")
assert cm[\"kind\"] == \"unknown\"
assert \"GitHub Copilot\" in cm[\"detail\"]
assert \"not tracked in Agent-Forge\" in cm[\"detail\"]
assert \"USD\" not in cm[\"detail\"]
assert \"$\" not in cm[\"detail\"]
"'

# 2. command('copilot', ...) contract and argv security checks
check "copilot command(): starts with copilot binary and quiet flags" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
argv, stdin = runtimes.command(\"copilot\", \"prompt text\", \"gpt-4o\")
assert argv[0] == \"copilot\"
for flag in runtimes.COPILOT_QUIET:
    assert flag in argv
assert \"--model\" in argv and \"gpt-4o\" in argv
"'

check "copilot command(): prompt passed on stdin and omitted from argv" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
p = \"Very long prompt content for testing GitHub Copilot CLI integration: 🚀 编写代码\" * 100
argv, stdin = runtimes.command(\"copilot\", p, \"gpt-4o\")
assert stdin == p
assert p not in argv
"'

check "copilot command(): never leaks secrets in argv" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
argv, stdin = runtimes.command(\"copilot\", \"task\", \"gpt-4o\", budget_usd=5.0)
argv_str = \" \".join(argv)
assert \"github.token\" not in argv_str
assert \"GITHUB_TOKEN\" not in argv_str
assert \"copilot.key\" not in argv_str
assert \"ghp_\" not in argv_str
assert \"gho_\" not in argv_str
assert \"sk-\" not in argv_str
"'

check "copilot command(): handles model and conversation parameters" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
# With model and conversation
argv, _ = runtimes.command(\"copilot\", \"task\", \"gpt-4o\", conversation=\"ses_123\")
assert \"--model\" in argv and \"gpt-4o\" in argv
assert \"--resume\" in argv and \"ses_123\" in argv

# Without model and conversation
argv_empty, _ = runtimes.command(\"copilot\", \"task\", None, conversation=None)
assert \"--model\" not in argv_empty
assert \"--resume\" not in argv_empty
"'

# 3. Start fake Copilot server
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
export COPILOT_SERVER_PORT="$PORT"

if command -v node >/dev/null 2>&1; then
  node "$ROOT/tests/fixtures/fake-copilot-server.js" "$PORT" > "$T/server.log" 2>&1 &
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
        sid = j.get('sessionID') or j.get('session_id') or j.get('sessionId') or j.get('session') or 'ses_copilot_test_01'
        sys.stderr.write(f'{self.command} {self.path} session={sid} bytes={len(body)}\n')
        sys.stderr.flush()
        if self.path in ('/health', '/healthz'):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{\"status\":\"ok\"}')
            return
        if '/error' in self.path or j.get('error'):
            err = j.get('error') or 'simulated rate limit'
            events = [
                {'type': 'start', 'sessionID': sid},
                {'type': 'result', 'status': 'error', 'error': err, 'sessionID': sid, 'part': {'reason': 'error', 'error': err}}
            ]
        else:
            events = [
                {'type': 'start', 'sessionID': sid, 'part': {'id': 'part_start_01'}},
                {'type': 'message', 'sessionID': sid, 'part': {'text': 'Done: wrote hello.txt.'}},
                {'type': 'tool', 'name': 'bash', 'sessionID': sid, 'part': {'type': 'tool', 'tool': 'bash', 'command': 'echo \"hello from copilot\" > hello.txt'}},
                {'type': 'result', 'status': 'success', 'sessionID': sid, 'part': {'reason': 'stop'}}
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
sys.stderr.write(f'fake Copilot server listening on 127.0.0.1:{port}\n')
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

# 4. Create stub CLI standing in for copilot binary
cat > "$T/bin/copilot" <<'EOF'
#!/usr/bin/env python3
"""Stub standing in for Copilot CLI binary during tests."""
import json
import os
import sys
import urllib.request

port = os.environ.get("COPILOT_SERVER_PORT", "8098")
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
    sys.stderr.write(f"copilot stub error: {e}\n")
    sys.exit(1)
EOF
chmod +x "$T/bin/copilot"
export PATH="$T/bin:$PATH"

# 5. Drive copilot command() through CLI stub against fake server, feed lines to scan()
RUN_JSON=$(python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, '$ROOT/bin/lib')
import runtimes

prompt = 'Implement Copilot runtime integration tests'
argv, stdin_text = runtimes.command('copilot', prompt, 'gpt-4o')
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
if proc.returncode != 0:
    sys.stderr.write(f'stub execution error: {stderr}\n')
    sys.exit(proc.returncode)

lines = stdout.splitlines()
parsed = runtimes.scan('copilot', lines)
print(json.dumps(parsed))
")

check "copilot scan(): tools count increments on tool event (tools == 1)" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"tools\"] == 1, f\"expected tools=1, got {r[\"tools\"]}\"" "$RUN_JSON"'

check "copilot scan(): conversation_id parsed from start event" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"conversation_id\"] == \"ses_copilot_test_01\", f\"got {r[\"conversation_id\"]}\"" "$RUN_JSON"'

check "copilot scan(): result status is SUCCESS" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"result\"][\"status\"] == \"SUCCESS\"" "$RUN_JSON"'

check "copilot scan(): response text extracted from message" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"result\"][\"response\"] == \"Done: wrote hello.txt.\"" "$RUN_JSON"'

check "copilot scan(): cost_usd is None by default (subscription cost model)" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"result\"][\"cost_usd\"] is None" "$RUN_JSON"'

check "copilot scan(): tokens is None by default (no usage in default fixture)" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"tokens\"] is None" "$RUN_JSON"'

# 6. Rework / resume existing conversation session
REWORK_JSON=$(python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, '$ROOT/bin/lib')
import runtimes

prompt = 'Follow-up task on same session'
conv = 'ses_copilot_rework_99'
argv, stdin_text = runtimes.command('copilot', prompt, 'gpt-4o', conversation=conv)
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
lines = stdout.splitlines()
parsed = runtimes.scan('copilot', lines)
print(json.dumps(parsed))
")

check "copilot rework: command() preserves session and scan() extracts resumed conversation_id" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"conversation_id\"] == \"ses_copilot_rework_99\"" "$REWORK_JSON"'

# 7. Error handling via stub and fake server
ERROR_JSON=$(python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, '$ROOT/bin/lib')
import runtimes

prompt = 'TRIGGER_ERROR'
argv, stdin_text = runtimes.command('copilot', prompt, 'gpt-4o')
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
lines = stdout.splitlines()
parsed = runtimes.scan('copilot', lines)
print(json.dumps(parsed))
")

check "copilot scan(): error events parse as status ERROR with error message" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"result\"][\"status\"] == \"ERROR\"; assert \"simulated rate limit\" in r[\"result\"][\"error\"]" "$ERROR_JSON"'

# 8. scan() edge cases and boundary tests
check "copilot scan(): empty stream yields tokens=None and default structure" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
r = runtimes.scan(\"copilot\", [])
assert r[\"tokens\"] is None
assert r[\"tools\"] == 0
assert r[\"conversation_id\"] is None
assert r[\"result\"] is None
"'

check "copilot scan(): incomplete stream without result returns result=None" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
stream = [
    json.dumps({\"type\": \"start\", \"sessionID\": \"ses_inc\"}),
    json.dumps({\"type\": \"tool\", \"name\": \"bash\"})
]
r = runtimes.scan(\"copilot\", stream)
assert r[\"tokens\"] is None
assert r[\"tools\"] == 1
assert r[\"conversation_id\"] == \"ses_inc\"
assert r[\"result\"] is None
"'

check "copilot scan(): ignores malformed, non-JSON, and non-dict lines safely" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
stream = [
    \"not a json line\",
    \"   \",
    \"[1, 2, 3]\",
    \"42\",
    json.dumps({\"type\": \"start\", \"sessionID\": \"ses_clean\"}),
    json.dumps({\"type\": \"result\", \"status\": \"success\"})
]
r = runtimes.scan(\"copilot\", stream)
assert r[\"conversation_id\"] == \"ses_clean\"
assert r[\"result\"][\"status\"] == \"SUCCESS\"
"'

check "copilot scan(): token calculation with total and cached tokens" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
stream = [
    json.dumps({\"type\": \"step_finish\", \"part\": {\"reason\": \"stop\"}, \"usage\": {\"total_tokens\": 1500, \"cached_tokens\": 200}})
]
r = runtimes.scan(\"copilot\", stream)
assert r[\"tokens\"] == 1300, f\"expected 1300, got {r[\"tokens\"]}\"
"'

check "copilot scan(): token calculation when total_tokens missing from usage" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
stream = [
    json.dumps({\"type\": \"step_finish\", \"part\": {\"reason\": \"stop\"}, \"usage\": {\"input_tokens\": 500, \"output_tokens\": 150, \"cached_tokens\": 50}})
]
r = runtimes.scan(\"copilot\", stream)
assert r[\"tokens\"] == 600, f\"expected 600, got {r[\"tokens\"]}\"
"'

check "copilot scan(): token calculation boundary when cached > total" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
stream = [
    json.dumps({\"type\": \"step_finish\", \"part\": {\"reason\": \"stop\"}, \"usage\": {\"total_tokens\": 100, \"cached\": 200}})
]
r = runtimes.scan(\"copilot\", stream)
assert r[\"tokens\"] == 0, f\"expected 0, got {r[\"tokens\"]}\"
"'

check "copilot scan(): token calculation remains None when usage missing" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
stream = [
    json.dumps({\"type\": \"step_finish\", \"part\": {\"reason\": \"stop\"}})
]
r = runtimes.scan(\"copilot\", stream)
assert r[\"tokens\"] is None, f\"expected None, got {r[\"tokens\"]}\"
"'

check "copilot scan(): alternative session ID field names (sessionID, sessionId, thread_id, conversation_id, part-nested)" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
for field, val in [(\"sessionID\", \"sid_1\"), (\"sessionId\", \"sid_2\"), (\"thread_id\", \"sid_3\"), (\"conversation_id\", \"sid_4\"), (\"session\", \"sid_5\")]:
    r = runtimes.scan(\"copilot\", [json.dumps({field: val})])
    assert r[\"conversation_id\"] == val, f\"failed for {field}: expected {val}, got {r[\"conversation_id\"]}\"

r_part = runtimes.scan(\"copilot\", [json.dumps({\"part\": {\"sessionID\": \"sid_nested\"}})])
assert r_part[\"conversation_id\"] == \"sid_nested\"
"'

check "copilot scan(): duplicate session IDs preserve first occurrence" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
stream = [
    json.dumps({\"type\": \"start\", \"sessionID\": \"first_session\"}),
    json.dumps({\"type\": \"message\", \"sessionID\": \"second_session\"})
]
r = runtimes.scan(\"copilot\", stream)
assert r[\"conversation_id\"] == \"first_session\"
"'

check "copilot scan(): tool event type variants increment tools count" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
stream = [
    json.dumps({\"type\": \"tool\"}),
    json.dumps({\"type\": \"tool_use\"}),
    json.dumps({\"type\": \"tool_call\"}),
    json.dumps({\"type\": \"tool_invocation\"}),
    json.dumps({\"type\": \"tool_execution\"}),
    json.dumps({\"part\": {\"type\": \"tool\"}}),
    json.dumps({\"part\": {\"tool\": \"read_file\"}}),
    json.dumps({\"tool\": \"grep\"})
]
r = runtimes.scan(\"copilot\", stream)
assert r[\"tools\"] == 8, f\"expected tools=8, got {r[\"tools\"]}\"
"'

check "copilot scan(): response text truncation to last 4000 chars and unicode preservation" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
long_text = \"C\" * 5000 + \"🚀\"
stream = [
    json.dumps({\"type\": \"text\", \"text\": long_text}),
    json.dumps({\"type\": \"result\", \"status\": \"success\"})
]
r = runtimes.scan(\"copilot\", stream)
assert len(r[\"result\"][\"response\"]) == 4000
assert \"🚀\" in r[\"result\"][\"response\"]
"'

check "copilot scan(): cost_usd remains None even when prices argument is provided" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
stream = [
    json.dumps({\"type\": \"result\", \"status\": \"success\", \"usage\": {\"total_tokens\": 1000}})
]
r = runtimes.scan(\"copilot\", stream, prices=\"2.0,0.5,6.0\")
assert r[\"result\"][\"cost_usd\"] is None
"'

# 9. scan_file('copilot', path) helper
check "copilot scan_file(): parses events from file and handles missing file" \
  'python3 -c "
import json, os, sys, tempfile
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
with tempfile.NamedTemporaryFile(\"w+\", encoding=\"utf-8\") as tf:
    tf.write(json.dumps({\"type\": \"start\", \"sessionID\": \"ses_f\"}) + \"\n\")
    tf.write(json.dumps({\"type\": \"step_finish\", \"part\": {\"reason\": \"stop\"}, \"usage\": {\"total_tokens\": 800, \"cached\": 100}}) + \"\n\")
    tf.flush()
    res = runtimes.scan_file(\"copilot\", tf.name)
    assert res[\"conversation_id\"] == \"ses_f\"
    assert res[\"tokens\"] == 700
    assert res[\"result\"][\"status\"] == \"SUCCESS\"
    assert res[\"result\"][\"cost_usd\"] is None

# Missing file returns default state with tokens=None
missing = runtimes.scan_file(\"copilot\", \"/path/does/not/exist.jsonl\")
assert missing[\"tokens\"] is None
assert missing[\"result\"] is None
"'

# 10. Fake server logged requests
check "fake server saw and logged requests" \
  'grep -q "session=" "$T/server.log"'

# 11. Run Python unittest suite against the running fake server
python3 "$ROOT/tests/test_runtime_copilot.py" || exit 1

echo "copilot runtime tests: $([ $fail = 0 ] && echo ALL OK || echo FAILED)"
exit $fail
