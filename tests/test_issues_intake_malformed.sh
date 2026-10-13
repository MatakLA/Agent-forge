#!/usr/bin/env bash
# Regression tests: bin/issues-intake rejects malformed webhook payloads and polled issues cleanly.
#
# Public interface:
#   bash tests/test_issues_intake_malformed.sh
#
# Invariants:
#   - Strictly offline: feeds malformed payloads directly and tests poll path against a local fake server.
#   - Tests 5 distinct malformed payload cases crafted in temporary files:
#     (1) not valid JSON at all
#     (2) valid JSON missing required fields (no 'title' or no 'labels')
#     (3) required field with wrong type (labels as string, id as non-numeric string)
#     (4) title/body containing control characters or embedded NUL byte
#     (5) oversized payload (multi-MB title) to test crash/unbounded memory bounds
#   - Tests webhook wrapper envelopes (GitHub action/issue and GitLab object_kind/object_attributes).
#   - Tests success path for well-formed payload file creating exactly one goal and idempotent re-run.
#   - Tests poll-path rejection when forge API returns malformed issue data.
#   - Asserts non-zero exit, clear stderr error, no raw Python traceback, and no bogus goal created.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INTAKE="$ROOT/bin/issues-intake"

T="$(mktemp -d)"
SERVER_PID=""

cleanup() {
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$T"
}
trap cleanup EXIT

export SANDBOX_TEST=1
export SANDBOX_RUNS_DIR="$T/runs"
mkdir -p "$SANDBOX_RUNS_DIR/foreman/goals"

run_case() {
  local name="$1"
  local payload_file="$2"
  local expected_err_sub="$3"

  local stdout_file="$T/${name}.stdout"
  local stderr_file="$T/${name}.stderr"

  local rc=0
  "$INTAKE" "$payload_file" > "$stdout_file" 2> "$stderr_file" || rc=$?

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
  goal_count=$(find "$SANDBOX_RUNS_DIR/foreman/goals" -type f -name "*.json" 2>/dev/null | wc -l)
  if [ "$goal_count" -ne 0 ]; then
    echo "FAIL: $name created goal file in storage" >&2
    exit 1
  fi

  if [ -f "$SANDBOX_RUNS_DIR/foreman/issues-intake.json" ]; then
    echo "FAIL: $name updated issues-intake.json seen list" >&2
    exit 1
  fi

  echo "ok   $name"
}

# Case 1: not valid JSON at all
echo "this is not { valid [ json at all" > "$T/case1_invalid_json.json"
run_case "case1_not_valid_json" "$T/case1_invalid_json.json" "invalid JSON"

# Case 2: valid JSON missing a required field
# 2a: missing 'title'
echo '{"id": 201, "body": "description without title", "labels": ["agent-forge"]}' > "$T/case2a_no_title.json"
run_case "case2a_missing_title" "$T/case2a_no_title.json" "missing required field 'title'"

# 2b: missing 'labels'
echo '{"id": 202, "title": "Issue without labels", "body": "description"}' > "$T/case2b_no_labels.json"
run_case "case2b_missing_labels" "$T/case2b_no_labels.json" "missing required field 'labels'"

# Case 3: required field with wrong type
# 3a: labels as string instead of list
echo '{"id": 301, "title": "Wrong label type", "labels": "agent-forge"}' > "$T/case3a_labels_string.json"
run_case "case3a_labels_string" "$T/case3a_labels_string.json" "invalid type for 'labels'"

# 3b: id as string containing non-numeric garbage
echo '{"id": "garbage-id-123xyz", "title": "Garbage ID", "labels": ["agent-forge"]}' > "$T/case3b_id_garbage.json"
run_case "case3b_id_non_numeric" "$T/case3b_id_garbage.json" "invalid type for 'id'"

# Case 4: title/body containing control characters or embedded NUL byte
# 4a: embedded NUL byte in JSON string (\u0000)
printf '{"id": 401, "title": "evil\\u0000title", "labels": ["agent-forge"]}' > "$T/case4a_nul_byte.json"
run_case "case4a_embedded_nul" "$T/case4a_nul_byte.json" "control characters or NUL byte"

# 4b: control characters in JSON string (\u001b, \u0007)
printf '{"id": 402, "title": "escape\\u001b[31mseq\\u0007bell", "labels": ["agent-forge"]}' > "$T/case4b_ctrl_chars.json"
run_case "case4b_control_chars" "$T/case4b_ctrl_chars.json" "control characters or NUL byte"

# 4c: raw unescaped NUL byte in payload
printf '{"id": 403, "title": "raw\x00nul", "labels": ["agent-forge"]}' > "$T/case4c_raw_nul.json"
run_case "case4c_raw_nul" "$T/case4c_raw_nul.json" "control character"

# Case 5: oversized payload (multi-MB title)
python3 -c "import json, sys; json.dump({'id': 501, 'title': 'A'*2000000, 'labels': ['agent-forge']}, open(sys.argv[1], 'w'))" "$T/case5_oversized.json"
run_case "case5_oversized_payload" "$T/case5_oversized.json" "exceeds"

