#!/usr/bin/env bash
# tests/test_pick_ai.sh — Offline shell test for scripts/pick-ai.sh.
#
# Public interface:
#   bash tests/test_pick_ai.sh
#
# Invariants:
#   - Strictly offline, no container daemon calls, and no external network calls.
#   - Runs scripts/pick-ai.sh on an unconfigured checkout and asserts exit code 0.
#   - Asserts combined output contains both doctor-style readiness lines and a 'Recommended' wizard line.
#   - Asserts scripts/pick-ai.sh contains no literal docker invocation and no outbound URL.
#   - Validates --help and failure handling when python3 is unavailable.
#   - Trap/cleanup removes temporary scratch files on exit.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/pick-ai.sh"
TMP_OUT="$(mktemp)"
TMP_DIR="$(mktemp -d)"

cleanup() {
  local rc=$?
  rm -f "$TMP_OUT"
  rm -rf "$TMP_DIR"
  exit "$rc"
}
trap cleanup EXIT INT TERM

# 1. Script existence and permissions
[ -f "$SCRIPT" ] || exit 1
[ -x "$SCRIPT" ] || exit 1

# 2. Test --help flag
HELP_OUT=$(bash "$SCRIPT" --help) || exit 1
echo "$HELP_OUT" | grep -q "Usage:" || exit 1
echo "$HELP_OUT" | grep -q "pick-ai.sh" || exit 1

HELP_SHORT=$(bash "$SCRIPT" -h) || exit 1
echo "$HELP_SHORT" | grep -q "Usage:" || exit 1

# 3. Static assertions: no literal docker invocation and no outbound URL in scripts/pick-ai.sh
if grep -iE '\bdocker\b' "$SCRIPT"; then
  echo "FAIL: scripts/pick-ai.sh contains literal docker invocation" >&2
  exit 1
fi

if grep -E 'https?://' "$SCRIPT"; then
  echo "FAIL: scripts/pick-ai.sh contains outbound URL" >&2
  exit 1
fi

# 4. Run scripts/pick-ai.sh on unconfigured checkout
bash "$SCRIPT" > "$TMP_OUT" 2>&1 || exit 1

# Assert doctor-style readiness line is present
grep -q "== Readiness" "$TMP_OUT" || exit 1
grep -qE "\[(OK|WARN)\] [a-zA-Z0-9_-]+: " "$TMP_OUT" || exit 1
grep -F -q "✔ [OK] agy: Runtime 'agy' is ready." "$TMP_OUT" || exit 1

# Assert 'Recommended' wizard line is present
grep -q "=== AI Recommendation Report ===" "$TMP_OUT" || exit 1
grep -qE "^Recommended:" "$TMP_OUT" || exit 1
grep -q "Next steps:" "$TMP_OUT" || exit 1

# 5. Test failure mode when python3 is missing
ln -s "$(command -v bash)" "$TMP_DIR/bash"
ERR_PY=$(PATH="$TMP_DIR" "$TMP_DIR/bash" "$SCRIPT" 2>&1 && exit 1 || true)
echo "$ERR_PY" | grep -qi "python3" || exit 1
if echo "$ERR_PY" | grep -qi "Traceback"; then
  echo "FAIL: raw traceback printed when python3 is missing" >&2
  exit 1
fi

echo "test_pick_ai: ALL OK"
