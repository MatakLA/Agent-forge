#!/usr/bin/env bash
# Property-based tests (fast-check) of the dashboard's pure helpers: tests/props.js against web/static/js/core.js.
# fast-check is a test-only dependency, installed with `npm ci` from tests/node/props/package-lock.json into a throwaway
# directory (nothing is added to the repository or the install). Needs node >= 18 and npm; without them the test is skipped.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then echo "property tests: node/npm missing, skipped"; exit 0; fi
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
cp "$ROOT"/tests/node/props/package*.json "$ROOT/tests/props.js" "$ROOT/web/static/js/core.js" "$T/"
cd "$T"
HOME="$T" npm ci --silent --no-audit --no-fund --ignore-scripts
AF_CORE_JS="$T/core.js" node props.js
