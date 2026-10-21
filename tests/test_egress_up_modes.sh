#!/usr/bin/env bash
# test_egress_up_modes.sh - bin/egress-up leaves the mounted squid files readable for the proxy user even under the installer's umask 077.
# Public interface: bash tests/test_egress_up_modes.sh
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d "${TMPDIR:-/tmp}/af-egress-up.XXXXXX")"
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/repo/bin" "$T/repo/egress" "$T/stub"
cp "$ROOT/bin/egress-up" "$T/repo/bin/"
printf '#!/usr/bin/env bash\necho "mode: enforce"\n' > "$T/stub/docker"; chmod +x "$T/stub/docker"
( umask 077; PATH="$T/stub:$PATH" bash "$T/repo/bin/egress-up" enforce >/dev/null )
for f in allowlist.local.txt local-endpoint.conf; do
  mode=$(stat -c %a "$T/repo/egress/$f")
  [ "$mode" = 644 ] || { echo "FAIL: egress/$f has mode $mode, the proxy user cannot read it"; exit 1; }
done
echo "egress-up modes: ALL OK"
