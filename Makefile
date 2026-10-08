# make check        fast checks, no Docker, no model calls (also what CI runs)
# make test         check + integration tests of the supervisor and the API runtimes (need Docker and the worker images)
# make doctor       end-to-end health check of this install
PY := python3
PYSCRIPTS := bin/foreman bin/dispatch bin/run-supervisor bin/review-branch bin/killswitch bin/open-pr bin/merge-pr bin/forgejo-api \
             bin/doctor bin/status bin/report bin/egress-report bin/audit-rotate bin/wait-run bin/verify-token-refresh bin/upstream bin/secret-set \
             scripts/forge-bootstrap web/server.py
SHSCRIPTS := bin/worker-add bin/worker-up bin/egress-up scripts/install-services scripts/install.sh scripts/vm-baseline-ubuntu.sh \
             worker/entrypoint.sh worker/agy-run worker/af-agent tests/test_supervisor.sh tests/test_runtimes.sh

.PHONY: check test doctor
check:
	@for f in $(PYSCRIPTS) bin/lib/*.py bin/lib/foreman/*.py web/dashboard/*.py tests/*.py; do $(PY) -m py_compile $$f || exit 1; done
	@for f in $(SHSCRIPTS); do bash -n $$f || exit 1; done
	@$(PY) bin/foreman --selftest | tail -1 | grep -q "ALL OK" || { $(PY) bin/foreman --selftest | grep -v '^ok'; exit 1; }
	@$(PY) tests/test_web.py | tail -1
	@$(PY) tests/test_upstream.py | tail -1
	@echo "check: ALL OK"

test: check
	@tests/test_supervisor.sh
	@tests/test_runtimes.sh

doctor:
	@$(PY) bin/doctor
