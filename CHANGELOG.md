# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Agent-Forge is
alpha software: until 1.0, commands, settings and file formats can change between releases.

## Unreleased

**Accessibility** (fixes #5). Keyboard and screen-reader pass over the dashboard: skip link, landmarks and accessible names, dialogs
with focus trap / `Esc` / focus return / inert background, `aria-current` and `aria-pressed` on tabs and toggles, live regions for
errors and status; WCAG 2.1 AA contrast in light and dark (new colour tokens), visible focus ring, underlined links in text,
`prefers-reduced-motion` and `forced-colors` support, reflow at 320 px. New `tests/test_a11y.sh` runs axe-core and the keyboard
checks in headless Chrome (27 views x 2 themes, 0 violations) and is part of the `install` workflow.

**Documentation.** `docs/FIELD-REPORT.md` and `docs/field-report-data.json`: a field report from one self-hosted install (221 merged tasks in five days on this repository, a randomised context-pack comparison on two projects, a before/after for the 0.9.0 efficiency features), with the bad numbers, the incidents and what is still unmeasured.

**Supply chain.** The demo image is built with an SPDX SBOM and signed build provenance (`gh attestation verify oci://ghcr.io/matakla/agent-forge-demo:X.Y.Z --owner MatakLA`), its base image is pinned by digest, and the SBOM is attached to each GitHub release; the `release` workflow can be run by hand as a rehearsal that publishes nothing. New `scorecard` workflow (OpenSSF Scorecard, weekly).

**CI.** The fresh-install proof now runs on Ubuntu 22.04 and 24.04. New `portability` workflow runs the pure-Python tests that pass on macOS and Windows from `tests/portable-python.txt` (`tests/run_portable.py`); the real install still needs Linux.

**Repository.** CodeQL code scanning (`.github/workflows/codeql.yml`: Python, JavaScript, workflows); every GitHub Action is
pinned to a commit SHA (Dependabot keeps them current); `CITATION.cff`; the security policy links the private advisory form;
a social preview image (`docs/img/social-preview.png`); removed `tools/publish-snapshot`.

## 0.12.0 - 2026-10-21

### Added

- **`forge` command.** One entry point: `forge install`, `demo`, `update`, `uninstall`, `doctor`, `logs`, `url`, `version`. Every
  other command (`status`, `goal`, `pause`, `backlog`, `answer`, ...) is handed to `bin/foreman`.
- **One-line installer.** `curl -fsSL .../install.sh | bash` clones the repository and runs `scripts/install.sh` (flags such as
  `--demo` and `--check` pass through; `FORGE_DIR`, `FORGE_REF` and `FORGE_REPO_URL` choose where, which ref and which source).
  The installer can be repeated, installs Docker only if needed and installs the `forge` shortcut into `~/.local/bin`.
- **`forge update [--check]` and `forge uninstall [--purge] [--yes] [--check]`.** Update fetches the new version, rebuilds what
  changed and restarts the services. Uninstall keeps your data unless `--purge` is given (it asks for confirmation).
- **Docker demo image** `ghcr.io/matakla/agent-forge-demo` (multi-arch, published on version tags): the real dashboard on invented
  data with one `docker run`. `forge demo [up|down|status]` runs it from a checkout (`scripts/demo.sh status` is read-only).
- **Community files:** Code of Conduct, SUPPORT, CONTRIBUTING, SECURITY, issue forms and GitHub Discussions.
- **`make lint` / `tools/lint`:** shellcheck at warning level and pyflakes for undefined names, over every tracked script.
  `.dockerignore`, `.editorconfig`, `.gitattributes`, CODEOWNERS and Dependabot for the Actions and base images.
- **CI and release workflows:** checks and an install test on every change; a release workflow that builds and publishes the demo
  image on version tags.
- **Documentation:** `docs/INSTALL.md`, `FAQ.md`, `FEATURES.md`, `DASHBOARD.md`, `TROUBLESHOOTING.md`, a docs index, a logo, and
  `QUICKSTART.md` (exact commands from `git clone` to a merged pull request on `examples/sample-project`),
  `CHOOSING-AN-AI.md` (honest comparison of runtimes and providers), `DEMO.md` and the `openai-api` / `gemini-api` sections of
  `PROVIDERS.md` (base URL and model settings, key management with `bin/secret-set`, cost and quota honesty, limitations, offline
  tests). `docs/RUNTIMES.md` and `CHOOSING-AN-AI.md` document setup, `bin/doctor` checks, Settings readiness, honest cost and quota
  models and local egress for every runtime, with an explicit "unknown" cost for OpenCode, Qwen Code, Goose and Anthropic-compat.
- **Example project:** `examples/sample-project/` pairs `vision.md`, `project.conf`, `seed-repo/` and a README.
- **Fewer orientation steps per run.** Every definition in the code map carries its line span (`[L12-40]`). A "Who uses what you
  change" block lists `path:line` of callers outside the task's files and the names nothing uses (one `git grep` on main, no LLM),
  and a rework gets its branch's diff against main. `ORIENT=on` puts the worker's clone on the task branch and writes a "Start
  state" block (behind/ahead of main, the branch's commits) into the prompt; the rules also ask for batched commands.
  `PACK_EXTRAS` and `ORIENT` (default on) switch the old behaviour back for A/B runs; `PROMPT_ORDER=cache` puts a
  task-independent prefix first. The planner's code map is unchanged. Measured on the reference install: about a quarter of the
  shell calls were `grep`/`find`/`ls`/`git status|log|diff|branch|fetch`, and 92 % of reads already used line ranges.
- **Quality gate.** `review-branch` prints `== QUALITY:` lines: tests added versus code added (limit `max(150, TEST_RATIO_MAX x code
  lines)`, default 1.5) and new source files that no other non-test file refers to. `QUALITY_CHECK=off|warn|block` (default
  warn); with `block` a quality-only failure goes back to the worker as rework. After each goal the planner is shown the unwired
  files and must add wiring tasks or delete them. Worker, planner, reviewer and tester prompts state the standard: least code that
  is correct, reuse, wire it in, behaviour tests instead of input enumeration.
- **Scripted checks instead of review round-trips.** `bin/lib/quality.py` feeds the worker's self-check, the gate and the foreman
  with what reviewers used to send back: files outside the task's list, weakened tests, copied definitions, the test/code ratio,
  unused imports (as hints). The gate prints a `== FLAGS:` line; rework history records the kind and flags, and the team metrics
  count them. A fast pass (changed tests and their importers, `--maxfail=5`) runs first in the self-check and the gate, and a red
  fast pass skips the full suite.
- **Four independent languages** (Settings → Project): yours (goals, questions, notes: `OWNER_LANG`), the team's (task specs,
  reviews, orchestrator and workers: `TASK_LANG`, default English), the product's own user-facing texts (`PRODUCT_LANG`, empty =
  yours) and code comments and commit messages (`DOCS_LANG`). The orchestrator, planner, reviewer and worker prompts state them.
