#!/usr/bin/env bash
# Regression tests: bin/issues-intake rejects malformed webhook payloads cleanly.
#
# Public interface:
#   bash tests/test_issues_intake_malformed.sh
#
# Invariants:
#   - Strictly offline: feeds malformed payloads directly without reaching real forges.
#   - Tests 5 distinct malformed payload cases crafted in temporary files:
#     (1) not valid JSON at all
#     (2) valid JSON missing required fields (no 'title' or no 'labels')
#     (3) required field with wrong type (labels as string, id as non-numeric string)
#     (4) title/body containing control characters or embedded NUL byte
#     (5) oversized payload (multi-MB title) to test crash/unbounded memory bounds
#   - Asserts non-zero exit, clear stderr error, no raw Python traceback, and no goal file created.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INTAKE="$ROOT/bin/issues-intake"

T="$(mktemp -d)"
cleanup() {
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

echo "test_issues_intake_malformed.sh: ALL OK"
