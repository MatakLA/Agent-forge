# Changelog

## Unreleased
- Documentation: document six new selectable worker runtimes (copilot, mistral-vibe, ollama, vllm, lmstudio, llamacpp) with cost models and egress requirements in docs/RUNTIMES.md and docs/CHOOSING-AN-AI.md.
- Quickstart offline E2E check: verify examples/sample-project install, plan, dispatch, gate, and merge steps within wall-clock budgets using fake model API and fake Docker fixtures (tests/test_quickstart_e2e.sh, tests/test_quickstart_e2e.py).
- Issue intake parsing E2E test: verify bin/issues-intake creates one goal per eligible GitHub issue with role taken from label-to-role mapping against a fake GitHub API fixture (tests/test_issues_intake_parsing.sh, tests/fixtures/fake-forge-api-github.js).
- Runtime recommendation: rank ready runtimes cheapest-first in Settings > Onboarding via GET /api/readiness/recommend with honest fallback when none are ready.
- Control endpoint: POST /api/control triggers autopilot on/off and foreman pause/resume per project or default via bin/foreman.
- Readiness API and CLI: expose runtime and provider readiness status via GET /api/readiness and `bin/foreman readiness` subcommand.
- Runtime cost model: add honest per-runtime cost/quota classifier `cost_model()` to `bin/lib/runtimes.py` with explicit unknown classification for unpriced runtimes.
- Documentation: document honest cost/quota model per runtime in docs/RUNTIMES.md with explicit unknown classification for OpenCode, Qwen Code, Goose, and Anthropic-compat.
- Cross-link docs/CHOOSING-AN-AI.md with the dashboard Settings Onboarding checklist.
- Settings: surface file-only settings with human-readable explanations via `FILE_ONLY` in `bin/lib/foreman/settings.py` and `settings_get()`.
- Dashboard: Overview tab can now toggle autopilot and pause/resume the foreman without the CLI.
- Quickstart CI workflow: `.github/workflows/quickstart-e2e.yml` runs the quickstart e2e check on demand and on relevant path changes.
- Add docs/QUICKSTART.md: exact commands for git-clone-to-merged-PR in under 30 minutes on examples/sample-project.
- Settings UI: add-worker runtime picker entries and per-runtime model fields with readiness badges for OpenCode, Qwen Code and Goose in `web/static/js/settings.js`.
- Runtime settings: dashboard-editable model settings (`OPENCODE_MODEL`, `QWEN_MODEL`, `GOOSE_MODEL`) for OpenCode, Qwen Code and Goose runtimes in `bin/lib/foreman/settings.py`.
- Orchestrator providers documentation: `docs/PROVIDERS.md` now covers `openai-api` and `gemini-api` setup (base URL and model settings fields, key management via `bin/secret-set`), cost/quota honesty, schema and URL validation limitations, and offline testing.
- Escape hostile NUL bytes: a hostile NUL byte in test/gate output could crash the supervisor when it reached the next retry prompt; now it is escaped.
- OpenCode runtime: support SST's OpenCode CLI (`opencode`) with non-interactive JSON stream execution and configurable endpoint egress.
- Qwen Code runtime: support Alibaba Qwen Code CLI (`qwen-code`) with non-interactive stream-json execution and DashScope egress allowlist.
- Goose runtime: support Block's Goose CLI agent (`goose`) with non-interactive execution and configurable endpoint egress.
- Add examples/sample-project/: public example project pairing vision.md, project.conf, seed-repo/, and README.md
- Add docs/CHOOSING-AN-AI.md: honest comparison of all supported runtimes and providers
- Add docs/DEMO.md: Docker-only demo mode design and user guide
- One-command demo mode: `bash scripts/demo.sh` runs the dashboard in a read-only Docker container on http://localhost:8171 against synthetic data, requiring only Docker and no API keys or secrets.
- GitHub / GitLab issue intake: `bin/issues-intake` bridges open issues labelled `agent-forge` into foreman goals with seen-state deduplication and dry-run support.
- Install smoke test: Hermetic install smoke test verifying fresh layout startup, web dashboard reachability at /api/status, and static asset delivery without Docker infrastructure (`tests/test_install_smoke.sh`, `tests/test_install_smoke.py`).
- Foreman loop: `dispatch_ready` prevents double dispatch and file-overlap bypass across racing ticks via in-flight tracking and early state persistence.
- Foreman loop: `dispatch_ready` and `why_no_task` respect goals with state or status set to 'paused', never dispatching their tasks.
- Foreman loop: `orphaned` validates `RUN_DIR_RE`, checks recent start markers, and never flags runs of already-finished tasks as orphans.
- Foreman loop: `recover_orphan` clears task worker/run bindings to restore dispatchability and resets worker dispatch history in `LAST_DISPATCHED`.
- Foreman loop: orphan-loop detection tracks consecutive crashes with no progress, alerting at ORPHAN_WARN, blocking at ORPHAN_LIMIT via merge.block, and exempting startup first-pass orphans.
- Foreman loop: `feed_idle_workers` counts planning tasks toward pending capacity, preventing premature backlog goal pulls and idle spin.
- Foreman loop: orphan-loop detection blocks tasks after consecutive crash-and-orphan cycles with no progress via `ORPHAN_LIMIT` (default 5) and raises warnings/notifications via `ORPHAN_WARN` (default 3).
- Foreman merge: `auto_rebase` aborts rebase cleanly on conflict or failure, cleaning temporary files and leaving no `.git/rebase-merge` state behind.
- Foreman merge: `requeue_gate` blocks tasks exceeding the retry cap with a clear reason, and merge helpers guard against silently merging blocked or cancelled tasks.
- Foreman merge: `step_gate_review_merge` and merge steps clear in-memory `PHASE` tracking on completion or mid-step failure, preventing dangling phases without active jobs.
- Foreman merge: `step_upstream_merge` and gate steps recover tasks stuck in status 'merging' with no PR reference back to 'gate'.
- Foreman merge: audited gate output and review feedback path; verified safe from subprocess argv injection (PR bodies pass via files and API payloads), and noted ANSI control sequences bypass terminal stripping in dashboard feedback rendering.
- Foreman store: serialize read-modify-write via `state_tx()` locking and ensure crash-safe atomic updates via temporary file replacement in `jsave()`.
- Foreman store: prevent resurrection of deleted tasks and goals in `save_task()` and `save_goal()` by refusing to recreate files removed after being read.
- Foreman store: support reentrant `state_tx()` and `locked()` in the same thread without deadlocking on nested file locks.

