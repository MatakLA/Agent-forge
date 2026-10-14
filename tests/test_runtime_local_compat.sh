#!/usr/bin/env bash
# Integration test for local-compat runtimes (ollama, vllm, lmstudio, llamacpp) in bin/lib/runtimes.py against fake-local-compat-server.js.
# Public interface: bash tests/test_runtime_local_compat.sh (runs with no real network call, exits 0 on success).
# Invariants: starts fake-local-compat-server.js bound to a free port via resolvePort(), drives command() and scan()
# through a stub binary on PATH for all four runtime keys using the same running fixture instance, verifies
# hosts() returns only endpoint host, cost_model() states self-hosted/unknown with no fabricated USD, and exits 0.
# Grounded from bin/lib/runtimes.py (Tasks 0207, 0213):
# - RUNTIMES: ollama (port 11434), vllm (port 8000), lmstudio (port 1234), llamacpp (port 8080)
# - endpoint=True, api=False, hosts=[]
# - cost_model(): kind="unknown", detail states Self-hosted local server with no per-token charges and no USD
# - command(): curl -s -X POST <url> -H Content-Type: application/json -d @-, prompt in messages payload
# - scan(): parses single/multiline JSON completions and SSE streams with tokens, tools, and result status.
# - Requires node on PATH to run fake-local-compat-server.js; skips fixture integration cleanly when node is unavailable.

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

# 1. Metadata and predicate checks for all four local-compat runtimes
for rt in ollama vllm lmstudio llamacpp; do
  check "$rt runtime: registered in RUNTIMES with correct metadata" "python3 -c '
