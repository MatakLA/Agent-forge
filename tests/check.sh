#!/usr/bin/env bash
# Fast checks, no Docker, no model calls: what `make check` runs and what a project gate can run (`bash tests/check.sh`).
set -u
cd "$(dirname "$0")/.."
PY=${PY:-python3}
PYSCRIPTS="bin/foreman bin/dispatch bin/run-supervisor bin/review-branch bin/killswitch bin/open-pr bin/merge-pr bin/forgejo-api
  bin/doctor bin/status bin/report bin/egress-report bin/audit-rotate bin/wait-run bin/verify-token-refresh bin/upstream bin/secret-set
  bin/issues-intake scripts/forge-bootstrap web/server.py worker/af-aider-events tests/fixtures/logging-proxy.py"
SHSCRIPTS="bin/worker-add bin/worker-up bin/egress-up scripts/install-services scripts/install.sh scripts/vm-baseline-ubuntu.sh tests/test_ui.sh
  tools/fake-docker egress/entrypoint.sh worker/entrypoint.sh worker/agy-run worker/af-agent tests/test_supervisor.sh tests/test_runtimes.sh tests/test_egress_security.sh worker/git-credential-forgejo tests/test_install_security.sh tests/test_install_smoke.sh tests/test_demo_mode.sh"
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
if [ -f tests/test_runtime_qwen.sh ]; then bash tests/test_runtime_qwen.sh || exit 1; fi
if [ -f tests/test_runtime_goose.sh ]; then bash tests/test_runtime_goose.sh || exit 1; fi
if command -v node >/dev/null 2>&1; then
  if [ -f tests/test_quickstart_e2e.sh ]; then bash tests/test_quickstart_e2e.sh || exit 1; fi
else
  echo "SKIP tests/test_quickstart_e2e.sh: node not available"
fi
if [ -f tests/test_runtime_anthropic_compat.sh ]; then bash tests/test_runtime_anthropic_compat.sh || exit 1; fi
if command -v node >/dev/null 2>&1; then
  if [ -f tests/test_runtime_mistral_vibe.sh ]; then bash tests/test_runtime_mistral_vibe.sh || exit 1; fi
else
  echo "SKIP tests/test_runtime_mistral_vibe.sh: node not available"
fi
if [ -f tests/test_runtime_mistral_vibe.py ]; then
  $PY tests/test_runtime_mistral_vibe.py || exit 1
fi
if command -v node >/dev/null 2>&1; then
  if [ -f tests/test_runtime_copilot.sh ]; then bash tests/test_runtime_copilot.sh || exit 1; fi
else
  echo "SKIP tests/test_runtime_copilot.sh: node not available"
fi
if [ -f tests/test_runtime_copilot.py ]; then
  $PY tests/test_runtime_copilot.py || exit 1
fi
if [ -f tests/test_goose_registry.py ]; then
  $PY tests/test_goose_registry.py || exit 1
fi
if [ -f tests/test_runtime_goose.py ]; then
  $PY tests/test_runtime_goose.py || exit 1
fi
if [ -f tests/test_quickstart_e2e.py ]; then
  $PY tests/test_quickstart_e2e.py || exit 1
fi
if [ -f tests/test_anthropic_compat_registry.py ]; then
  $PY tests/test_anthropic_compat_registry.py || exit 1
fi
if [ -f tests/test_sbx_security.py ]; then
  $PY tests/test_sbx_security.py || exit 1
fi
if [ -f tests/test_foreman_store.py ]; then
  $PY tests/test_foreman_store.py || exit 1
fi
if [ -f tests/test_issues_intake.py ]; then
  $PY tests/test_issues_intake.py || exit 1
fi
if command -v node >/dev/null 2>&1; then
  if [ -f tests/test_issues_intake_parsing.sh ]; then
    bash tests/test_issues_intake_parsing.sh || exit 1
  fi
  if [ -f tests/test_issues_intake_parsing.py ]; then
    $PY tests/test_issues_intake_parsing.py || exit 1
  fi
  if [ -f tests/test_issues_intake_duplicates.sh ]; then
    bash tests/test_issues_intake_duplicates.sh || exit 1
  fi
  if [ -f tests/test_issues_intake_duplicates.py ]; then
    $PY tests/test_issues_intake_duplicates.py || exit 1
  fi
  if [ -f tests/test_issues_intake_malformed.sh ]; then
    bash tests/test_issues_intake_malformed.sh || exit 1
  fi
  if [ -f tests/test_issues_intake_malformed.py ]; then
    $PY tests/test_issues_intake_malformed.py || exit 1
  fi
fi
if [ -f tests/test_install_smoke.sh ]; then
  bash tests/test_install_smoke.sh || exit 1
fi
if [ -f tests/test_install_smoke.py ]; then
  python3 tests/test_install_smoke.py || exit 1
fi
if [ -f tests/test_demo_mode.sh ]; then
  bash tests/test_demo_mode.sh || exit 1
fi
if [ -f tests/test_demo_mode.py ]; then
  $PY tests/test_demo_mode.py || exit 1
fi
if [ -f tests/test_runtime_anthropic_compat.py ]; then
  $PY tests/test_runtime_anthropic_compat.py || exit 1
fi
if [ -f tests/test_orphan_warning_ui.py ]; then
  $PY tests/test_orphan_warning_ui.py || exit 1
fi
echo "check: ALL OK"



