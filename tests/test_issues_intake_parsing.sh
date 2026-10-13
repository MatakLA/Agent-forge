#!/usr/bin/env bash
# End-to-end integration test for GitHub issue parsing into goals in bin/issues-intake against fake GitHub forge API.
# Public interface: bash tests/test_issues_intake_parsing.sh [RUNS_DIR]
# Invariants:
# - Strictly offline: starts tests/fixtures/fake-forge-api-github.js bound to port 0 and reads assigned port from stdout.
# - Points bin/issues-intake at the fixture with GitHub configuration.
# - Runs intake step once against temporary offline runs directory.
# - Asserts exactly one goal exists per eligible issue matching the label filter (agent-forge).
# - Asserts created goals have status 'new', stripped HTML in text, and clean content.
# - Reads each goal artifact from disk and asserts goal['role'] == mapping[label] from bin/issues-intake's LABEL_TO_ROLE.
# - Guaranteed cleanup of the fixture server on exit under all termination paths via trap.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Verify node availability; per project architecture, skip cleanly if node is not installed
if ! command -v node >/dev/null 2>&1; then
  echo "SKIP: node is not available on host; fake-forge-api-github.js requires node"
  exit 0
fi

FIXTURE="$ROOT/tests/fixtures/fake-forge-api-github.js"
if [ ! -f "$FIXTURE" ]; then
  echo "ERROR: fixture $FIXTURE not found" >&2
  exit 1
fi

TMP_RUNS="${1:-}"
CLEANUP_RUNS=0
if [ -z "$TMP_RUNS" ]; then
  TMP_RUNS="$(mktemp -d)"
  CLEANUP_RUNS=1
fi
mkdir -p "$TMP_RUNS"

SERVER_LOG="$(mktemp)"
SERVER_PID=""

