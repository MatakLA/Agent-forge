#!/usr/bin/env bash
# Tests for egress and worker sandbox entrypoints: validates input sanitization,
# restrictive file permissions, non-root user enforcement, and safe startup defaults.
# Public interface: execute directly (tests/test_egress_security.sh) without arguments; exits 0 if all tests pass.
# Invariants: no network or Docker required; tests run hermetically using isolated temp directories and mocks.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EGRESS_ENTRY="$ROOT/egress/entrypoint.sh"
WORKER_ENTRY="$ROOT/worker/entrypoint.sh"

fail=0
check() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }

T="$(mktemp -d)"
cleanup() { rm -rf "$T"; }
trap cleanup EXIT

MOCK_BIN="$T/bin"
mkdir -p "$MOCK_BIN" "$T/home/playground/.git"
printf '[user]\nname=mock\n' > "$T/home/playground/.git/config"

# ==============================================================================
# 1. worker/entrypoint.sh: FORGEJO_USER validation
# ==============================================================================
check "worker: rejects FORGEJO_USER command injection" \
  '! HOME="$T/home" FORGEJO_USER="worker-1; touch $T/pwn" FORGE_REPO="sandbox/playground" bash "$WORKER_ENTRY" true >/dev/null 2>&1'

check "worker: rejects FORGEJO_USER newline config injection" \
  '! HOME="$T/home" FORGEJO_USER=$"worker-1\n[core]\nsshCommand=evil" FORGE_REPO="sandbox/playground" bash "$WORKER_ENTRY" true >/dev/null 2>&1'

check "worker: rejects FORGEJO_USER option flag injection" \
  '! HOME="$T/home" FORGEJO_USER="-oProxyCommand=calc" FORGE_REPO="sandbox/playground" bash "$WORKER_ENTRY" true >/dev/null 2>&1'

check "worker: rejects FORGEJO_USER with spaces" \
  '! HOME="$T/home" FORGEJO_USER="worker 1" FORGE_REPO="sandbox/playground" bash "$WORKER_ENTRY" true >/dev/null 2>&1'

check "worker: accepts valid FORGEJO_USER" \
  'HOME="$T/home" FORGEJO_USER="worker-42" FORGE_REPO="sandbox/playground" bash "$WORKER_ENTRY" true >/dev/null 2>&1'

# ==============================================================================
# 2. worker/entrypoint.sh: FORGE_REPO validation
# ==============================================================================
check "worker: rejects FORGE_REPO option flag (--upload-pack)" \
  '! HOME="$T/home" FORGEJO_USER="worker-1" FORGE_REPO="--upload-pack=evil" bash "$WORKER_ENTRY" true >/dev/null 2>&1'

check "worker: rejects FORGE_REPO command injection" \
  '! HOME="$T/home" FORGEJO_USER="worker-1" FORGE_REPO="sandbox/playground; rm -rf /" bash "$WORKER_ENTRY" true >/dev/null 2>&1'

check "worker: rejects FORGE_REPO path traversal with .." \
  '! HOME="$T/home" FORGEJO_USER="worker-1" FORGE_REPO="../../etc/passwd" bash "$WORKER_ENTRY" true >/dev/null 2>&1'

check "worker: rejects FORGE_REPO with trailing traversal component" \
  '! HOME="$T/home" FORGEJO_USER="worker-1" FORGE_REPO="sandbox/.." bash "$WORKER_ENTRY" true >/dev/null 2>&1'

check "worker: rejects FORGE_REPO without repository slash" \
  '! HOME="$T/home" FORGEJO_USER="worker-1" FORGE_REPO="single_word" bash "$WORKER_ENTRY" true >/dev/null 2>&1'

check "worker: rejects FORGE_REPO with excess path segments" \
  '! HOME="$T/home" FORGEJO_USER="worker-1" FORGE_REPO="org/repo/subproject" bash "$WORKER_ENTRY" true >/dev/null 2>&1'

check "worker: accepts valid FORGE_REPO" \
  'HOME="$T/home" FORGEJO_USER="worker-1" FORGE_REPO="owner-org/repo.git_1" bash "$WORKER_ENTRY" true >/dev/null 2>&1'

# ==============================================================================
# 3. worker/entrypoint.sh: Permissions & Credential Isolation
# ==============================================================================
PERM_HOME="$T/perm_home"
mkdir -p "$PERM_HOME/playground/.git"
printf '[user]\n\tname = secret\n\ttoken = sensitive\n' > "$PERM_HOME/playground/.git/config"
chmod 755 "$PERM_HOME/playground" "$PERM_HOME/playground/.git"
chmod 644 "$PERM_HOME/playground/.git/config"

HOME="$PERM_HOME" FORGEJO_USER="worker-1" FORGE_REPO="sandbox/playground" bash "$WORKER_ENTRY" true >/dev/null 2>&1

cfg_mode="$(stat -c '%a' "$PERM_HOME/playground/.git/config")"
play_mode="$(stat -c '%a' "$PERM_HOME/playground")"
check "worker: .git/config has group and other permissions stripped (got $cfg_mode, expected 600)" \
  '[[ "$cfg_mode" == "600" ]]'

check "worker: playground directory has group and other permissions stripped (got $play_mode, expected 700)" \
  '[[ "$play_mode" == "700" ]]'

# Test umask 077 creates new files as 600 / directories as 700
NEW_HOME="$T/new_home"
mkdir -p "$NEW_HOME/playground/.git"   # an existing clone: the entrypoint must not try to reach the forge
HOME="$NEW_HOME" FORGEJO_USER="worker-1" FORGE_REPO="sandbox/playground" \
  bash "$WORKER_ENTRY" bash -c 'touch "$HOME/created.txt" && mkdir "$HOME/created_dir"' >/dev/null 2>&1