**Secret store permission hardening.** `bin/secret-set` enforces mode 0600 on secrets files via `fchmod` and `chmod`, unlinks pre-existing temporary files with `O_NOFOLLOW` to prevent permission inheritance or symlink attacks, and confirms storage without echoing secret values.

**Git credential helper trace suppression.** `worker/git-credential-forgejo` disables shell tracing (`set +x`, `set +v`) to prevent tokens leaking to stderr or debug logs, tolerates verbose flags, and strictly reads tokens from secrets files rather than argv.

**Forgejo API URL query token protection.** `bin/forgejo-api` rejects requests containing tokens in URL query strings to avoid leaking secrets into access and egress proxy logs, strictly passing monitoring credentials via HTTP `Authorization` headers.

**Worker pool liveness against orphaned runs.** `worker_busy(w)` verifies supervisor process liveness rather than run-directory presence alone so dead runs never mark workers busy forever.

**Starvation-free dispatch rotation.** `order_by_quota` safely breaks ties using least-recently-used dispatch timestamps and handles null quotas to ensure fair round-robin dispatch across workers.

**Worker cooldown expiration and cache invalidation.** `is_cooling_down(w)` and `pool_quota_out()` prune expired cooldown entries and purge stale quota cache so recovered workers become dispatchable immediately.

**Worker-specific API budget checks.** `pool_quota_out()` enforces individual worker caps via `api_budget_left(w)` conjunctively with cooldown status, surfacing accurate exhaustion reasons without silently blocking the pool.

**Dashboard backend SSH command injection hardening.** Remote command generation in `web/dashboard/backend.py` validates all owner-supplied parameters against strict allowlists (`PROJECT_RE`, `REPO_RE`, `WORKER_RE`, `LANG_RE`, `VALID_RUNTIMES`) and safely escapes shell arguments with `shlex.quote` (`create_project_job`, `add_worker_job`, `connect_upstream_job`, `pflag`).


**PTY breakout protection.** `LoginSessions` validates worker names and strictly blocks terminal control sequences and newlines in `send(w, text)` to prevent breakout into secondary shell commands.

**Credentials over stdin.** Tokens, settings changes, and product vision markdown are verified to travel strictly over stdin (`inp=...`), never formatted into command line argv or exposed in `/proc/*/cmdline`.

**Open source.** Agent-Forge is now licensed under the Apache License 2.0 (was: Sustainable Use License, source-available).
Selling it, hosting it and building on it are allowed; the commercial license (`LICENSE_EE.md`) and the contributor license
agreement are gone, contributions are accepted under Apache-2.0 (`git commit -s`).

`NOTIFY_URL=outbox`: notifications are appended to `runs/notify-outbox.jsonl` for a relay outside the machine (when the
sandbox may not reach your notification server itself); the file can be deleted at any time.

