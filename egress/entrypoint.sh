#!/bin/bash
set -euo pipefail
MODE="${EGRESS_MODE:-enforce}"
CONF="/etc/squid/squid.${MODE}.conf"
[ -f "$CONF" ] || { echo "unknown EGRESS_MODE=$MODE" >&2; exit 1; }
echo "egress-proxy starting in mode: $MODE" >&2
squid -k parse -f "$CONF" >&2
exec squid -N -f "$CONF"