- **Settings rebuilt as eight short pages** (Workers, Runtimes, Models, Project, Money, Knowledge, Limits, Advanced): every setting
  is on exactly one page with a plain-language label, unsaved edits go into one Save bar (with Discard, a warning before leaving and
  a refused value marked at its field). File-only settings are explained (`FILE_ONLY` in `bin/lib/foreman/settings.py`).
- **Workers and keys from the dashboard.** Remove a worker (`bin/worker-remove`; refuses while a run is in progress or when it is a
  project's only worker; deletes the container, volume, Forgejo bot user and secrets), add a role from the Knowledge page, and set
  API keys and the notification token from the page (write-only). The add-worker picker lists exactly the runtimes the server says
  it can create (`creatable`); the readiness table and the onboarding card are part of Runtimes.
- **Overview switches** for autopilot and pause/resume, backed by `POST /api/control` (`bin/foreman` per project or default).
- **Readiness.** `GET /api/readiness` and `bin/foreman readiness` show runtime and provider readiness.
  `GET /api/readiness/recommend` ranks ready runtimes cheapest-first (with an honest fallback when none is ready) in Settings →
  Runtimes. `cost_model()` in `bin/lib/runtimes.py` classifies each runtime's cost and quota, with "unknown" for unpriced ones.
- **More worker runtimes:** OpenCode (`opencode`), Qwen Code (`qwen-code`; DashScope egress) and Goose (`goose`) with
  non-interactive stream execution and configurable endpoint egress; dashboard-editable `OPENCODE_MODEL`, `QWEN_MODEL` and
  `GOOSE_MODEL`, with readiness badges in the add-worker picker. Local-compat runtimes `ollama`, `vllm`, `lmstudio` and `llamacpp`
  (local endpoint resolution, zero-cost classification, OpenAI-compatible completions command and scan handling, tests), and
  `copilot` and `mistral-vibe`, are registered. `bin/worker-add` creates only `agy`, `claude`, `codex`, `gemini`, `compat` and
  `aider`, because no worker image ships the other CLIs yet.
- **Issue intake.** `bin/issues-intake` turns open GitHub / GitLab issues labelled `agent-forge` into foreman goals (seen-state
  deduplication, dry-run support).
- **Demo mode.** `bash scripts/demo.sh` runs the dashboard in a read-only Docker container on <http://localhost:8171> against
  synthetic data; it needs only Docker, no API keys and no secrets.
- **`NOTIFY_URL=outbox`:** notifications are appended to `runs/notify-outbox.jsonl` for a relay outside the machine, for when the
  sandbox cannot reach your notification server itself. The file can be deleted at any time.
- **Dashboard downloads:** the regular files directly in `DASHBOARD_DOWNLOADS_DIR` are served at `/downloads/<name>` after
  sign-in (replaces a fixed single-file route).
- **Tests:** an offline quickstart end-to-end check against a fake model API and fake Docker (`tests/test_quickstart_e2e.sh`, run
  by `.github/workflows/quickstart-e2e.yml` on relevant changes), an issue-intake parsing test against a fake GitHub API, an install
  smoke test (fresh layout, dashboard reachable at `/api/status`, static assets, no Docker; `tests/test_install_smoke.sh`), static
  install-security tests (`tests/test_install_security.sh`), and tests for the `forge` CLI and the install flow.

### Changed

- **License: Apache-2.0** (was the source-available Sustainable Use License). Selling, hosting and building on Agent-Forge are
  allowed; contributions are accepted under Apache-2.0 (`git commit -s`).
- **README, QUICKSTART and SETUP rewritten.** The README now leads with the three ways to try it; `docs/SETUP.md` is the manual,
  step-by-step installation.
- **Finite goals.** The planner refuses open-ended goals ("more", "improve", "continue") and asks for a finite end state; the answer
  replaces the goal. The autopilot proposes only finite goals; when nothing is left in the vision it switches itself off and sends
  a `project_done` notification.
- **Merging survives a busy Forgejo.** `bin/merge-pr` waits up to a minute for "Please try again later" (405), 5xx or a refused
  connection and exits 75. The foreman then retries the merge with growing pauses (up to 12 tries) instead of blocking the task,
  and spends no extra review round on it. Real conflicts (409) behave as before.
- **Slow suites are remembered.** A self-check that finds the full suite too slow remembers it for 7 days and runs only the changed
  test files meanwhile, instead of waiting out the timeout on every run.
- **Switching the dashboard's UI language no longer changes `OWNER_LANG`.**
- **System → Health, Disk and Egress open at once:** the server answers from a cache and refreshes it in the background
  (`?fresh=1` measures again), `docker system df` / `docker ps -s` are replaced by cheap queries, and the egress proxy log is
  rotated hourly instead of being re-read at 100+ MB.
- The code-map cache is rebuilt once (`codemap.VERSION`); the usage block groups callers per file, tests last.
- `bin/doctor` names the full worker-image build command when the image is missing.
- The benchmark (`bench/run_bench.py`) reaches the Agent-Forge machine through `BENCH_SSH` instead of a fixed address.
- **Issue forms are for humans now.** `.github/ISSUE_TEMPLATE/` holds a bug report and a feature request (blank issues off,
  security reports go to a private advisory). The four agent-task forms, which label an issue `agent-forge` and so let anyone queue
  work for a team that runs `bin/issues-intake`, live on as examples in `examples/issue-templates/` with a warning in
  `docs/ISSUE-TEMPLATES.md`.
- Workflows: `ci` lints, runs `make check` on Python 3.10 and 3.12 and builds the demo image with a no-private-files check;
  `install` also runs for changes to the installer and checks `forge uninstall` and `--purge`; every workflow has read-only
  permissions and cancels superseded runs.

### Fixed

- The six settings added with the quality gate (`QUALITY_CHECK`, `TEST_RATIO_MAX`, `MAX_FILE_LINES`, `ORIENT`, `PACK_EXTRAS`,
  `PROMPT_ORDER`) had no row in the dashboard; they are on the Advanced page now, with a label and a help text each.
- The dashboard looked for the foreman in `~/sandbox` even on a local install, so a fresh checkout (and the demo image) showed an
  error instead of its data. Local mode now uses the checkout it runs from; `FORGE_DIR` still overrides.
- The demo image started on `127.0.0.1` inside the container, so the published port answered nothing. It binds `0.0.0.0` now (the
  CI job checks that the demo serves data, not only that it starts).
- `tests/check.sh` ran six tests through `| tail -1`, which hides a failing exit code, and 20 test files were not run at all. Every
  test is gated now, a test file that `check.sh` does not mention fails the run, and the run fails when a test changes the
  checkout's kill switch or `etc/*.conf`. This turned up and fixed: a demo-mode test that tripped the real kill switch and wrote
  `AUTOPILOT=1` into `etc/foreman.conf`, an egress test that tried to clone from the internal Forgejo, and stale expectations in
  the dashboard, doctor and settings tests.
- When GitHub or GitLab refused or could not be reached for a task's push, the follow-up step failed with a `NameError` (the error
  variable was used after its `except` block) instead of sending the task back to the gate with the reason. Found by the new
  lint; a regression test covers it.
