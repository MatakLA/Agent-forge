# make check        fast checks, no Docker, no model calls (also what CI runs)
# make test         check + integration tests of the supervisor, the API runtimes, the dashboard page and its accessibility (need Docker)
# make lint         shellcheck (warnings) + pyflakes (errors); skipped tools are reported, CI installs both
# make doctor       end-to-end health check of this install
PY := python3
PYSCRIPTS := bin/foreman bin/dispatch bin/run-supervisor bin/review-branch bin/killswitch bin/open-pr bin/merge-pr bin/forgejo-api \
             bin/doctor bin/status bin/report bin/egress-report bin/audit-rotate bin/wait-run bin/verify-token-refresh bin/upstream bin/secret-set \
             scripts/forge-bootstrap web/server.py worker/af-aider-events tests/fixtures/logging-proxy.py
SHSCRIPTS := bin/worker-add bin/worker-up bin/egress-up scripts/install-services scripts/install.sh scripts/update.sh scripts/uninstall.sh install.sh forge tests/test_forge_cli.sh tests/test_install_flow.sh tools/lint tools/sign-release scripts/vm-baseline-ubuntu.sh tests/test_ui.sh tests/test_a11y.sh tests/test_props.sh tools/fake-docker \
             egress/entrypoint.sh worker/entrypoint.sh worker/agy-run worker/af-agent tests/test_supervisor.sh tests/test_runtimes.sh tests/test_egress_security.sh worker/git-credential-forgejo

.PHONY: check test lint doctor
check:
	@bash tests/check.sh

test: check
	@tests/test_supervisor.sh
	@tests/test_runtimes.sh
	@tests/test_ui.sh
	@tests/test_a11y.sh
	@RUN_DOCKER_TESTS=1 tests/test_demo_mode.sh

lint:
	@tools/lint

doctor:
	@$(PY) bin/doctor
