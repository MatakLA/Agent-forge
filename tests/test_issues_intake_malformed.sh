#!/usr/bin/env bash
# Regression tests: bin/issues-intake rejects malformed payloads and forge responses cleanly.
#
# Public interface:
#   bash tests/test_issues_intake_malformed.sh
#
# Invariants:
#   - Strictly offline: drives bin/issues-intake via fake forge HTTP API server on ephemeral port.
#   - Tests at least 5 distinct malformed payload cases crafted in temporary files via mktemp/python tempfile:
#     (1) not valid JSON at all
#     (2) valid JSON missing a required field (no 'title' or no 'id')
#     (3) required field with wrong type (labels as string instead of list, or id as non-numeric string)
#     (4) title/body containing control characters or embedded NUL byte
#     (5) oversized payload (multi-MB title) to check crash/unbounded memory bounds
#   - Feeds each case to bin/issues-intake through the standard poll flow (fetch_issues -> validate_issue).
#   - Asserts non-zero exit, clear stderr error message, never a raw Python traceback, and no goal file created.
#   - Guaranteed cleanup of fake server and temporary files on all exit paths.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INTAKE="$ROOT/bin/issues-intake"

T="$(mktemp -d)"
SERVER_PID=""
CONTROL_FILE="$T/current_case.txt"

