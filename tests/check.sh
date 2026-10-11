#!/usr/bin/env bash
# Fast checks, no Docker, no model calls: what `make check` runs and what a project gate can run (`bash tests/check.sh`).
set -u
cd "$(dirname "$0")/.."
PY=${PY:-python3}
PYSCRIPTS="bin/foreman bin/dispatch bin/run-supervisor bin/review-branch bin/killswitch bin/open-pr bin/merge-pr bin/forgejo-api
  bin/doctor bin/status bin/report bin/egress-report bin/audit-rotate bin/wait-run bin/verify-token-refresh bin/upstream bin/secret-set
  scripts/forge-bootstrap web/server.py worker/af-aider-events tests/fixtures/logging-proxy.py"
SHSCRIPTS="bin/worker-add bin/worker-up bin/egress-up scripts/install-services scripts/install.sh scripts/vm-baseline-ubuntu.sh tests/test_ui.sh
  tools/fake-docker worker/entrypoint.sh worker/agy-run worker/af-agent tests/test_supervisor.sh tests/test_runtimes.sh"
for f in $PYSCRIPTS bin/lib/*.py bin/lib/foreman/*.py web/dashboard/*.py tests/*.py; do
  $PY -c 'import sys; compile(open(sys.argv[1]).read(), sys.argv[1], "exec")' "$f" || { echo "syntax error: $f"; exit 1; }
done
for f in $SHSCRIPTS; do bash -n "$f" || { echo "syntax error: $f"; exit 1; }; done
$PY bin/foreman --selftest | tail -1 | grep -q "ALL OK" || { $PY bin/foreman --selftest | grep -v '^ok'; exit 1; }
$PY tests/test_web.py | tail -1
$PY tests/test_upstream.py | tail -1
echo "check: ALL OK"