- A task whose branch is identical to main (its change already landed, for example after the auto-rebase) is finished as "nothing
  to merge" instead of looping on Forgejo's 405 for an empty pull request.
- `salvage()` chained the commit to the exit code of `git add`, which is 1 whenever `.gitignore` lists `.pytest_cache`, so
  uncommitted work was never saved. It now always commits.
- A hostile NUL byte in test or gate output could crash the supervisor when it reached the next retry prompt; it is escaped.
- Foreman loop:
  - `dispatch_ready` prevents double dispatch and file-overlap bypass across racing ticks (in-flight tracking, early state
    persistence), and neither it nor `why_no_task` dispatches tasks of paused goals.
  - `orphaned` validates `RUN_DIR_RE`, checks recent start markers and never flags runs of finished tasks;
    `recover_orphan` clears worker and run bindings and resets `LAST_DISPATCHED`.
  - Orphan loops are detected: consecutive crash-and-orphan cycles with no progress warn at `ORPHAN_WARN` (default 3) and block
    the task at `ORPHAN_LIMIT` (default 5); first-pass orphans at startup are exempt.
  - `feed_idle_workers` counts planning tasks as pending capacity, so backlog goals are not pulled too early and idle spin stops.
- Foreman merge:
  - `auto_rebase` aborts cleanly on conflict or failure and leaves no `.git/rebase-merge` state.
  - `requeue_gate` blocks tasks over the retry cap with a clear reason; merge helpers never merge blocked or cancelled tasks.
  - The merge steps clear in-memory `PHASE` tracking on completion or failure; tasks stuck in `merging` without a PR reference go
    back to `gate`.
