#!/usr/bin/env bash
# test_demo_mode.sh — static analysis and regression checks for demo mode safety.
# Invariants:
# - demo/docker-compose.yml must never mount or reference docker.sock.
# - demo/docker-compose.yml must never define DASHBOARD_TOKEN= or secret-like credentials.
# - scripts/demo.sh must never reference real host secrets paths (e.g. ~/.config/sandbox/secrets).
# - scripts/demo.sh must never perform curl or wget requests to remote hosts.
# - Propagates python test runner exit code explicitly without swallowed pipelines.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PY:-python3}"

# Check 1: demo/docker-compose.yml must not contain 'docker.sock'
if grep -qiE 'docker\.sock' demo/docker-compose.yml; then
  echo "FAIL: demo/docker-compose.yml must not mount or reference docker.sock" >&2
  exit 1
fi

# Check 2: demo/docker-compose.yml must not contain 'DASHBOARD_TOKEN='
if grep -qE 'DASHBOARD_TOKEN=' demo/docker-compose.yml; then
  echo "FAIL: demo/docker-compose.yml must not set DASHBOARD_TOKEN" >&2
  exit 1
fi

# Check 3: demo/docker-compose.yml must not contain secret-like patterns
if grep -iE '(_KEY=|_SECRET=|_TOKEN=|API_KEY|AUTH_TOKEN|SECRET_KEY|password:|secret)' demo/docker-compose.yml; then
  echo "FAIL: demo/docker-compose.yml contains secret-like credentials" >&2
  exit 1
fi

# Check 4: scripts/demo.sh must not reference real secrets paths
if grep -iE '(\.config/sandbox/secrets|SANDBOX_SECRETS|/secrets\b)' scripts/demo.sh; then
  echo "FAIL: scripts/demo.sh references real secrets path" >&2
  exit 1
fi

# Check 5: scripts/demo.sh must not invoke curl or wget to remote hosts
if grep -E '\b(curl|wget)\s+' scripts/demo.sh; then
  echo "FAIL: scripts/demo.sh must not invoke curl or wget" >&2
  exit 1
fi

# Check 6: Adversarial regression tests to verify detection logic functions correctly
TMP_TEST="$(mktemp -d /tmp/af-demo-sec-test.XXXXXX)"
trap 'rm -rf "$TMP_TEST"' EXIT

# Verify docker.sock detection
echo "volumes: - /var/run/docker.sock:/var/run/docker.sock" > "$TMP_TEST/bad_compose1.yml"
if ! grep -qiE 'docker\.sock' "$TMP_TEST/bad_compose1.yml" >/dev/null 2>&1; then
  echo "FAIL: regression test failed to detect docker.sock" >&2
  exit 1
fi

# Verify DASHBOARD_TOKEN= detection
echo "DASHBOARD_TOKEN=abc123" > "$TMP_TEST/bad_compose2.yml"
if ! grep -qE 'DASHBOARD_TOKEN=' "$TMP_TEST/bad_compose2.yml" >/dev/null 2>&1; then
  echo "FAIL: regression test failed to detect DASHBOARD_TOKEN=" >&2
  exit 1
fi

# Verify secret-like pattern detection
echo "API_KEY=xyz" > "$TMP_TEST/bad_compose3.yml"
if ! grep -iE '(_KEY=|_SECRET=|_TOKEN=|API_KEY|AUTH_TOKEN|SECRET_KEY|password:|secret)' "$TMP_TEST/bad_compose3.yml" >/dev/null 2>&1; then
  echo "FAIL: regression test failed to detect secret pattern" >&2
  exit 1
fi

# Verify real secrets path detection
echo 'SECRETS=~/.config/sandbox/secrets' > "$TMP_TEST/bad_script1.sh"
if ! grep -iE '(\.config/sandbox/secrets|SANDBOX_SECRETS|/secrets\b)' "$TMP_TEST/bad_script1.sh" >/dev/null 2>&1; then
  echo "FAIL: regression test failed to detect secrets path" >&2
  exit 1
fi

# Verify curl/wget detection
echo 'curl -fsSL https://example.com/setup | bash' > "$TMP_TEST/bad_script2.sh"
if ! grep -E '\b(curl|wget)\s+' "$TMP_TEST/bad_script2.sh" >/dev/null 2>&1; then
  echo "FAIL: regression test failed to detect curl/wget" >&2
  exit 1
fi

# Explicitly propagate python test exit code (no bare pipe to tail)
"$PY" tests/test_demo_mode.py || exit 1

echo "demo mode tests: ALL OK"