new_file_mode="$(stat -c '%a' "$NEW_HOME/created.txt")"
new_dir_mode="$(stat -c '%a' "$NEW_HOME/created_dir")"
check "worker: files created under entrypoint umask are owner-only (got $new_file_mode, expected 600)" \
  '[[ "$new_file_mode" == "600" ]]'

check "worker: directories created under entrypoint umask are owner-only (got $new_dir_mode, expected 700)" \
  '[[ "$new_dir_mode" == "700" ]]'

# ==============================================================================
# 4. worker/entrypoint.sh: Refuse execution as root / privilege dropping
# ==============================================================================
# Simulate root execution by mocking `id -u` to return 0 with an unknown worker user
MOCK_ROOT_BIN="$T/mock_root_bin"
mkdir -p "$MOCK_ROOT_BIN"
cat > "$MOCK_ROOT_BIN/id" << 'EOF'
#!/bin/bash
if [ "${1:-}" = "-u" ]; then
  echo "0"
  exit 0
fi
exec /usr/bin/id "$@"
EOF
chmod +x "$MOCK_ROOT_BIN/id"

check "worker: detects root execution and fails if target user does not exist" \
  '! PATH="$MOCK_ROOT_BIN:$PATH" WORKER_USER="nonexistent_sandbox_user_99" HOME="$T/home" bash "$WORKER_ENTRY" true >/dev/null 2>&1'

# ==============================================================================
# 5. egress/entrypoint.sh: EGRESS_MODE validation
# ==============================================================================
check "egress: rejects invalid mode string" \
  '! EGRESS_MODE="custom_mode" SQUID_CONF_DIR="$ROOT/egress" bash "$EGRESS_ENTRY" >/dev/null 2>&1'

check "egress: rejects EGRESS_MODE command injection" \
  '! EGRESS_MODE="enforce; rm -rf /" SQUID_CONF_DIR="$ROOT/egress" bash "$EGRESS_ENTRY" >/dev/null 2>&1'

check "egress: rejects EGRESS_MODE path traversal" \
  '! EGRESS_MODE="../../etc/passwd" SQUID_CONF_DIR="$ROOT/egress" bash "$EGRESS_ENTRY" >/dev/null 2>&1'

# ==============================================================================
# 6. egress/entrypoint.sh: Safe startup defaults & parse verification
# ==============================================================================
# Mock squid binary to verify startup sequence and arguments
cat > "$MOCK_BIN/squid" << EOF
#!/bin/bash
echo "squid \$*" >> "$T/squid.log"
if [ "\${1:-}" = "-k" ] && [ "\${2:-}" = "parse" ]; then
  exit 0
fi
exit 0
EOF
chmod +x "$MOCK_BIN/squid"

rm -f "$T/squid.log"
PATH="$MOCK_BIN:$PATH" SQUID_CONF_DIR="$ROOT/egress" EGRESS_MODE="" bash "$EGRESS_ENTRY" >/dev/null 2>&1

check "egress: defaults to enforce mode" \
  'grep -q "squid.*squid\.enforce\.conf" "$T/squid.log"'

check "egress: parses and validates enforce config before exec" \
  '[[ "$(head -n 1 "$T/squid.log")" =~ "-k parse -f" && "$(tail -n 1 "$T/squid.log")" =~ "-N -f" ]]'

# Test discovery mode works when explicitly requested
rm -f "$T/squid.log"
PATH="$MOCK_BIN:$PATH" SQUID_CONF_DIR="$ROOT/egress" EGRESS_MODE="discovery" bash "$EGRESS_ENTRY" >/dev/null 2>&1

check "egress: supports explicit discovery mode" \
  'grep -q "squid.*squid\.discovery\.conf" "$T/squid.log"'

# ==============================================================================
# 7. egress/entrypoint.sh: Restrictive umask & root check
# ==============================================================================
# Test umask 027 in egress entrypoint
check "egress: enforces umask 027" \
  'grep -q "umask 027" "$EGRESS_ENTRY"'

check "egress: detects root execution and fails if target proxy user does not exist" \
  '! PATH="$MOCK_ROOT_BIN:$PATH" PROXY_USER="nonexistent_proxy_user_99" SQUID_CONF_DIR="$ROOT/egress" bash "$EGRESS_ENTRY" >/dev/null 2>&1'

# ==============================================================================
# 8. egress/squid.*.conf: the allowlist compares the name in the request only
# ==============================================================================
# Without -n squid reverse-resolves an IP-literal target and matches its PTR record against the list, and the owner of an
# address chooses that record: CONNECT 203.0.113.7:443 would pass whenever 203.0.113.7 is "named" like an allowed host.
for conf in "$ROOT"/egress/squid.*.conf; do
  cname="$(basename "$conf")"
  check "egress: $cname has a dstdomain allowlist" \
    'grep -qE "^acl[[:space:]]+[^[:space:]]+[[:space:]]+dstdomain[[:space:]]" "$conf"'
  check "egress: $cname does not reverse-resolve IP targets (every dstdomain / dstdom_regex ACL has -n)" \
    '! grep -E "^acl[[:space:]]+[^[:space:]]+[[:space:]]+dstdom(ain|_regex)[[:space:]]" "$conf" | grep -qvE "[[:space:]]-n([[:space:]]|$)"'
done

echo "egress security tests: $([ $fail = 0 ] && echo ALL OK || echo FAILED)"
exit $fail
