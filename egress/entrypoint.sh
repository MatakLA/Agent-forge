#!/bin/bash
# Egress proxy entrypoint: validates mode, drops root if needed, parses enforce config, and execs squid.
# Invariants:
# - Runs with restrictive umask (027) so PID/log files are not world-readable/writable.
# - MODE is strictly validated against ^(enforce|discovery)$; defaults to enforce.
# - Configuration is validated with squid -k parse before opening port 3128.
# - Enforce config is active before any client connection can be accepted (no default-allow window).
# - Refuses to run as root if invoked as uid 0; drops privileges to proxy user.
set -euo pipefail
umask 027

if [ "$(id -u)" -eq 0 ]; then
  TARGET_USER="${PROXY_USER:-proxy}"
  if id "$TARGET_USER" >/dev/null 2>&1; then
    if command -v runuser >/dev/null 2>&1; then
      exec runuser -m -u "$TARGET_USER" -- "$0" "$@"
    elif command -v su >/dev/null 2>&1; then
      exec su -m "$TARGET_USER" -c 'exec "$@"' -- "$0" "$@"
    fi
  fi
  echo "egress entrypoint: refuse to run as root and cannot switch to non-root user ($TARGET_USER)" >&2
  exit 1
fi

MODE="${EGRESS_MODE:-enforce}"
[[ "$MODE" =~ ^(enforce|discovery)$ ]] || { echo "invalid EGRESS_MODE: $MODE" >&2; exit 1; }
CONF="${SQUID_CONF_DIR:-/etc/squid}/squid.${MODE}.conf"
[ -f "$CONF" ] || { echo "missing configuration file: $CONF" >&2; exit 1; }
echo "egress-proxy starting in mode: $MODE" >&2
squid -k parse -f "$CONF" >&2
exec squid -N -f "$CONF"