# Webhook payload structure verification (GitHub and GitLab formats)
echo '{"action": "opened", "issue": {"id": 601, "body": "missing title in webhook", "labels": ["agent-forge"]}}' > "$T/case6_gh_webhook_no_title.json"
run_case "case6_github_webhook_missing_title" "$T/case6_gh_webhook_no_title.json" "missing required field 'title'"

printf '{"object_kind": "issue", "object_attributes": {"id": 602, "title": "NUL in gitlab\\u0000", "description": "desc"}, "labels": [{"title": "agent-forge"}]}' > "$T/case7_gl_webhook_nul.json"
run_case "case7_gitlab_webhook_nul" "$T/case7_gl_webhook_nul.json" "control characters or NUL byte"

# Case 8: Success-path test for well-formed payload file
echo '{"id": 801, "title": "Valid payload issue", "body": "Valid description", "labels": ["agent-forge"]}' > "$T/case8_valid.json"
rc=0
"$INTAKE" "$T/case8_valid.json" > "$T/case8.stdout" 2> "$T/case8.stderr" || rc=$?
if [ "$rc" -ne 0 ]; then
  echo "FAIL: case8_valid_payload expected exit 0, got $rc" >&2
  cat "$T/case8.stderr" >&2
  exit 1
fi
if grep -q "Traceback (most recent call last)" "$T/case8.stderr"; then
  echo "FAIL: case8_valid_payload leaked traceback" >&2
  exit 1
fi
goal_count=$(find "$SANDBOX_RUNS_DIR/foreman/goals" -type f -name "*.json" 2>/dev/null | wc -l)
if [ "$goal_count" -ne 1 ]; then
  echo "FAIL: case8_valid_payload expected exactly 1 goal file, got $goal_count" >&2
  exit 1
fi
if [ ! -f "$SANDBOX_RUNS_DIR/foreman/issues-intake.json" ]; then
  echo "FAIL: case8_valid_payload did not create seen file" >&2
  exit 1
fi

# Case 8b: Idempotent re-run on valid payload does not duplicate goal
rc_dupe=0
"$INTAKE" "$T/case8_valid.json" > "$T/case8b.stdout" 2> "$T/case8b.stderr" || rc_dupe=$?
if [ "$rc_dupe" -ne 0 ]; then
  echo "FAIL: case8b_duplicate_payload expected exit 0, got $rc_dupe" >&2
  exit 1
fi
goal_count_dupe=$(find "$SANDBOX_RUNS_DIR/foreman/goals" -type f -name "*.json" 2>/dev/null | wc -l)
if [ "$goal_count_dupe" -ne 1 ]; then
  echo "FAIL: case8b_duplicate_payload duplicated goal, found $goal_count_dupe" >&2
  exit 1
fi
echo "ok   case8_payload_success_path"

# Case 9: Poll-path malformed issue rejection via local HTTP server
SERVER_PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
python3 -c '
import http.server, sys
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"[{\"id\": 901, \"title\": \"Poll evil\\u0000title\", \"labels\": [\"agent-forge\"]}]")
    def log_message(self, format, *args):
        pass
server = http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler)
server.serve_forever()
' "$SERVER_PORT" > "$T/server.log" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 50); do
  python3 -c "import socket; s=socket.socket(); s.connect(('127.0.0.1', $SERVER_PORT)); s.close()" 2>/dev/null && break
  sleep 0.05
done

rc_poll=0
AF_UPSTREAM_API="http://127.0.0.1:$SERVER_PORT" \
FORGE_KIND="github" \
FORGE_REPO="acme/shop" \
FORGE_UPSTREAM_TOKEN="dummy-token" \
"$INTAKE" > "$T/case9_poll.stdout" 2> "$T/case9_poll.stderr" || rc_poll=$?

kill "$SERVER_PID" 2>/dev/null || true
wait "$SERVER_PID" 2>/dev/null || true
SERVER_PID=""

if [ "$rc_poll" -eq 0 ]; then
  echo "FAIL: case9_poll_path_malformed expected non-zero exit, got 0" >&2
  exit 1
fi
if grep -q "Traceback (most recent call last)" "$T/case9_poll.stderr"; then
  echo "FAIL: case9_poll_path_malformed leaked raw traceback into stderr:" >&2
  cat "$T/case9_poll.stderr" >&2
  exit 1
fi
if ! grep -qi "control characters or NUL byte" "$T/case9_poll.stderr"; then
  echo "FAIL: case9_poll_path_malformed stderr missing expected error:" >&2
  cat "$T/case9_poll.stderr" >&2
  exit 1
fi
goal_count_poll=$(find "$SANDBOX_RUNS_DIR/foreman/goals" -type f -name "*.json" 2>/dev/null | wc -l)
if [ "$goal_count_poll" -ne 1 ]; then
  echo "FAIL: case9_poll_path_malformed created bogus goal file in storage" >&2
  exit 1
fi
echo "ok   case9_poll_path_malformed_rejected"

echo "test_issues_intake_malformed.sh: ALL OK"