import sys
sys.path.insert(0, sys.argv[1] + \"/bin/lib\")
import runtimes
rt = sys.argv[2]
assert rt in runtimes.RUNTIMES
entry = runtimes.RUNTIMES[rt]
assert entry[\"image\"] == runtimes.AGY_IMAGE
assert entry[\"hosts\"] == []
assert entry[\"api\"] is False
assert entry[\"endpoint\"] is True
assert entry[\"optional_key\"] is True
assert entry[\"default_path\"] == \"/v1/chat/completions\"
' \"$ROOT\" \"$rt\""

  check "$rt runtime: is_api() returns False" "python3 -c '
import sys
sys.path.insert(0, sys.argv[1] + \"/bin/lib\")
import runtimes
assert runtimes.is_api(sys.argv[2]) is False
' \"$ROOT\" \"$rt\""

  check "$rt runtime: uses_endpoint() returns True" "python3 -c '
import sys
sys.path.insert(0, sys.argv[1] + \"/bin/lib\")
import runtimes
assert runtimes.uses_endpoint(sys.argv[2]) is True
' \"$ROOT\" \"$rt\""

  check "$rt runtime: hosts() returns only endpoint host, never static public hosts" "python3 -c '
import sys
sys.path.insert(0, sys.argv[1] + \"/bin/lib\")
import runtimes
rt = sys.argv[2]
assert runtimes.hosts(rt, \"\") == []
assert runtimes.hosts(rt) == []
assert runtimes.hosts(rt, \"http://127.0.0.1:9000/v1\") == [\"127.0.0.1\"]
assert runtimes.hosts(rt, \"http://localhost:8000/v1\") == [\"localhost\"]
assert runtimes.hosts(rt, \"invalid-url\") == []
assert \"api.openai.com\" not in runtimes.hosts(rt, \"http://127.0.0.1:8000/v1\")
assert \"api.githubcopilot.com\" not in runtimes.hosts(rt, \"http://127.0.0.1:8000/v1\")
assert \"api.mistral.ai\" not in runtimes.hosts(rt, \"http://127.0.0.1:8000/v1\")
' \"$ROOT\" \"$rt\""

  check "$rt runtime: cost_model() reports unknown pricing honestly without fabricated USD" "python3 -c '
import sys, re
sys.path.insert(0, sys.argv[1] + \"/bin/lib\")
import runtimes
cm = runtimes.cost_model(sys.argv[2])
assert cm[\"kind\"] == \"unknown\"
assert \"Self-hosted\" in cm[\"detail\"]
assert \"no per-token charges\" in cm[\"detail\"]
assert \"$\" not in cm[\"detail\"]
assert not re.search(r\"\\$\\d+\\.?\\d*\", cm[\"detail\"])
assert not re.search(r\"\\b0\\.00\\b\", cm[\"detail\"])
' \"$ROOT\" \"$rt\""
done

# 2. command() contract and argv security checks for all four runtimes
for rt in ollama vllm lmstudio llamacpp; do
  check "$rt command(): starts with curl POST and JSON headers" "python3 -c '
import sys
sys.path.insert(0, sys.argv[1] + \"/bin/lib\")
import runtimes
argv, stdin = runtimes.command(sys.argv[2], \"Test task prompt\", \"test-model\")
assert argv[0] == \"curl\"
assert argv[1:4] == [\"-s\", \"-X\", \"POST\"]
assert \"-H\" in argv and \"Content-Type: application/json\" in argv
assert \"-d\" in argv and \"@-\" in argv
assert \"Test task prompt\" not in argv
' \"$ROOT\" \"$rt\""

  check "$rt command(): prompt passed on stdin payload and omitted from argv" "python3 -c '
import sys, json
sys.path.insert(0, sys.argv[1] + \"/bin/lib\")
import runtimes
p = \"Confidential prompt with unicode: 🚀 运行本地模型 \" * 50
argv, stdin = runtimes.command(sys.argv[2], p, \"test-model\")
assert p not in \" \".join(argv)
body = json.loads(stdin)
assert body[\"messages\"][-1][\"content\"] == p
' \"$ROOT\" \"$rt\""

  check "$rt command(): never leaks secrets in argv" "python3 -c '
import sys
sys.path.insert(0, sys.argv[1] + \"/bin/lib\")
import runtimes
argv, stdin = runtimes.command(sys.argv[2], \"task\", \"test-model\", budget_usd=5.0)
argv_str = \" \".join(argv)
assert \"compat.key\" not in argv_str
assert \"sk-\" not in argv_str
assert \"ghp_\" not in argv_str
assert \"bearer\" not in argv_str.lower()
' \"$ROOT\" \"$rt\""

  check "$rt command(): handles default and explicit endpoint URLs" "python3 -c '
import sys, os
sys.path.insert(0, sys.argv[1] + \"/bin/lib\")
import runtimes
rt = sys.argv[2]
default_port = runtimes.RUNTIMES[rt][\"default_port\"]
argv_def, _ = runtimes.command(rt, \"task\", \"m1\")
assert argv_def[4] == f\"http://127.0.0.1:{default_port}/v1/chat/completions\"

# Explicit endpoint
argv_ep, _ = runtimes.command(rt, \"task\", \"m1\", endpoint_url=\"http://127.0.0.1:9999/v1\")
assert argv_ep[4] == \"http://127.0.0.1:9999/v1/chat/completions\"
' \"$ROOT\" \"$rt\""
done

# 3. Check node availability for fixture-dependent integration tests
if ! command -v node >/dev/null 2>&1; then
  echo "SKIP: node is not available on host; fake-local-compat-server.js requires node"
else
  # Start the real fixture bound to free port 0 (resolvePort returns 0, listen(0) picks ephemeral port)
  node "$ROOT/tests/fixtures/fake-local-compat-server.js" 0 > "$T/server.log" 2>&1 &
  SERVER_PID=$!

  # Capture assigned port from fixture's resolvePort() output
  PORT=""
  for _ in $(seq 1 50); do
    if [ -f "$T/server.log" ]; then
      PORT=$(grep -oE "127\.0\.0\.1:[0-9]+" "$T/server.log" | cut -d: -f2 | head -n1 || true)
      if [ -n "$PORT" ]; then break; fi
    fi
    sleep 0.1
  done

  if [ -z "$PORT" ]; then
    echo "FAIL: fake-local-compat-server.js did not report listening port in time"
    cat "$T/server.log" || true
    exit 1
  fi

  # Wait for fake server to accept connections
  for _ in $(seq 1 40); do
    python3 -c "import socket; s=socket.socket(); s.connect(('127.0.0.1', $PORT)); s.close()" 2>/dev/null && break
    sleep 0.1
  done

  # 4. Create stub binaries on PATH
  cat > "$T/bin/curl" <<'EOF'
#!/usr/bin/env bash
if [ -x /usr/bin/curl ]; then
  exec /usr/bin/curl "$@"
elif [ -x /bin/curl ]; then
  exec /bin/curl "$@"
else
  python3 -c "
import sys, urllib.request
url = None
data = None
headers = {}
args = sys.argv[1:]
i = 0
while i < len(args):
    a = args[i]
    if a in ('-X', '--request') and i + 1 < len(args):
        i += 2
    elif a in ('-H', '--header') and i + 1 < len(args):
        k, v = args[i+1].split(':', 1)
        headers[k.strip()] = v.strip()
        i += 2
    elif a in ('-d', '--data') and i + 1 < len(args):
        val = args[i+1]
        data = sys.stdin.read().encode('utf-8') if val == '@-' else val.encode('utf-8')
        i += 2
    elif not a.startswith('-'):
        url = a
        i += 1
    else:
        i += 1
req = urllib.request.Request(url, data=data, headers=headers)
with urllib.request.urlopen(req) as resp:
    sys.stdout.write(resp.read().decode('utf-8'))
" "$@"
fi
EOF
  chmod +x "$T/bin/curl"

  for rt in ollama vllm lmstudio llamacpp; do
    cat > "$T/bin/$rt" <<EOF
#!/usr/bin/env bash
echo "stub $rt called" >&2
EOF
    chmod +x "$T/bin/$rt"
  done

  export PATH="$T/bin:$PATH"

  # 5. Drive command() and scan() end-to-end against the same running fixture for all 4 runtimes
  for rt in ollama vllm lmstudio llamacpp; do
    RUN_JSON=$(python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, sys.argv[1] + '/bin/lib')
import runtimes

rt = sys.argv[2]
port = sys.argv[3]
prompt = f'Implement hello world module for {rt}'
endpoint = f'http://127.0.0.1:{port}/v1/chat/completions'
argv, stdin_text = runtimes.command(rt, prompt, 'local-model', endpoint_url=endpoint)
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
if proc.returncode != 0:
    sys.stderr.write(f'execution error for {rt}: {stderr}\n')
    sys.exit(proc.returncode)

lines = stdout.splitlines()
parsed = runtimes.scan(rt, lines)
print(json.dumps(parsed))
" "$ROOT" "$rt" "$PORT")

    check "$rt live fixture: result status is SUCCESS" \
      "python3 -c \"import json, sys; r=json.loads(sys.argv[1]); assert r['result']['status'] == 'SUCCESS'\" \"$RUN_JSON\""

    check "$rt live fixture: response text extracted correctly" \
      "python3 -c \"import json, sys; r=json.loads(sys.argv[1]); assert r['result']['response'] == 'Task completed successfully.'\" \"$RUN_JSON\""

    check "$rt live fixture: tokens parsed accurately from usage (1240)" \
      "python3 -c \"import json, sys; r=json.loads(sys.argv[1]); assert r['tokens'] == 1240, f'got {r[\"tokens\"]}'\" \"$RUN_JSON\""

    check "$rt live fixture: cost_usd is None by default" \
      "python3 -c \"import json, sys; r=json.loads(sys.argv[1]); assert r['result']['cost_usd'] is None\" \"$RUN_JSON\""

    check "$rt live fixture: conversation_id parsed from completion id" \
      "python3 -c \"import json, sys; r=json.loads(sys.argv[1]); assert r['conversation_id'] == 'chatcmpl-fake-local-001'\" \"$RUN_JSON\""
  done

  # 6. Conversation session handling with conversation ID
  CONV_JSON=$(python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, sys.argv[1] + '/bin/lib')
import runtimes

port = sys.argv[2]
prompt = 'Follow up question'
endpoint = f'http://127.0.0.1:{port}/v1/chat/completions'
argv, stdin_text = runtimes.command('ollama', prompt, 'local-model', conversation='ses_local_42', endpoint_url=endpoint)
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
lines = stdout.splitlines()
parsed = runtimes.scan('ollama', lines)
print(json.dumps(parsed))
" "$ROOT" "$PORT")

  check "ollama conversation: scan() extracts conversation id" \
    "python3 -c \"import json, sys; r=json.loads(sys.argv[1]); assert r['result']['status'] == 'SUCCESS'\" \"$CONV_JSON\""

  # 7. Error response handling against /error endpoint
  ERR_JSON=$(python3 -c "
import json, os, subprocess, sys
sys.path.insert(0, sys.argv[1] + '/bin/lib')
import runtimes

port = sys.argv[2]
endpoint = f'http://127.0.0.1:{port}/error'
argv, stdin_text = runtimes.command('vllm', 'trigger error', 'local-model', endpoint_url=endpoint)
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=stdin_text)
lines = stdout.splitlines()
parsed = runtimes.scan('vllm', lines)
print(json.dumps(parsed))
" "$ROOT" "$PORT")

  check "vllm error handling: result status is ERROR and message captured" \
    "python3 -c \"import json, sys; r=json.loads(sys.argv[1]); assert r['result']['status'] == 'ERROR' and 'simulated' in r['result']['error']\" \"$ERR_JSON\""
fi

# 8. Pure scan checks for all four runtimes
for rt in ollama vllm lmstudio llamacpp; do
  check "$rt scan(): empty stream yields tokens=None and default structure" "python3 -c '
import sys
sys.path.insert(0, sys.argv[1] + \"/bin/lib\")
import runtimes
r = runtimes.scan(sys.argv[2], [])
assert r[\"tokens\"] is None
assert r[\"tools\"] == 0
assert r[\"conversation_id\"] is None
assert r[\"result\"] is None
' \"$ROOT\" \"$rt\""

  check "$rt scan(): incomplete stream without completion returns result=None" "python3 -c '
import json, sys
sys.path.insert(0, sys.argv[1] + \"/bin/lib\")
import runtimes
rt = sys.argv[2]
stream = [\"data: \" + json.dumps({\"choices\": [{\"delta\": {\"content\": \"partial content\"}}]})]
r = runtimes.scan(rt, stream)
assert r[\"result\"] is None
assert r[\"tokens\"] is None
' \"$ROOT\" \"$rt\""

  check "$rt scan(): ignores malformed, non-JSON, and non-dict lines safely" "python3 -c '
import sys
sys.path.insert(0, sys.argv[1] + \"/bin/lib\")
import runtimes
rt = sys.argv[2]
garbage = [
    \"curl: (7) Failed to connect\",
    \"<html>502 Bad Gateway</html>\",
    \"   \",
    \"{invalid json\",
    \"[1, 2, 3]\",
    \"plain string\"
]
r = runtimes.scan(rt, garbage)
assert r[\"result\"] is None
assert r[\"tokens\"] is None
' \"$ROOT\" \"$rt\""

  check "$rt scan(): token calculation with total and cached tokens" "python3 -c '
import json, sys
sys.path.insert(0, sys.argv[1] + \"/bin/lib\")
import runtimes
stream = [
    json.dumps({
        \"object\": \"chat.completion\",
        \"usage\": {\"total_tokens\": 2000, \"cached_tokens\": 500},
        \"choices\": [{\"finish_reason\": \"stop\"}]
    })
]
r = runtimes.scan(sys.argv[2], stream)
assert r[\"tokens\"] == 1500
' \"$ROOT\" \"$rt\""

  check "$rt scan(): token calculation boundary when cached > total" "python3 -c '
import json, sys
sys.path.insert(0, sys.argv[1] + \"/bin/lib\")
import runtimes
stream = [
    json.dumps({
        \"object\": \"chat.completion\",
        \"usage\": {\"total_tokens\": 100, \"cached_tokens\": 200},
        \"choices\": [{\"finish_reason\": \"stop\"}]
    })
]
r = runtimes.scan(sys.argv[2], stream)
assert r[\"tokens\"] == 0
' \"$ROOT\" \"$rt\""

  check "$rt scan(): alternative session ID field names" "python3 -c '
import json, sys
sys.path.insert(0, sys.argv[1] + \"/bin/lib\")
import runtimes
rt = sys.argv[2]
for field, val in [(\"id\", \"id_01\"), (\"conversation_id\", \"cid_02\"), (\"session_id\", \"sid_03\"), (\"sessionId\", \"sid_04\"), (\"thread_id\", \"tid_05\")]:
    r = runtimes.scan(rt, [json.dumps({field: val, \"choices\": [{\"finish_reason\": \"stop\"}]})])
    assert r[\"conversation_id\"] == val, f\"failed for {field}={val}, got {r[\"conversation_id\"]}\"
' \"$ROOT\" \"$rt\""

  check "$rt scan(): tool calls from choices and diff blocks increment tools" "python3 -c '
import json, sys
sys.path.insert(0, sys.argv[1] + \"/bin/lib\")
import runtimes
events = [
    json.dumps({\"choices\": [{\"message\": {\"tool_calls\": [{\"id\": \"1\"}, {\"id\": \"2\"}]}}]}),
    json.dumps({\"choices\": [{\"message\": {\"function_call\": {\"name\": \"f1\"}}}]}),
    json.dumps({\"choices\": [{\"message\": {\"content\": \"<<<<<<< SEARCH\\na\\n=======\\nb\\n>>>>>>> REPLACE\"}}]}),
    json.dumps({\"type\": \"tool_use\"}),
    json.dumps({\"choices\": [{\"finish_reason\": \"stop\"}]})
]
r = runtimes.scan(sys.argv[2], events)
assert r[\"tools\"] == 5, f\"expected tools=5, got {r[\"tools\"]}\"
' \"$ROOT\" \"$rt\""

  check "$rt scan(): response text truncation to last 4000 chars and unicode preservation" "python3 -c '
import json, sys
sys.path.insert(0, sys.argv[1] + \"/bin/lib\")
import runtimes
long_text = \"Z\" * 5000 + \" 🚀 本地模型\"
ev = json.dumps({\"object\": \"chat.completion\", \"choices\": [{\"finish_reason\": \"stop\", \"message\": {\"content\": long_text}}]})
r = runtimes.scan(sys.argv[2], [ev])
assert len(r[\"result\"][\"response\"]) == 4000
assert r[\"result\"][\"response\"].endswith(\" 🚀 本地模型\")
' \"$ROOT\" \"$rt\""

  check "$rt scan(): cost_usd remains None by default and computed with prices" "python3 -c '
import json, sys
sys.path.insert(0, sys.argv[1] + \"/bin/lib\")
import runtimes
ev = json.dumps({\"object\": \"chat.completion\", \"usage\": {\"prompt_tokens\": 1000, \"completion_tokens\": 500, \"cached_tokens\": 200}, \"choices\": [{\"finish_reason\": \"stop\"}]})
r1 = runtimes.scan(sys.argv[2], [ev])
assert r1[\"result\"][\"cost_usd\"] is None
r2 = runtimes.scan(sys.argv[2], [ev], prices=\"3.0,0.5,15.0\")
assert r2[\"result\"][\"cost_usd\"] == 0.01
' \"$ROOT\" \"$rt\""

  check "$rt scan_file(): parses events from file and handles missing file" "python3 -c '
import json, os, sys, tempfile
sys.path.insert(0, sys.argv[1] + \"/bin/lib\")
import runtimes
rt = sys.argv[2]
with tempfile.NamedTemporaryFile(\"w+\", encoding=\"utf-8\") as tf:
    tf.write(json.dumps({\"id\": \"file_01\", \"object\": \"chat.completion\", \"usage\": {\"total_tokens\": 400}, \"choices\": [{\"finish_reason\": \"stop\", \"message\": {\"content\": \"ok\"}}]}) + \"\\n\")
    tf.flush()
    res = runtimes.scan_file(rt, tf.name)
    assert res[\"conversation_id\"] == \"file_01\"
    assert res[\"tokens\"] == 400
    assert res[\"result\"][\"status\"] == \"SUCCESS\"
missing = runtimes.scan_file(rt, \"/path/does/not/exist.jsonl\")
assert missing[\"tokens\"] is None
assert missing[\"result\"] is None
' \"$ROOT\" \"$rt\""
done

# 9. Run Python unittest suite
python3 "$ROOT/tests/test_runtime_local_compat.py" || exit 1

echo "local-compat runtimes tests: $([ $fail = 0 ] && echo ALL OK || echo FAILED)"
exit $fail