- Foreman store: read-modify-write is serialized with `state_tx()` locking and atomic via a temporary file in `jsave()`;
  `save_task()` and `save_goal()` no longer resurrect deleted tasks and goals; `state_tx()` and `locked()` are re-entrant in one
  thread.
- Worker pool: `worker_busy(w)` checks that the supervisor process is alive, so dead runs no longer mark a worker busy forever;
  `order_by_quota` breaks ties by least-recently-dispatched and handles null quotas (no starvation); `is_cooling_down(w)` and
  `pool_quota_out()` prune expired cooldowns and stale quota caches; `pool_quota_out()` also applies each worker's own cap via
  `api_budget_left(w)`, so exhaustion reasons are accurate.

### Security

- **Secrets:** `bin/secret-set` forces mode 0600 (`fchmod`, `chmod`), unlinks pre-existing temporary files with `O_NOFOLLOW` and
  confirms storage without echoing the value. Tokens, settings changes and the vision travel over stdin only, never in argv or
  `/proc/*/cmdline`. `worker/git-credential-forgejo` disables shell tracing and reads tokens from the secrets files only.
  `bin/forgejo-api` rejects tokens in URL query strings (they would leak into access and egress logs).
- **Dashboard server:** Host header validation on every path including `/healthz`, with numeric ports required (DNS rebinding);
  Origin and Referer checks on state-changing POSTs (CSRF). Remote command generation in `web/dashboard/backend.py` validates every
  owner-supplied parameter against strict allow-lists (`PROJECT_RE`, `REPO_RE`, `WORKER_RE`, `LANG_RE`, `VALID_RUNTIMES`) and quotes
  with `shlex.quote`. `LoginSessions.send` validates worker names and blocks terminal control sequences and newlines (PTY breakout).
