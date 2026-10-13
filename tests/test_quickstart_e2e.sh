#!/usr/bin/env bash
# Quickstart end-to-end integration test runner: drives examples/sample-project through bin/foreman.
#
# Public interface:
#   bash tests/test_quickstart_e2e.sh
#
# Invariants:
#   - If node is not on PATH, prints a one-line explanation and exits 0 (skip).
#   - Otherwise, arms EXIT trap to invoke tests/test_quickstart_setup.sh down,
#     runs tests/test_quickstart_setup.sh up, exports 6 KEY=VALUE lines,
#     executes python3 tests/fixtures/quickstart_e2e_runner.py, and exits with its exact return code.
#   - Never touches real runs/ or ~/sandbox directories.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 1. Skip cleanly if node is not on PATH (matching tests/test_runtime_goose.sh)
if ! command -v node >/dev/null 2>&1; then
  echo "SKIP: node is not available on host; quickstart e2e check requires node"
  exit 0
fi

# 2. Arm cleanup trap for EXIT
trap 'tests/test_quickstart_setup.sh down' EXIT

# 3. Run setup up and export all six KEY=VALUE lines
UP_OUT=$(tests/test_quickstart_setup.sh up)
eval "$UP_OUT"
export ANTHROPIC_API_BASE OPENAI_API_BASE GEMINI_API_BASE QUICKSTART_RUNS_DIR QUICKSTART_FAKE_API_PID QUICKSTART_FAKE_DOCKER_DIR

# 4. Run quickstart_e2e_runner.py and exit with its exact return code
python3 tests/fixtures/quickstart_e2e_runner.py
