#!/usr/bin/env bash
# Fast checks, no Docker, no model calls: what `make check` runs and what a project gate can run (`bash tests/check.sh`).
set -u
cd "$(dirname "$0")/.." || exit 1
PY=${PY:-python3}
# gate CMD...: run a test, show its last line, and stop the whole run when it fails (a bare `| tail -1` would hide the exit code)
gate() { local out; out=$("$@" 2>&1) || { printf '%s\n' "$out"; echo "FAILED: $*"; exit 1; }; printf '%s\n' "$out" | tail -1; }
# the tests must never touch a live install: its kill switch and settings stay exactly as they were
live_state() { cat runs/KILLSWITCH runs/KILLSWITCH-* etc/foreman.conf etc/limits.conf 2>/dev/null | cksum; }
live_before=$(live_state)
PYSCRIPTS="bin/foreman bin/dispatch bin/run-supervisor bin/review-branch bin/killswitch bin/open-pr bin/merge-pr bin/forgejo-api
  bin/doctor bin/status bin/report bin/egress-report bin/audit-rotate bin/wait-run bin/verify-token-refresh bin/upstream bin/secret-set
  bin/issues-intake scripts/forge-bootstrap web/server.py worker/af-aider-events tests/fixtures/logging-proxy.py"
SHSCRIPTS="bin/worker-add bin/worker-remove bin/worker-up bin/egress-up scripts/install-services scripts/install.sh scripts/update.sh scripts/uninstall.sh install.sh forge tests/test_forge_cli.sh tests/test_install_flow.sh tests/test_egress_up_modes.sh tools/lint tools/sign-release scripts/vm-baseline-ubuntu.sh tests/test_ui.sh tests/test_a11y.sh tests/test_props.sh
  tools/fake-docker egress/entrypoint.sh worker/entrypoint.sh worker/agy-run worker/af-agent tests/test_supervisor.sh tests/test_review_branch.sh tests/test_runtimes.sh tests/test_egress_security.sh worker/git-credential-forgejo tests/test_install_security.sh tests/test_install_smoke.sh tests/test_demo_mode.sh tests/test_worker_remove.sh
  scripts/pick-ai.sh tests/test_pick_ai.sh"