cleanup() {
  local exit_code=$?
  if [ -n "${SERVER_PID:-}" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -f "$SERVER_LOG" 2>/dev/null || true
  if [ "$CLEANUP_RUNS" -eq 1 ] && [ -d "$TMP_RUNS" ]; then
    rm -rf "$TMP_RUNS" 2>/dev/null || true
  fi
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

# Start the fake GitHub API server fixture bound to port 0 (OS-assigned ephemeral port)
node "$FIXTURE" 0 > "$SERVER_LOG" 2>&1 &
SERVER_PID=$!

# Read back the OS-assigned port from the fixture stdout line ('listening on 127.0.0.1:PORT')
PORT=""
for _ in $(seq 1 50); do
  if [ -s "$SERVER_LOG" ]; then
    PORT=$(grep -oE 'listening on 127\.0\.0\.1:[0-9]+' "$SERVER_LOG" | cut -d: -f2 | head -n1 || true)
    if [ -n "$PORT" ]; then
      if python3 -c "import socket; s=socket.socket(); s.connect(('127.0.0.1', $PORT)); s.close()" 2>/dev/null; then
        break
      fi
    fi
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "ERROR: fake-forge-api-github.js process died unexpectedly:" >&2
    cat "$SERVER_LOG" >&2
    exit 1
  fi
  sleep 0.05
done

if [ -z "$PORT" ]; then
  echo "ERROR: failed to detect listening port from fake-forge-api-github.js log:" >&2
  cat "$SERVER_LOG" >&2
  exit 1
fi

# Configure intake environment pointing at the fake GitHub fixture and temp runs dir
export SANDBOX_TEST="1"
export SANDBOX_RUNS_DIR="$TMP_RUNS"
export FORGE_KIND="github"
export FORGE_REPO="agent-forge/agent-forge"
export FORGE_UPSTREAM_TOKEN="ghp_dummy_test_token"
export AF_UPSTREAM_API="http://127.0.0.1:$PORT"

echo "Running intake parsing step..."
python3 "$ROOT/bin/issues-intake" || {
  echo "ERROR: bin/issues-intake run failed" >&2
  exit 1
}

# Verify goals directory and goal count
GOALS_DIR="$TMP_RUNS/foreman/goals"
if [ ! -d "$GOALS_DIR" ]; then
  echo "ERROR: goals directory $GOALS_DIR was not created" >&2
  exit 1
fi

GOAL_COUNT=$(find "$GOALS_DIR" -maxdepth 1 -name "G*.json" | wc -l)
echo "Goals count: $GOAL_COUNT"

if [ "$GOAL_COUNT" -ne 5 ]; then
  echo "ERROR: expected exactly 5 goals, found $GOAL_COUNT" >&2
  exit 1
fi

SEEN_FILE="$TMP_RUNS/foreman/issues-intake.json"
if [ ! -f "$SEEN_FILE" ]; then
  echo "ERROR: seen file $SEEN_FILE was not created" >&2
  exit 1
fi

# Verify created goals, goal content, HTML stripping, seen tracking, role mapping, and role playbooks
python3 -c '
import glob, importlib.machinery, importlib.util, json, os, sys

root_dir = sys.argv[1]
runs_dir = sys.argv[2]

# 1. Load seen file and verify tracked IDs
seen_file = os.path.join(runs_dir, "foreman", "issues-intake.json")
with open(seen_file, "r", encoding="utf-8") as f:
    seen_data = json.load(f)
seen_ids = seen_data.get("seen", [])
expected_ids = [1001, 1002, 1003, 1004, 1005]
if sorted(seen_ids) != sorted(expected_ids):
    sys.stderr.write(f"FAIL: seen IDs mismatch: got {seen_ids}, expected {expected_ids}\n")
    sys.exit(1)

# 2. Read production goal JSON files off disk
goals_dir = os.path.join(runs_dir, "foreman", "goals")
goal_files = sorted(glob.glob(os.path.join(goals_dir, "G*.json")))
if len(goal_files) != 5:
    sys.stderr.write(f"FAIL: expected 5 goal files, found {len(goal_files)}\n")
    sys.exit(1)

loaded_goals = []
for gf in goal_files:
    with open(gf, "r", encoding="utf-8") as f:
        loaded_goals.append(json.load(f))

# Verify goal text, status, and HTML tag stripping
expected_issues = [
    {
        "id": 1001,
        "title": "Fix session cookie expiration bug",
        "forbidden": ["<br>", "<b>", "</b>"],
        "required": ["Fix session cookie expiration bug", "preserve session"]
    },
    {
        "id": 1002,
        "title": "Update documentation for custom providers",
        "forbidden": [],
        "required": ["Update documentation for custom providers", "docs/PROVIDERS.md"]
    },
    {
        "id": 1003,
        "title": "Perform security audit on egress firewall",
        "forbidden": ["<p>", "</p>"],
        "required": ["Perform security audit on egress firewall", "Audit drop policies"]
    },
    {
        "id": 1004,
        "title": "Add token count metric in status",
        "forbidden": [],
        "required": ["Add token count metric in status", "dashboard overview"]
    },
    {
        "id": 1005,
        "title": "Triage incoming community feedback notes",
        "forbidden": [],
        "required": ["Triage incoming community feedback notes", "Community discussion"]
    }
]

for exp in expected_issues:
    matching = [g for g in loaded_goals if exp["title"] in g.get("text", "")]
    if len(matching) != 1:
        sys.stderr.write(f"FAIL: expected exactly 1 goal matching title {exp[\"title\"]!r}, found {len(matching)}\n")
        sys.exit(1)
    goal = matching[0]
    if goal.get("status") != "new":
        sys.stderr.write(f"FAIL: goal status mismatch: got {goal.get(\"status\")!r}, expected \"new\"\n")
        sys.exit(1)
    text = goal.get("text", "")
    if len(text) > 4000:
        sys.stderr.write(f"FAIL: goal text exceeds 4000 characters: length={len(text)}\n")
        sys.exit(1)
    for tag in exp["forbidden"]:
        if tag in text:
            sys.stderr.write(f"FAIL: unstripped HTML tag {tag!r} found in goal text\n")
            sys.exit(1)
    for req in exp["required"]:
        if req not in text:
            sys.stderr.write(f"FAIL: required text {req!r} missing from goal text\n")
            sys.exit(1)

# 3. Read real mapping table from bin/issues-intake and compare directly against goal role fields
intake_path = os.path.join(root_dir, "bin", "issues-intake")
loader = importlib.machinery.SourceFileLoader("issues_intake", intake_path)
spec = importlib.util.spec_from_loader("issues_intake", loader)
issues_intake_mod = importlib.util.module_from_spec(spec)
loader.exec_module(issues_intake_mod)

if not hasattr(issues_intake_mod, "LABEL_TO_ROLE"):
    sys.stderr.write("FAIL: bin/issues-intake does not expose LABEL_TO_ROLE mapping table\n")
    sys.exit(1)

mapping = issues_intake_mod.LABEL_TO_ROLE

# Verify expected roles differ across at least 3 distinct labels
distinct_roles = {mapping.get("bug"), mapping.get("docs"), mapping.get("security"), mapping.get("feature")}
if len(distinct_roles) < 3:
    sys.stderr.write(f"FAIL: expected roles must differ across at least 3 distinct labels: got {distinct_roles}\n")
    sys.exit(1)

# Direct comparison of actual goal role field read off disk against mapping[label]
goal_bug = next(g for g in loaded_goals if "Fix session cookie expiration bug" in g.get("text", ""))
goal_docs = next(g for g in loaded_goals if "Update documentation for custom providers" in g.get("text", ""))
goal_sec = next(g for g in loaded_goals if "Perform security audit on egress firewall" in g.get("text", ""))
goal_feat = next(g for g in loaded_goals if "Add token count metric in status" in g.get("text", ""))

for name, goal_obj, expected_role in [
    ("bug", goal_bug, mapping.get("bug")),
    ("docs", goal_docs, mapping.get("docs")),
    ("security", goal_sec, mapping.get("security")),
    ("feature", goal_feat, mapping.get("feature")),
]:
    actual_role = goal_obj.get("role")
    if actual_role != expected_role:
        sys.stderr.write(f"FAIL: goal for {name} label role mismatch: got {actual_role!r}, expected {expected_role!r} (from mapping[{name!r}])\n")
        sys.exit(1)

# 4. Verify each documented role playbook exists in roles/
documented_roles = ["tester", "technical-writer", "security-tester", "backend-developer"]
for r in documented_roles:
    playbook_file = os.path.join(root_dir, "roles", f"{r}.md")
    if not os.path.isfile(playbook_file):
        sys.stderr.write(f"FAIL: playbook {playbook_file} not found\n")
        sys.exit(1)
' "$ROOT" "$TMP_RUNS" || exit 1

echo "ok: parsing e2e test passed (port bound to 0, exactly 5 goals created, HTML stripped, role mapping verified, valid playbooks)"
