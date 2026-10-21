#!/usr/bin/env bash
# Integration test for anthropic-compat runtime in bin/lib/runtimes.py against fake Anthropic Messages server.
# Public interface: bash tests/test_runtime_anthropic_compat.sh (runs with no real network call, exits 0 on success).
# Invariants: starts fake Anthropic Messages server, drives command() and scan()/scan_file(),
# asserts API key never appears in argv (stdin-only JSON payload), verifies OpenAI conversation history is split
# into system+messages, asserts scan() sums input_tokens and output_tokens from usage, handles both success
# and error server responses, runs tests/test_runtime_anthropic_compat.py, and propagates exit codes strictly.

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

# 1. Metadata and registration checks for anthropic-compat runtime
check "anthropic-compat runtime: registered in RUNTIMES with correct metadata" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
assert \"anthropic-compat\" in runtimes.RUNTIMES
entry = runtimes.RUNTIMES[\"anthropic-compat\"]
assert entry[\"image\"] == runtimes.API_IMAGE
assert entry[\"key\"] == \"compat.key\"
assert entry[\"hosts\"] == []
assert entry[\"api\"] is True
assert entry.get(\"endpoint\") is True
assert entry.get(\"optional_key\") is True
assert entry.get(\"prices\") == \"PRICE_COMPAT\"
"'

check "anthropic-compat runtime: is_api() returns True" \
  'python3 -c "import sys; sys.path.insert(0, \"$ROOT/bin/lib\"); import runtimes; assert runtimes.is_api(\"anthropic-compat\") is True"'

check "anthropic-compat runtime: uses_endpoint() returns True" \
  'python3 -c "import sys; sys.path.insert(0, \"$ROOT/bin/lib\"); import runtimes; assert runtimes.uses_endpoint(\"anthropic-compat\") is True"'

check "anthropic-compat runtime: hosts() resolves host from endpoint URL" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
assert runtimes.hosts(\"anthropic-compat\", \"http://127.0.0.1:8099/v1\") == [\"127.0.0.1\"]
assert runtimes.hosts(\"anthropic-compat\", \"https://api.anthropic.com/v1\") == [\"api.anthropic.com\"]
assert runtimes.hosts(\"anthropic-compat\", \"\") == []
assert runtimes.hosts(\"anthropic-compat\", \"ftp://invalid\") == []
"'

# 2. command('anthropic-compat', ...) contract and argv security checks
check "anthropic-compat command(): argv is af-agent anthropic-compat exec and prompt is on stdin" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
argv, stdin_text = runtimes.command(\"anthropic-compat\", \"Fix auth handler\", \"claude-3-5-sonnet\")
assert argv == [\"af-agent\", \"anthropic-compat\", \"exec\"]
assert \"Fix auth handler\" not in argv
assert \"Fix auth handler\" in stdin_text
"'

check "anthropic-compat command(): API key and secrets never appear in argv (stdin-only JSON body)" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
secret = \"secret_api_key_test_value\"
prompt = f\"Please use key {secret}\"
argv, stdin_text = runtimes.command(\"anthropic-compat\", prompt, \"claude-3-5-sonnet\", left_min=15, budget_usd=5.0)
argv_str = \" \".join(argv)
assert secret not in argv_str
assert \"x-api-key\" not in argv_str
assert \"api-key\" not in argv_str
assert argv == [\"af-agent\", \"anthropic-compat\", \"exec\"]
assert secret in stdin_text
"'

check "anthropic-compat command(): OpenAI-shape conversation history split into system+messages correctly" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
openai_history = [
    {\"role\": \"system\", \"content\": \"You are an elite QA tester.\"},
    {\"role\": \"user\", \"content\": \"Verify endpoint security\"},
    {\"role\": \"assistant\", \"content\": \"Ready to audit.\"}
]
prompt = \"Run full penetration scan\"
argv, stdin_text = runtimes.command(\"anthropic-compat\", prompt, \"claude-3-5-sonnet\", conversation=openai_history)
body = json.loads(stdin_text)
assert body[\"system\"] == \"You are an elite QA tester.\"
assert len(body[\"messages\"]) == 3
assert body[\"messages\"][0] == {\"role\": \"user\", \"content\": \"Verify endpoint security\"}
assert body[\"messages\"][1] == {\"role\": \"assistant\", \"content\": \"Ready to audit.\"}
assert body[\"messages\"][2] == {\"role\": \"user\", \"content\": prompt}
for msg in body[\"messages\"]:
    assert msg[\"role\"] != \"system\"
"'

check "anthropic-compat command(): handles JSON string conversation history" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
json_hist = json.dumps([
    {\"role\": \"system\", \"content\": \"System instruction\"},
    {\"role\": \"user\", \"content\": \"First turn\"}
])
argv, stdin_text = runtimes.command(\"anthropic-compat\", \"Second turn\", \"claude-3-5-sonnet\", conversation=json_hist)
body = json.loads(stdin_text)
assert body[\"system\"] == \"System instruction\"
assert len(body[\"messages\"]) == 2
assert body[\"messages\"][0] == {\"role\": \"user\", \"content\": \"First turn\"}
assert body[\"messages\"][1] == {\"role\": \"user\", \"content\": \"Second turn\"}
"'

check "anthropic-compat command(): handles default model and string conversation session ID" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
argv, stdin_text = runtimes.command(\"anthropic-compat\", \"hello\", \"\", conversation=\"ses_test_session_42\")
body = json.loads(stdin_text)
assert body[\"model\"] == \"claude-3-5-sonnet-20241022\"
assert body[\"conversation_id\"] == \"ses_test_session_42\"
assert body[\"metadata\"] == {\"conversation_id\": \"ses_test_session_42\"}
"'

# 3. Determine an available local port and configure endpoint pointing at fake server
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
export ANTHROPIC_COMPAT_PORT="$PORT"
export PORT="$PORT"

printf 'COMPAT_BASE_URL=http://127.0.0.1:%s/v1\nPRICE_COMPAT=1.0,0.1,10.0\n' "$PORT" > "$T/etc/limits.conf"
export COMPAT_BASE_URL="http://127.0.0.1:$PORT/v1"
export AF_ENDPOINT="http://127.0.0.1:$PORT/v1"
export AF_TEST_BASE_URL="http://127.0.0.1:$PORT"
export ANTHROPIC_BASE_URL="http://127.0.0.1:$PORT/v1"

# 4. Start the fake Anthropic Messages server fixture
python3 -c "
import http.server, json, sys, time

port = int(sys.argv[1])

class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length > 0 else ''
        try:
            j = json.loads(body)
        except Exception:
            j = {}
        sid = j.get('conversation_id') or (j.get('metadata') or {}).get('conversation_id') or f'msg_{int(time.time()*1000)}'
        sys.stderr.write(f'{self.command} {self.path} session={sid} stream={j.get(\"stream\")} bytes={len(body)}\n')
        sys.stderr.flush()

        if self.path in ('/health', '/healthz'):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{\"status\":\"ok\"}')
            return

        # Error response handling
        if '/error' in self.path or j.get('error') or 'TRIGGER_ERROR' in body:
            err_msg = j.get('error') or 'simulated rate limit'
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'type': 'error',
                'error': {'type': 'invalid_request_error', 'message': err_msg}
            }).encode('utf-8'))
            return

        if self.path.endswith('/messages') or '/v1/messages' in self.path:
            is_stream = j.get('stream', True)
            if not is_stream:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                resp = {
                    'id': sid,
                    'type': 'message',
                    'role': 'assistant',
                    'model': j.get('model', 'claude-3-5-sonnet-20241022'),
                    'content': [{'type': 'text', 'text': 'Done: wrote hello.txt.'}],
                    'stop_reason': 'end_turn',
                    'usage': {'input_tokens': 1000, 'output_tokens': 50}
                }
                self.wfile.write(json.dumps(resp).encode('utf-8'))
                return

            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()

            events = [
                ('message_start', {
                    'type': 'message_start',
                    'message': {
                        'id': sid,
                        'type': 'message',
                        'role': 'assistant',
                        'model': j.get('model', 'claude-3-5-sonnet-20241022'),
                        'content': [],
                        'stop_reason': None,
                        'usage': {'input_tokens': 1000, 'output_tokens': 1}
                    }
                }),
                ('content_block_start', {
                    'type': 'content_block_start',
                    'index': 0,
                    'content_block': {'type': 'tool_use', 'id': 'toolu_01', 'name': 'Bash'}
                }),
                ('content_block_delta', {
                    'type': 'content_block_delta',
                    'index': 0,
                    'delta': {'type': 'input_json_delta', 'partial_json': '{\"command\": \"echo hello > hello.txt\"}'}
                }),
                ('content_block_stop', {
                    'type': 'content_block_stop',
                    'index': 0
                }),
                ('content_block_start', {
                    'type': 'content_block_start',
                    'index': 1,
                    'content_block': {'type': 'text', 'text': ''}
                }),
                ('content_block_delta', {
                    'type': 'content_block_delta',
                    'index': 1,
                    'delta': {'type': 'text_delta', 'text': 'Done: wrote hello.txt.'}
                }),
                ('content_block_stop', {
                    'type': 'content_block_stop',
                    'index': 1
                }),
                ('message_delta', {
                    'type': 'message_delta',
                    'delta': {'stop_reason': 'end_turn'},
                    'usage': {'output_tokens': 42}
                }),
                ('message_stop', {
                    'type': 'message_stop'
                })
            ]
            for ev_name, ev_data in events:
                payload = f'event: {ev_name}\ndata: {json.dumps(ev_data)}\n\n'
                self.wfile.write(payload.encode('utf-8'))
                self.wfile.flush()
            return

        self.send_response(404)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{\"error\":\"not faked\"}')

    def do_GET(self):
        if self.path in ('/health', '/healthz'):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{\"status\":\"ok\"}')
        else:
            self.do_POST()

    def log_message(self, format, *args):
        pass

server = http.server.HTTPServer(('127.0.0.1', port), Handler)
sys.stderr.write(f'fake Anthropic Messages server listening on 127.0.0.1:{port}\n')
sys.stderr.flush()
server.serve_forever()
" "$PORT" > "$T/server.log" 2>&1 &
SERVER_PID=$!

# Wait for fake server to accept connections
for _ in $(seq 1 40); do
  python3 -c "import socket; s=socket.socket(); s.connect(('127.0.0.1', $PORT)); s.close()" 2>/dev/null && break
  sleep 0.1
done

# 5. Create stub standing in for af-agent CLI binary during tests
cat > "$T/bin/af-agent" <<'EOF'
#!/usr/bin/env python3
"""Stub standing in for af-agent CLI binary during tests."""
import json
import os
import sys
import urllib.request
import urllib.error

args = sys.argv[1:]

# Invariants assertion: runtime must be anthropic-compat and command must be exec
if len(args) < 2 or args[0] != "anthropic-compat" or args[1] != "exec":
    sys.stderr.write(f"af-agent stub called with unexpected args: {args}\n")
    sys.exit(2)

# Ensure no secrets leak in argv
for arg in sys.argv:
    if "secret" in arg.lower() or "x-api-key" in arg:
        sys.stderr.write(f"FATAL: secret leaked in argv: {arg}\n")
        sys.exit(3)

# Stdin receives the prompt JSON payload built by command('anthropic-compat', ...)
stdin_raw = sys.stdin.read()
try:
    payload = json.loads(stdin_raw) if stdin_raw else {}
except Exception as e:
    sys.stderr.write(f"invalid stdin JSON: {e}\n")
    sys.exit(1)

endpoint = (
    os.environ.get("ANTHROPIC_BASE_URL")
    or os.environ.get("AF_ENDPOINT")
    or os.environ.get("COMPAT_BASE_URL")
    or f"http://127.0.0.1:{os.environ.get('PORT', '8099')}/v1"
)
endpoint = endpoint.rstrip("/")
if not endpoint.endswith("/messages") and not endpoint.endswith("/error"):
    endpoint = f"{endpoint}/messages"

has_error = (
    "TRIGGER_ERROR" in stdin_raw
    or "--trigger-error" in args
    or payload.get("error")
    or "/error" in endpoint
)

if has_error:
    base = endpoint.rsplit("/", 1)[0]
    endpoint = f"{base}/error"
    payload["error"] = "simulated rate limit"

# By default, request streaming unless STREAM=0
if "stream" not in payload:
    payload["stream"] = os.environ.get("STREAM", "1") != "0"

body_bytes = json.dumps(payload).encode("utf-8")
headers = {
    "Content-Type": "application/json",
    "x-api-key": os.environ.get("ANTHROPIC_API_KEY", "test-api-key"),
    "anthropic-version": "2023-06-01",
}

req = urllib.request.Request(endpoint, data=body_bytes, headers=headers)
try:
    with urllib.request.urlopen(req) as resp:
        for line in resp:
            sys.stdout.write(line.decode("utf-8"))
        sys.stdout.flush()
except urllib.error.HTTPError as e:
    err_body = e.read().decode("utf-8")
    sys.stdout.write(err_body + "\n")
    sys.stdout.flush()
except Exception as e:
    sys.stderr.write(f"af-agent stub error: {e}\n")
    sys.exit(1)
EOF
chmod +x "$T/bin/af-agent"
export PATH="$T/bin:$PATH"

# 6. Drive command('anthropic-compat', ...) against fake server and capture output
RUN_JSON=$(python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, '$ROOT/bin/lib')
import runtimes

prompt = 'Implement Anthropic Messages integration tests'
argv, stdin_text = runtimes.command('anthropic-compat', prompt, 'claude-3-5-sonnet')
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
if proc.returncode != 0:
    sys.stderr.write(f'stub execution error: {stderr}\n')
    sys.exit(proc.returncode)

lines = stdout.splitlines()
parsed = runtimes.scan('anthropic-compat', lines, prices='1.0,0.1,10.0')
print(json.dumps(parsed))
")

check "anthropic-compat scan(): sums input_tokens (1000) and output_tokens (42) to 1042" \
  'python3 -c "
import json, sys
r = json.loads(sys.argv[1])
assert r[\"tokens\"] == 1042, \"expected 1042 tokens, got %s\" % r[\"tokens\"]
assert r[\"result\"][\"usage\"][\"input_tokens\"] == 1000
assert r[\"result\"][\"usage\"][\"output_tokens\"] == 42
assert r[\"tokens\"] == r[\"result\"][\"usage\"][\"input_tokens\"] + r[\"result\"][\"usage\"][\"output_tokens\"]
" "$RUN_JSON"'

check "anthropic-compat scan(): tools count increments on tool_use (tools >= 1)" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"tools\"] >= 1, \"tools was %s\" % r[\"tools\"]" "$RUN_JSON"'

check "anthropic-compat scan(): conversation_id is extracted from message" \
  'python3 -c "import json, sys; r=json.loads(sys.argv[1]); assert r[\"conversation_id\"] is not None and len(r[\"conversation_id\"]) > 0" "$RUN_JSON"'

check "anthropic-compat scan(): result status is SUCCESS with assistant response" \
  'python3 -c "
import json, sys
r = json.loads(sys.argv[1])
assert r[\"result\"] is not None
assert r[\"result\"][\"status\"] == \"SUCCESS\"
assert \"Done: wrote hello.txt.\" in r[\"result\"][\"response\"]
assert r[\"result\"][\"error\"] == \"\"
" "$RUN_JSON"'

check "anthropic-compat scan(): cost_usd is computed when prices config is provided" \
  'python3 -c "
import json, sys
r = json.loads(sys.argv[1])
assert r[\"result\"][\"cost_usd\"] is not None
assert r[\"result\"][\"cost_usd\"] > 0
" "$RUN_JSON"'

# 7. Rework continuing conversation with conversation parameter
CONV_REWORK="ses_anthropic_resumed_77"
REWORK_JSON=$(python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, '$ROOT/bin/lib')
import runtimes

prompt = 'Refactor edge cases in auth'
argv, stdin_text = runtimes.command('anthropic-compat', prompt, 'claude-3-5-sonnet', conversation='$CONV_REWORK')
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
lines = stdout.splitlines()
parsed = runtimes.scan('anthropic-compat', lines)
print(json.dumps(parsed))
")

check "anthropic-compat rework: command() preserves session and scan() extracts conversation_id" \
  'python3 -c "
import json, sys
r = json.loads(sys.argv[1])
assert r[\"conversation_id\"] == \"$CONV_REWORK\", \"expected $CONV_REWORK, got %s\" % r[\"conversation_id\"]
" "$REWORK_JSON"'

# 8. Drive error path against fake server via stub
ERR_RUN_JSON=$(python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, '$ROOT/bin/lib')
import runtimes

prompt = 'TRIGGER_ERROR'
argv, stdin_text = runtimes.command('anthropic-compat', prompt, 'claude-3-5-sonnet')
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
lines = stdout.splitlines()
parsed = runtimes.scan('anthropic-compat', lines)
print(json.dumps(parsed))
")

check "anthropic-compat live stub: error response parses as status ERROR without crashing" \
  'python3 -c "
import json, sys
r = json.loads(sys.argv[1])
assert r[\"result\"] is not None
assert r[\"result\"][\"status\"] == \"ERROR\"
assert \"rate limit\" in r[\"result\"][\"error\"].lower() or \"error\" in r[\"result\"][\"error\"].lower()
" "$ERR_RUN_JSON"'

# 9. Non-streaming server response handling
NON_STREAM_JSON=$(STREAM=0 python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, '$ROOT/bin/lib')
import runtimes

prompt = 'Generate non-streaming reply'
argv, stdin_text = runtimes.command('anthropic-compat', prompt, 'claude-3-5-sonnet')
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
lines = stdout.splitlines()
parsed = runtimes.scan('anthropic-compat', lines)
print(json.dumps(parsed))
")

check "anthropic-compat live stub: non-streaming response sums tokens (1050) and status SUCCESS" \
  'python3 -c "
import json, sys
r = json.loads(sys.argv[1])
assert r[\"tokens\"] == 1050, \"expected 1050, got %s\" % r[\"tokens\"]
assert r[\"result\"] is not None
assert r[\"result\"][\"status\"] == \"SUCCESS\"
assert \"Done: wrote hello.txt.\" in r[\"result\"][\"response\"]
" "$NON_STREAM_JSON"'

# 10. Fake server logged requests
check "fake server saw and logged requests" \
  'grep -q "session=" "$T/server.log"'

# 11. scan() edge cases and robust error handling
check "anthropic-compat scan(): incomplete stream leaves result as None" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
mid_stream = [
    \"{\\\"type\\\": \\\"message_start\\\", \\\"message\\\": {\\\"id\\\": \\\"mid_01\\\", \\\"usage\\\": {\\\"input_tokens\\\": 300}}}\",
    \"{\\\"type\\\": \\\"content_block_start\\\", \\\"index\\\": 0, \\\"content_block\\\": {\\\"type\\\": \\\"text\\\", \\\"text\\\": \\\"partial\\\"}}\"
]
res = runtimes.scan(\"anthropic-compat\", mid_stream)
assert res[\"tokens\"] == 300
assert res[\"conversation_id\"] == \"mid_01\"
assert res[\"result\"] is None
"'

check "anthropic-compat scan(): malformed and corrupt lines skipped safely" \
  'python3 -c "
import json, sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
noisy_lines = [
    \"malformed header line\",
    \"[DONE]\",
    \"\",
    \"corrupt unclosed json: {\",
    \"data: {\\\"type\\\": \\\"message_start\\\", \\\"message\\\": {\\\"id\\\": \\\"noisy_01\\\", \\\"usage\\\": {\\\"input_tokens\\\": 100}}}\",
    \"data: {\\\"type\\\": \\\"message_delta\\\", \\\"delta\\\": {\\\"stop_reason\\\": \\\"end_turn\\\"}, \\\"usage\\\": {\\\"output_tokens\\\": 50}}\",
    \"data: {\\\"type\\\": \\\"message_stop\\\"}\"
]
res = runtimes.scan(\"anthropic-compat\", noisy_lines)
assert res[\"tokens\"] == 150
assert res[\"conversation_id\"] == \"noisy_01\"
assert res[\"result\"] is not None
assert res[\"result\"][\"status\"] == \"SUCCESS\"
"'

check "anthropic-compat scan(): empty stream returns default structure" \
  'python3 -c "
import sys
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes
res = runtimes.scan(\"anthropic-compat\", [])
assert res[\"tokens\"] == 0
assert res[\"tools\"] == 0
assert res[\"conversation_id\"] is None
assert res[\"result\"] is None
"'

# 12. scan_file() parses events from file and handles missing file
check "anthropic-compat scan_file(): parses events from file and handles missing file" \
  'python3 -c "
import json, sys, tempfile
sys.path.insert(0, \"$ROOT/bin/lib\")
import runtimes

with tempfile.NamedTemporaryFile(\"w+\", encoding=\"utf-8\") as tf:
    tf.write(json.dumps({
        \"id\": \"msg_file_99\",
        \"type\": \"message\",
        \"role\": \"assistant\",
        \"content\": [{\"type\": \"text\", \"text\": \"File scanned successfully\"}],
        \"stop_reason\": \"end_turn\",
        \"usage\": {\"input_tokens\": 600, \"output_tokens\": 150}
    }) + \"\\n\")
    tf.flush()
    r = runtimes.scan_file(\"anthropic-compat\", tf.name)
    assert r[\"tokens\"] == 750
    assert r[\"conversation_id\"] == \"msg_file_99\"
    assert r[\"result\"][\"status\"] == \"SUCCESS\"
    assert \"File scanned successfully\" in r[\"result\"][\"response\"]

missing = runtimes.scan_file(\"anthropic-compat\", \"/nonexistent/missing_file.log\")
assert missing[\"tokens\"] == 0
assert missing[\"result\"] is None
"'

# 13. Run tests/test_runtime_anthropic_compat.py directly and propagate the real exit code
echo "Running tests/test_runtime_anthropic_compat.py against environment..."
python3 "$ROOT/tests/test_runtime_anthropic_compat.py" || exit 1
echo "ok   python test suite (tests/test_runtime_anthropic_compat.py) passed"

echo "anthropic-compat runtime tests: $([ $fail = 0 ] && echo ALL OK || echo FAILED)"
exit $fail