for f in $PYSCRIPTS bin/lib/*.py bin/lib/foreman/*.py web/dashboard/*.py tests/*.py; do
  $PY -c 'import sys; compile(open(sys.argv[1]).read(), sys.argv[1], "exec")' "$f" || { echo "syntax error: $f"; exit 1; }
done
for f in $SHSCRIPTS; do bash -n "$f" || { echo "syntax error: $f"; exit 1; }; done
if command -v node >/dev/null 2>&1; then
  for f in tests/*.js web/static/js/*.js; do
    node -c "$f" || { echo "syntax error: $f"; exit 1; };
  done
fi
gate bash tests/test_props.sh        # property-based tests of the page's helpers (fast-check); skipped without node
$PY bin/foreman --selftest | tail -1 | grep -q "ALL OK" || { $PY bin/foreman --selftest | grep -v '^ok'; exit 1; }
gate $PY tests/test_web.py
gate $PY tests/test_upstream.py
gate $PY tests/test_merge_pr.py
gate $PY tests/test_review_branch_failclosed.py
if [ -f tests/test_foreman_loop.py ]; then
  gate $PY tests/test_foreman_loop.py
fi
if [ -f tests/test_foreman_merge.py ]; then
  $PY tests/test_foreman_merge.py | tail -1 | grep "ALL OK" || { $PY tests/test_foreman_merge.py; exit 1; }
fi
if [ -f tests/test_foreman_settings.py ]; then
  gate $PY tests/test_foreman_settings.py
fi
$PY tests/test_foreman_languages.py 2>&1 | tail -1 | grep -q '^OK' || { $PY tests/test_foreman_languages.py; exit 1; }
$PY tests/test_quality.py 2>&1 | tail -1 | grep -q "^OK" || { $PY tests/test_quality.py; exit 1; }
echo "language tests: ALL OK"
if [ -f tests/test_egress_security.sh ]; then
  gate bash tests/test_egress_security.sh
fi
if [ -f tests/test_foreman_pool.py ]; then
  $PY tests/test_foreman_pool.py | grep -q "ALL OK" || { $PY tests/test_foreman_pool.py; exit 1; }
  echo "pool tests: ALL OK"
fi
$PY tests/test_locales.py 2>&1 | tail -1 | grep -q '^OK' || { $PY tests/test_locales.py; exit 1; }
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
# Runtime test suites with node-availability guards: mistral-vibe, copilot, local-compat
if command -v node >/dev/null 2>&1; then
  if [ -f tests/test_runtime_mistral_vibe.sh ]; then
    bash tests/test_runtime_mistral_vibe.sh || exit 1
  fi
else
  echo "SKIP tests/test_runtime_mistral_vibe.sh: node not available"
fi
if [ -f tests/test_runtime_mistral_vibe.py ]; then
  $PY tests/test_runtime_mistral_vibe.py || exit 1
fi
if command -v node >/dev/null 2>&1; then
  if [ -f tests/test_runtime_copilot.sh ]; then
    bash tests/test_runtime_copilot.sh || exit 1
  fi
else
  echo "SKIP tests/test_runtime_copilot.sh: node not available"
fi
if [ -f tests/test_runtime_copilot.py ]; then
  $PY tests/test_runtime_copilot.py || exit 1
fi
if command -v node >/dev/null 2>&1; then
  if [ -f tests/test_runtime_local_compat.sh ]; then
    bash tests/test_runtime_local_compat.sh || exit 1
  fi
else
  echo "SKIP tests/test_runtime_local_compat.sh: node not available"
fi
if [ -f tests/test_runtime_local_compat.py ]; then
  $PY tests/test_runtime_local_compat.py || exit 1
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
if [ -f tests/test_forge_cli.sh ]; then
  bash tests/test_forge_cli.sh | tail -1 | grep -q "ALL OK" || { bash tests/test_forge_cli.sh; exit 1; }
fi
gate bash tests/test_egress_up_modes.sh
if [ -f tests/test_install_flow.sh ]; then
  bash tests/test_install_flow.sh | tail -1 | grep -q "ALL OK" || { bash tests/test_install_flow.sh; exit 1; }
fi
if [ -f tests/test_install_smoke.sh ]; then
  bash tests/test_install_smoke.sh || exit 1
fi
if [ -f tests/test_install_smoke.py ]; then
  python3 tests/test_install_smoke.py || exit 1
fi
if [ -f tests/test_demo_mode.sh ] && [ "${RUN_DOCKER_TESTS:-0}" = "1" ]; then
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
if [ -f tests/test_wizard.py ]; then
  $PY tests/test_wizard.py || exit 1
fi
if [ -f tests/test_pick_ai.sh ]; then
  bash tests/test_pick_ai.sh || exit 1
fi
if [ -f tests/test_egress_logs.py ]; then
  $PY tests/test_egress_logs.py | tail -1 | grep -q "ALL OK" || { $PY tests/test_egress_logs.py; exit 1; }
fi
if [ -f tests/test_worker_remove.sh ]; then
  bash tests/test_worker_remove.sh | tail -1 | grep -q "ALL OK" || { bash tests/test_worker_remove.sh; exit 1; }
fi
if [ -f tests/test_cache.py ]; then
  $PY tests/test_cache.py | tail -1 | grep -q "ALL OK" || { $PY tests/test_cache.py; exit 1; }
fi
if [ -f tests/test_docs_links.py ]; then
  $PY tests/test_docs_links.py 2>&1 | tail -1 | grep -q "^OK" || { $PY tests/test_docs_links.py; exit 1; }
fi
if [ -f tests/test_creatable_runtimes.py ]; then
  $PY tests/test_creatable_runtimes.py || exit 1
fi
for t in tests/test_cost_model_reporting.py \
         tests/test_dashboard_backend.py \
         tests/test_doctor_egress_hosts.py \
         tests/test_doctor_providers.py \
         tests/test_doctor_readiness.py \
         tests/test_egress_runtime_hosts.py \
         tests/test_fake_model_api_providers.py \
         tests/test_fake_model_api_providers.sh \
         tests/test_foreman_llm.py \
         tests/test_foreman_prompts.py \
         tests/test_foreman_readiness.py \
         tests/test_hostile_bytes.py \
         tests/test_llm_providers_security.py \
         tests/test_provider_egress_hosts.py \
         tests/test_quickstart_budget.py \
         tests/test_quickstart_setup.sh \
         tests/test_runtime_cost_model.py \
         tests/test_runtime_qwen.py \
         tests/test_self_check.py \
         tests/test_settings_conf_audit.py \
         tests/test_settings_egress_validation.py \
         tests/test_settings_providers.py; do
  case "$t" in
    *.sh) out="$(bash "$t" 2>&1)" ;;
    *) out="$($PY "$t" 2>&1)" ;;
  esac || { printf '%s\n' "$out"; echo "FAILED: $t"; exit 1; }
done
# every test file must be run by this script, or it silently rots
for t in tests/test_*; do
  grep -qF "$(basename "$t")" tests/check.sh || { echo "not run by tests/check.sh: $t"; exit 1; }
done
[ "$(live_state)" = "$live_before" ] || { echo "the tests changed runs/KILLSWITCH or etc/*.conf of this checkout"; exit 1; }
echo "check: ALL OK"
