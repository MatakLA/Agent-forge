#!/usr/bin/env bash
# Fast checks, no Docker, no model calls: what `make check` runs and what a project gate can run (`bash tests/check.sh`).
set -u
cd "$(dirname "$0")/.."
PY=${PY:-python3}
PYSCRIPTS="bin/foreman bin/dispatch bin/run-supervisor bin/review-branch bin/killswitch bin/open-pr bin/merge-pr bin/forgejo-api
  bin/doctor bin/status bin/report bin/egress-report bin/audit-rotate bin/wait-run bin/verify-token-refresh bin/upstream bin/secret-set
  scripts/forge-bootstrap web/server.py worker/af-aider-events tests/fixtures/logging-proxy.py"
SHSCRIPTS="bin/worker-add bin/worker-up bin/egress-up scripts/install-services scripts/install.sh scripts/vm-baseline-ubuntu.sh tests/test_ui.sh
  tools/fake-docker egress/entrypoint.sh worker/entrypoint.sh worker/agy-run worker/af-agent tests/test_supervisor.sh tests/test_runtimes.sh tests/test_egress_security.sh worker/git-credential-forgejo tests/test_install_security.sh"
for f in $PYSCRIPTS bin/lib/*.py bin/lib/foreman/*.py web/dashboard/*.py tests/*.py; do
  $PY -c 'import sys; compile(open(sys.argv[1]).read(), sys.argv[1], "exec")' "$f" || { echo "syntax error: $f"; exit 1; }
done
for f in $SHSCRIPTS; do bash -n "$f" || { echo "syntax error: $f"; exit 1; }; done
if command -v node >/dev/null 2>&1; then
  for f in tests/*.js web/static/js/*.js; do
    node -c "$f" || { echo "syntax error: $f"; exit 1; };
  done
fi
$PY bin/foreman --selftest | tail -1 | grep -q "ALL OK" || { $PY bin/foreman --selftest | grep -v '^ok'; exit 1; }
$PY tests/test_web.py | tail -1
$PY tests/test_upstream.py | tail -1
if [ -f tests/test_foreman_loop.py ]; then
  $PY tests/test_foreman_loop.py | tail -1
fi
if [ -f tests/test_foreman_merge.py ]; then
  $PY tests/test_foreman_merge.py | tail -1 | grep "ALL OK" || { $PY tests/test_foreman_merge.py; exit 1; }
fi
if [ -f tests/test_foreman_settings.py ]; then
  $PY tests/test_foreman_settings.py | tail -1
fi
if [ -f tests/test_egress_security.sh ]; then
  bash tests/test_egress_security.sh | tail -1
fi
if [ -f tests/test_foreman_pool.py ]; then
  $PY tests/test_foreman_pool.py | grep -q "ALL OK" || { $PY tests/test_foreman_pool.py; exit 1; }
  echo "pool tests: ALL OK"
fi
if [ -f tests/test_knowledge_paths.py ]; then
  $PY tests/test_knowledge_paths.py || exit 1
fi
if [ -f tests/test_credentials.py ]; then
  $PY tests/test_credentials.py || exit 1
fi
if [ -f tests/test_install_security.sh ]; then
  bash tests/test_install_security.sh || exit 1
fi
if [ -f tests/test_opencode_registries.py ]; then
  $PY tests/test_opencode_registries.py || exit 1
fi
if [ -f tests/test_runtime_opencode.py ]; then
  $PY tests/test_runtime_opencode.py || exit 1
fi
if [ -f tests/test_runtime_opencode.sh ]; then bash tests/test_runtime_opencode.sh || exit 1; fi
if [ -f tests/test_goose_registry.py ]; then
  $PY tests/test_goose_registry.py || exit 1
fi
if [ -f tests/test_sbx_security.py ]; then
  $PY tests/test_sbx_security.py || exit 1
fi
echo "check: ALL OK"



