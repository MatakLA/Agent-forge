# make check        fast checks, no Docker, no model calls (also what CI runs)
# make test         check + integration tests of the supervisor, the API runtimes and the dashboard page (need Docker)
# make doctor       end-to-end health check of this install
PY := python3
PYSCRIPTS := bin/foreman bin/dispatch bin/run-supervisor bin/review-branch bin/killswitch bin/open-pr bin/merge-pr bin/forgejo-api \
             bin/doctor bin/status bin/report bin/egress-report bin/audit-rotate bin/wait-run bin/verify-token-refresh bin/upstream bin/secret-set \
             scripts/forge-bootstrap web/server.py worker/af-aider-events tests/fixtures/logging-proxy.py
SHSCRIPTS := bin/worker-add bin/worker-up bin/egress-up scripts/install-services scripts/install.sh scripts/vm-baseline-ubuntu.sh tests/test_ui.sh tools/fake-docker \
             egress/entrypoint.sh worker/entrypoint.sh worker/agy-run worker/af-agent tests/test_supervisor.sh tests/test_runtimes.sh tests/test_egress_security.sh worker/git-credential-forgejo

.PHONY: check test doctor
check:
	@bash tests/check.sh

test: check
	@tests/test_supervisor.sh
	@tests/test_runtimes.sh
	@tests/test_ui.sh
	@RUN_DOCKER_TESTS=1 tests/test_demo_mode.sh

doctor:
	@$(PY) bin/doctor