- **Entrypoints:** `worker/entrypoint.sh` validates `FORGEJO_USER` and `FORGE_REPO` against strict patterns, quotes every
  expansion, sets umask 077, strips group/other permissions on the workspace and `.git`, and drops root to the `worker` user.
  `egress/entrypoint.sh` validates `EGRESS_MODE` (`enforce|discovery`), sets umask 027, checks that the enforce configuration
  parses before starting the daemon, and drops root to `proxy`.
- **Paths and input:** `bin/lib/foreman/knowledge.py` rejects `..`, separators and absolute paths in `doc_path`, `doc_set`,
  `doc_reset` and `role_playbook`, enforces the role slug format and checks `realpath` containment. `bin/lib/sbx.py` rejects
  traversal in `ks_path`, `project_conf`, `project_pool` and `ledger_path`, and fails safe with `ValueError` on malicious `TEST_CMD`
  or `GATE_IMAGE` values. `bin/dispatch` validates worker names, `DISPATCH_MODEL` and `DISPATCH_ROLE` before running anything.
- **Install scripts:** `scripts/install.sh` and `scripts/vm-baseline-ubuntu.sh` use restrictive umasks (077 and 027); `etc/` and the
  secrets directory are 0700, configs and secrets 0600. Static tests check safe remote execution and secret handling.
- **Audit note:** the gate output and review feedback path was checked and is safe from argv injection (pull request bodies go
  through files and API payloads). ANSI control sequences in feedback are not stripped before the dashboard renders them.

### Removed

- The root `docker-compose.yml`, `Dockerfile` and `.env.example` ("unified stack"). The installer replaces them for a real
  install and the demo image for a look around.
- The commercial license (`LICENSE_EE.md`) and the contributor license agreement.
- The fixed single-file download route of the dashboard (see `DASHBOARD_DOWNLOADS_DIR` above).
- `docs/PENDING-QUOTA.md`, an internal work list.

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
`ROADMAP.md`.

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