`worker/entrypoint.sh`: Validate `FORGEJO_USER` and `FORGE_REPO` against strict allow-patterns before execution, quote all shell variable expansions, set umask 077, strip group/other permissions on workspace and `.git`, and drop root privileges to unprivileged `worker` user.
`egress/entrypoint.sh`: Validate `EGRESS_MODE` against allow-pattern `^(enforce|discovery)$`, set umask 027, verify enforce configuration parsing before daemon exec, and drop root privileges to unprivileged `proxy` user.
`web/server.py`: Enforce Host header validation across all request paths including `/healthz` and require numeric ports to prevent DNS rebinding.
`web/server.py`: Validate Origin and Referer headers against allowed hosts on state-changing POST requests as a CSRF guard.
`bin/lib/foreman/knowledge.py`: Reject directory traversal (`..`, `/`, `\`), absolute paths, and malformed names in `doc_path`, `doc_set`, `doc_reset`, and `role_playbook`.
`bin/lib/foreman/knowledge.py`: Enforce canonical `ROLE_SLUG` validation on role identifiers in `role_playbook` to prevent path traversal when loading role playbooks.
`bin/lib/foreman/knowledge.py`: Enforce realpath containment verification (`commonpath`) in `doc_path` and `doc_reset` as defense-in-depth against directory traversal.
`bin/lib/sbx.py`: Reject directory traversal (`..` and path separators) in `ks_path`, `project_conf`, `project_pool`, and `ledger_path`, and add safe path helpers for runs and secrets.
`bin/lib/sbx.py`: Reject malicious `TEST_CMD` and `GATE_IMAGE` values in configuration by failing safe with `ValueError` rather than falling back or executing verbatim.
`bin/dispatch`: Enforce preflight validation of worker identifiers, `DISPATCH_MODEL`, and `DISPATCH_ROLE` against safe regexes before subprocess execution.

**Security: Install scripts hardening.** `scripts/install.sh` and `scripts/vm-baseline-ubuntu.sh` enforce restrictive umasks (`umask 077` and `umask 027`) and file permissions (mode 0700 for `etc/` and secrets directory, mode 0600 for configuration and secret files) so no secrets or configs are created world- or group-readable; static regression tests verify safe remote execution and secret handling (`tests/test_install_security.sh`).

## 0.11.0 - 2026-10-08
**You stay in the loop when you want to.** `APPROVE=plan,risky,merge` per project: a goal's plan, each risky task and each
reviewed branch can wait for your OK. Everything that waits for you is in one place - Overview → Needs you - with one or two
buttons each (approve, send back with your words, retry, cancel). From the dashboard or `bin/foreman task|goal <id> …`: cancel
a task (a running one is stopped), retry, send its work back with your feedback, assign it to a worker or a model; pause,
resume, prioritize, edit, re-plan or cancel a goal.

**Your own model server.** Two new worker runtimes for any OpenAI-compatible server - Ollama, vLLM, LM Studio, OpenRouter:
`compat` (Codex CLI, Responses API) and `aider` (Aider 0.86, chat completions). A server on your network gets an egress
exception for exactly its address and port. The runtime test now proves for all five API runtimes that they contact nothing
but their API (it caught Aider downloading a price table - fixed).

**Budgets** per calendar month, per project (day / month) and per worker (day), next to the per-run and per-day caps; running
API runs count at their full cap.

**The planner learns from this project.** What merged tasks really cost by size and role goes into every plan; each open task
gets an estimate; `MODEL_ROUTING=auto` gives each role the cheapest worker model that has proven itself here; A/B results come
with 95 % intervals and a verdict.

**Dashboard.** Pushed live updates (Server-Sent Events; polling as a fallback). Why each idle worker is idle. A page per goal
(plan, tasks, events in order, cost, criteria, combined diff), a task sheet with its runs and costs, the replay of any finished
run, a project-wide dependency graph with the critical path, backlog drag-and-drop with a done-by forecast, search across goals,
tasks, events and transcripts. Insights: weekly trends, money and quota (spend per day, caps, when today's budget runs out,
quota burn). System: health checks with fix buttons, egress report with acknowledge, disk usage and cleanup. Settings in six
pages; the vision, team rules, decisions, lessons and role playbooks are edited in the browser (a role per project).

**Egress decisions in the dashboard.** System → Egress lists every blocked host (who tried it, how often, alarm or not) with
**Allow** (exact host names only; removable again) and **Ignore** (stays blocked, no alarm); a project stopped by an egress
alarm shows up in Needs you with "Acknowledge and continue". `bin/egress-report --allow / --unallow`.

**Code.** `bin/foreman` is now the package `bin/lib/foreman/` (17 modules) and the dashboard is `web/server.py` +
`web/dashboard/` + static files (`web/static/`, no inline scripts, a stricter CSP). `bin/doctor --json`; `tests/test_ui.sh`
tests the page in a real DOM.

## 0.10.0 - 2026-10-08
**Workers with API keys.** Besides Antigravity subscriptions, a worker can now run Claude Code, Codex or Gemini CLI with an API
key (`bin/worker-add N --runtime claude|codex|gemini`, or the dashboard). Keys stay in the secrets directory and reach only the
agent's environment; each runtime may reach only its provider's API host (the CLIs' update checks, telemetry and discovery
calls are switched off - measured with a logging proxy). Hard caps: USD per run (Claude Code's own `--max-budget-usd`), USD per
day for all projects, tool calls per run. API workers are overflow by default. A rejected key re-queues the task and pauses the
worker. Event formats, resume and cost were verified with the real CLIs against a fake API (`tests/test_runtimes.sh`).

**GitHub and GitLab.** A project can live on GitHub or GitLab (cloud or self-hosted): approved branches are pushed there and
merged through pull / merge requests under the repository's own protection, upstream main is synced back every minute. Workers
never reach GitHub/GitLab or the token. The dashboard asks where a new project's code lives. `bin/upstream`, docs/UPSTREAM.md,
`tests/test_upstream.py`.

**Install in one command.** `scripts/install.sh [--demo]`, `deploy/cloud-init.yaml`, a demo project in `examples/demo`.

**License.** Source-available under the Sustainable Use License: free to use and change for yourself and inside your company;
selling it or offering it as a paid / hosted service needs a commercial license (`LICENSE`, `LICENSE_EE.md`, `CONTRIBUTING.md`
with a contributor license agreement).

**Dashboard.** English only (one language, no client-side translation layer); deep links (`#tab=queue&project=p&task=0004-x`);
the Live tab shows only running runs and those finished in the last minutes; API workers show their runtime and spend.

**Proof for newcomers.** Screenshots and a tour GIF from invented demo data (`tools/screenshots.sh`, `tools/demo_state.py`),
a CI workflow that installs everything on a fresh VM and runs the integration tests (`.github/workflows/install.yml`),
`ROADMAP.md`, `tools/publish-snapshot` (publishes the tree as one commit without private history).

**Security.** The gate refuses branches that add API keys, tokens or private keys (was: a warning for some patterns).
`bin/worker-add` no longer passes the Forgejo admin token on curl's command line.

## 0.9.0 - 2026-10-07
First versioned release. Highlights since the extraction from a private setup:

**Efficiency** (data from ~400 runs: token-cap runs used 38 % of the quota) - context pack per task from a deterministic code
map, self-check with fixes in the same agent conversation, push after every run, pre-split of tasks owning many files, model
routing and A/B tests with quota-per-merged-task metrics, planning ahead and LLM-free maintenance while quota is out.

**Owner controls** - goals, backlog order and answers to the planner's questions from the dashboard (answers are kept in
`decisions.md` and bind later plans); per-project kill switch and `--all`; settings, workers (with browser login) and new
projects from the dashboard; notifications via ntfy / Slack / Discord / webhook; `bin/doctor`.