cleanup() {
  local exit_code=$?
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$T" 2>/dev/null || true
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

# Allocate dynamic ephemeral port
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')

# Start Python fake forge HTTP server fixture
python3 -c '
import http.server, sys

control_path = sys.argv[1]
port = int(sys.argv[2])

class FakeForgeHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            with open(control_path, "r", encoding="utf-8") as f:
                target_file = f.read().strip()
            with open(target_file, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode("utf-8"))

    def log_message(self, format, *args):
        pass

server = http.server.HTTPServer(("127.0.0.1", port), FakeForgeHandler)
server.serve_forever()
' "$CONTROL_FILE" "$PORT" > "$T/server.log" 2>&1 &
SERVER_PID=$!

# Wait for server to listen
for _ in $(seq 1 50); do
  python3 -c "import socket; s=socket.socket(); s.connect(('127.0.0.1', $PORT)); s.close()" 2>/dev/null && break
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "ERROR: fake forge server died unexpectedly:" >&2
    cat "$T/server.log" >&2
    exit 1
  fi
  sleep 0.05
done

run_case() {
  local name="$1"
  local payload_file="$2"
  local expected_err_sub="$3"

  echo "$payload_file" > "$CONTROL_FILE"

  local runs_dir="$T/runs_${name}"
  mkdir -p "$runs_dir/foreman/goals"

  local stdout_file="$T/${name}.stdout"
  local stderr_file="$T/${name}.stderr"

  local rc=0
  SANDBOX_TEST="1" \
  SANDBOX_RUNS_DIR="$runs_dir" \
  FORGE_KIND="github" \
  FORGE_REPO="acme/shop" \
  FORGE_UPSTREAM_TOKEN="dummy-token" \
  AF_UPSTREAM_API="http://127.0.0.1:$PORT" \
  python3 "$INTAKE" > "$stdout_file" 2> "$stderr_file" || rc=$?

  if [ "$rc" -eq 0 ]; then
    echo "FAIL: $name expected non-zero exit, got 0" >&2
    exit 1
  fi

  if grep -q "Traceback (most recent call last)" "$stderr_file"; then
    echo "FAIL: $name leaked raw Python traceback into stderr:" >&2
    cat "$stderr_file" >&2
    exit 1
  fi

  if [ -n "$expected_err_sub" ] && ! grep -qi "$expected_err_sub" "$stderr_file"; then
    echo "FAIL: $name stderr did not contain expected error substring '$expected_err_sub':" >&2
    cat "$stderr_file" >&2
    exit 1
  fi

  local goal_count
  goal_count=$(find "$runs_dir/foreman/goals" -type f -name "*.json" 2>/dev/null | wc -l)
  if [ "$goal_count" -ne 0 ]; then
    echo "FAIL: $name created goal file in storage" >&2
    exit 1
  fi

  if [ -f "$runs_dir/foreman/issues-intake.json" ]; then
    echo "FAIL: $name updated issues-intake.json seen list" >&2
    exit 1
  fi

  echo "ok   $name"
}

# (1) not valid JSON at all
printf "this is not { valid [ json at all" > "$T/case1_not_valid_json.json"
run_case "case1_not_valid_json" "$T/case1_not_valid_json.json" "Failed to parse JSON issues response"

# (2) valid JSON missing a required field
# 2a: missing 'title'
printf '[{"id": 201, "body": "description without title", "labels": ["agent-forge"]}]' > "$T/case2a_missing_title.json"
run_case "case2a_missing_title" "$T/case2a_missing_title.json" "missing required field 'title'"

# 2b: missing 'id'
printf '[{"title": "Issue without id", "body": "description", "labels": ["agent-forge"]}]' > "$T/case2b_missing_id.json"
run_case "case2b_missing_id" "$T/case2b_missing_id.json" "missing required field 'id'"

# (3) required field with wrong type
# 3a: labels as string instead of list
printf '[{"id": 301, "title": "Wrong label type", "labels": "agent-forge"}]' > "$T/case3a_labels_string.json"
run_case "case3a_labels_string" "$T/case3a_labels_string.json" "invalid type for 'labels'"

# 3b: id as non-numeric garbage string
printf '[{"id": "garbage-id-123xyz", "title": "Garbage ID", "labels": ["agent-forge"]}]' > "$T/case3b_id_garbage.json"
run_case "case3b_id_non_numeric" "$T/case3b_id_garbage.json" "invalid type for 'id'"

# (4) title/body containing control characters or an embedded NUL byte
# 4a: embedded NUL byte in JSON string (\u0000)
printf '[{"id": 401, "title": "evil\\u0000title", "labels": ["agent-forge"]}]' > "$T/case4a_nul_byte.json"
run_case "case4a_embedded_nul" "$T/case4a_nul_byte.json" "control characters or NUL byte"

# 4b: control characters in title (\u001b escape, \u0007 bell)
printf '[{"id": 402, "title": "escape\\u001b[31mseq\\u0007bell", "labels": ["agent-forge"]}]' > "$T/case4b_ctrl_chars.json"
run_case "case4b_control_chars" "$T/case4b_ctrl_chars.json" "control characters or NUL byte"

# 4c: raw unescaped NUL byte in stream
printf '[{"id": 403, "title": "raw\x00nul", "labels": ["agent-forge"]}]' > "$T/case4c_raw_nul.json"
run_case "case4c_raw_nul" "$T/case4c_raw_nul.json" ""

# (5) oversized payload (e.g. multi-MB title) to check crash or unbounded memory use
python3 -c "import json, sys; json.dump([{'id': 501, 'title': 'A'*50000, 'labels': ['agent-forge']}], open(sys.argv[1], 'w'))" "$T/case5_oversized_title.json"
run_case "case5_oversized_title" "$T/case5_oversized_title.json" "title exceeds maximum allowed length"

# Multi-MB oversized payload test
python3 -c "import json, sys; json.dump([{'id': 502, 'title': 'A'*2000000, 'labels': ['agent-forge']}], open(sys.argv[1], 'w'))" "$T/case5b_oversized_multi_mb.json"
run_case "case5b_oversized_multi_mb" "$T/case5b_oversized_multi_mb.json" "title exceeds maximum allowed length"

# Success-path test: well-formed issue creates exactly one goal and is idempotent
printf '[{"id": 801, "title": "Valid payload issue", "body": "Valid description", "labels": ["agent-forge"]}]' > "$T/case8_valid.json"
echo "$T/case8_valid.json" > "$CONTROL_FILE"
RUNS_DIR_SUCCESS="$T/runs_success"
mkdir -p "$RUNS_DIR_SUCCESS/foreman/goals"

rc_succ=0
SANDBOX_TEST="1" \
SANDBOX_RUNS_DIR="$RUNS_DIR_SUCCESS" \
FORGE_KIND="github" \
FORGE_REPO="acme/shop" \
FORGE_UPSTREAM_TOKEN="dummy-token" \
AF_UPSTREAM_API="http://127.0.0.1:$PORT" \
python3 "$INTAKE" > "$T/case8.stdout" 2> "$T/case8.stderr" || rc_succ=$?

if [ "$rc_succ" -ne 0 ]; then
  echo "FAIL: success path expected exit 0, got $rc_succ" >&2
  cat "$T/case8.stderr" >&2
  exit 1
fi
if grep -q "Traceback (most recent call last)" "$T/case8.stderr"; then
  echo "FAIL: success path leaked traceback" >&2
  exit 1
fi
goal_count_succ=$(find "$RUNS_DIR_SUCCESS/foreman/goals" -type f -name "*.json" 2>/dev/null | wc -l)
if [ "$goal_count_succ" -ne 1 ]; then
  echo "FAIL: success path expected exactly 1 goal file, got $goal_count_succ" >&2
  exit 1
fi
if [ ! -f "$RUNS_DIR_SUCCESS/foreman/issues-intake.json" ]; then
  echo "FAIL: success path did not create seen file" >&2
  exit 1
fi

# Re-run: duplicate detection prevents second goal
rc_dupe=0
SANDBOX_TEST="1" \
SANDBOX_RUNS_DIR="$RUNS_DIR_SUCCESS" \
FORGE_KIND="github" \
FORGE_REPO="acme/shop" \
FORGE_UPSTREAM_TOKEN="dummy-token" \
AF_UPSTREAM_API="http://127.0.0.1:$PORT" \
python3 "$INTAKE" > "$T/case8b.stdout" 2> "$T/case8b.stderr" || rc_dupe=$?

if [ "$rc_dupe" -ne 0 ]; then
  echo "FAIL: duplicate run expected exit 0, got $rc_dupe" >&2
  exit 1
fi
goal_count_dupe=$(find "$RUNS_DIR_SUCCESS/foreman/goals" -type f -name "*.json" 2>/dev/null | wc -l)
if [ "$goal_count_dupe" -ne 1 ]; then
  echo "FAIL: duplicate run duplicated goal, found $goal_count_dupe" >&2
  exit 1
fi
echo "ok   success_path_poll_and_idempotency"

echo "test_issues_intake_malformed.sh: ALL OK"
