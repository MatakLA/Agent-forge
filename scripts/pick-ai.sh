#!/usr/bin/env bash
# scripts/pick-ai.sh — Public setup wizard for choosing an AI in Agent-Forge.
#
# Public interface:
#   scripts/pick-ai.sh [--help|-h]
#
# Invariants:
#   - Resolves repo root robustly via BASH_SOURCE without relying on caller cwd.
#   - Strictly offline, no container runtime calls, and no external network calls.
#   - Runs bin/doctor to display system and readiness diagnostic status to the owner.
#   - Invokes foreman.wizard.main() to display ranked AI recommendations and next steps.
#   - Exits 0 on success; fails closed with a clear, one-line actionable message and no raw traceback.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Show help
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  cat <<'EOF'
Usage: scripts/pick-ai.sh

Offline AI setup wizard for Agent-Forge.
Runs system readiness diagnostics and prints ranked AI recommendations with next steps.

Options:
  -h, --help  Show this help message
EOF
  exit 0
fi

# Ensure python3 is available
if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is required to run the AI setup wizard. Please install Python 3.10 or newer." >&2
  exit 1
fi

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
  echo "Error: Python 3.10 or newer is required to run the AI setup wizard." >&2
  exit 1
fi

DOCTOR="$ROOT/bin/doctor"
if [ ! -f "$DOCTOR" ]; then
  echo "Error: bin/doctor not found at $DOCTOR." >&2
  exit 1
fi

# Run bin/doctor to show system and runtime/provider readiness to the owner.
# bin/doctor exits 0 (all ok), 1 (warnings only), or 2 (unconfigured / problems found).
set +e
if [ -x "$DOCTOR" ]; then
  "$DOCTOR"
else
  python3 "$DOCTOR"
fi
doctor_rc=$?
set -e

if [ "$doctor_rc" -ne 0 ] && [ "$doctor_rc" -ne 1 ] && [ "$doctor_rc" -ne 2 ]; then
  echo "Error: bin/doctor failed to run (exit code $doctor_rc)." >&2
  exit "$doctor_rc"
fi

echo ""

# Print wizard ranked recommendation
set +e
wizard_out=$(PYTHONPATH="$ROOT/bin/lib" python3 -c '
import sys
try:
    from foreman import wizard
    sys.exit(wizard.main())
except Exception as e:
    sys.stderr.write(f"Error: failed to run wizard: {e}\n")
    sys.exit(1)
')
wizard_rc=$?
set -e

if [ "$wizard_rc" -ne 0 ]; then
  echo "Error: wizard module could not run." >&2
  exit "$wizard_rc"
fi

printf "%s\n" "$wizard_out"
exit 0