**Any language** - `TEST_CMD` and `GATE_IMAGE` per project (also editable in the dashboard's Settings → Project).

**Security** - dashboard Host check (DNS rebinding), CSRF header, no framing, CSP, optional `DASHBOARD_TOKEN`; Forgejo tokens
no longer on git command lines; nothing is pushed from a run with a HIGH egress alarm.

**Fixes**
- Dashboard froze on stale data after ~14 h: tail processes leaked pipes until the open-file limit; finished runs' panes were
  re-created every ~95 s.
- Parallel gate steps shared one rebase clone: interleaved rebases could push one branch's result to another branch.
- Freshly opened PRs looked conflicted (Forgejo's mergeability check still running) and were sent back to the worker.
- The kill switch re-tripped after "Continue": stops by the kill switch, token caps and quota stops counted as failures.
- A run whose supervisor died (reboot, OOM) left its task "running" forever; after a reboot a reused pid could make a worker look
  busy forever.
- A fresh checkout crashed when planning (missing `tasks/`); malformed planner JSON made the loop re-plan (and re-pay) every
  tick; two goals created in the same second overwrote each other; concurrent backlog/state writes lost updates.
- Claude Code failures that were not usage limits were retried every tick; LLM call kinds were mislabelled under parallel calls.
- Maintenance reported "main is red" for a project without tests or when Docker failed.
- Timeline mixed the runs of all projects; the remote dashboard assumed the user `dev`.
- A worker out of weekly quota logged the same event every 3 minutes for days and re-read its quota each time (now once per
  change, re-checked every 30 min when the reset is far; ISO reset times were never parsed).
