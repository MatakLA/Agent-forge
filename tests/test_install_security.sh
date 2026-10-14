#!/usr/bin/env bash
# test_install_security.sh — static analysis and regression checks for install scripts security.
# Verifies that scripts/install.sh and scripts/vm-baseline-ubuntu.sh enforce restrictive umasks,
# create configuration and secrets directories with 0700/0600 modes, do not use unpinned
# remote execution patterns (curl|bash / wget|sh), do not define world-writable modes,
# and never echo secret tokens or credentials.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Check 1: scripts/install.sh must enforce restrictive umask (077)
if ! grep -qE '^umask 077' scripts/install.sh; then
  echo "FAIL: scripts/install.sh must set umask 077 at top of script" >&2
  exit 1
fi

# Check 2: scripts/install.sh must restrict permissions on configuration directory and files
if ! grep -qE 'chmod 700 ("etc"|etc)' scripts/install.sh; then
  echo "FAIL: scripts/install.sh must enforce mode 700 on etc/ directory" >&2
  exit 1
fi

if ! grep -qE 'chmod 600 ("etc/\$f\.conf"|etc/\$f\.conf)' scripts/install.sh; then
  echo "FAIL: scripts/install.sh must enforce mode 600 on etc/*.conf files" >&2
  exit 1
fi

# Check 3: scripts/install.sh must ensure secrets directory has restrictive mode (700)
if ! grep -qE 'chmod 700 ("\$SECRETS"|\$SECRETS)' scripts/install.sh; then
  echo "FAIL: scripts/install.sh must enforce mode 700 on secrets directory" >&2
  exit 1
fi

# Check 4: scripts/vm-baseline-ubuntu.sh must set a restrictive umask
if ! grep -qE '^umask 02[27]' scripts/vm-baseline-ubuntu.sh; then
  echo "FAIL: scripts/vm-baseline-ubuntu.sh must set restrictive umask" >&2
  exit 1
fi

# Check 5: No unpinned remote script execution (curl|bash or wget|sh) in install scripts
if grep -E '(curl|wget)\s+[^|]+[|]\s*(ba)?sh' scripts/install.sh scripts/vm-baseline-ubuntu.sh; then
  echo "FAIL: dangerous unpinned remote script execution pattern found" >&2
  exit 1
fi

# Check 6: No permissive world-writable permissions (e.g. chmod 777 or a+w)
if grep -E 'chmod\s+(0?777|a\+[wW]|o\+[wW])' scripts/install.sh scripts/vm-baseline-ubuntu.sh; then
  echo "FAIL: insecure permissive chmod found" >&2
  exit 1
fi

# Check 7: No secrets or generated tokens echoed or printed to terminal/stdout
if grep -iE '(echo|printf)\s+.*(\$(TOKEN|PASSWORD|SECRET)|token=|password=)' scripts/install.sh scripts/vm-baseline-ubuntu.sh; then
  echo "FAIL: potential secret exposure in stdout/echo commands" >&2
  exit 1
fi

# Check 8: Adversarial functional test: simulate install config creation under permissive caller umask (0022)
TMP_DIR="$(mktemp -d /tmp/af-install-sec-test.XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT

# Copy current install script and minimal test environment
mkdir -p "$TMP_DIR/etc" "$TMP_DIR/scripts"
cp etc/limits.example.conf "$TMP_DIR/etc/limits.example.conf"
cp etc/foreman.example.conf "$TMP_DIR/etc/foreman.example.conf"
cp etc/forge.example.conf "$TMP_DIR/etc/forge.example.conf"
cp etc/team-rules.example.md "$TMP_DIR/etc/team-rules.example.md"
cp scripts/install.sh "$TMP_DIR/scripts/install.sh"

# Run configuration step with permissive umask 0022
(
  export SANDBOX_SECRETS="$TMP_DIR/secrets"
  cd "$TMP_DIR"
  umask 0022
  # Extract and execute configuration logic from scripts/install.sh
  bash -c '
    set -e
    # Run the umask and configuration block from scripts/install.sh
    eval "$(grep -E "^umask 077" scripts/install.sh || true)"
    mkdir -p etc
    eval "$(grep -E "chmod 700 etc" scripts/install.sh || true)"
    for f in limits foreman forge; do
      if [ ! -f "etc/$f.conf" ]; then
        cp "etc/$f.example.conf" "etc/$f.conf"
      fi
      eval "$(grep -E "chmod 600.*etc/\$f\.conf" scripts/install.sh || true)"
    done
    SECRETS="${SANDBOX_SECRETS:-$HOME/.config/sandbox/secrets}"
    mkdir -p "$SECRETS"
    eval "$(grep -E "chmod 700.*SECRETS" scripts/install.sh || true)"
  '
)

# Verify permissions: etc must be 700 (or at least not group/world readable)
ETC_MODE="$(stat -c '%a' "$TMP_DIR/etc")"
if [ "$ETC_MODE" != "700" ]; then
  echo "FAIL: etc directory has mode $ETC_MODE, expected 700" >&2
  exit 1
fi

# Verify permissions: etc/*.conf must be 600 (not group/world readable)
for f in limits foreman forge; do
  CONF_MODE="$(stat -c '%a' "$TMP_DIR/etc/$f.conf")"
  if [ "$CONF_MODE" != "600" ]; then
    echo "FAIL: etc/$f.conf has mode $CONF_MODE, expected 600" >&2
    exit 1
  fi
done

# Verify permissions: secrets dir must be 700
SEC_MODE="$(stat -c '%a' "$TMP_DIR/secrets")"
if [ "$SEC_MODE" != "700" ]; then
  echo "FAIL: secrets directory has mode $SEC_MODE, expected 700" >&2
  exit 1
fi

echo "install security tests: ALL OK"
