# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Agent-Forge is
alpha software: until 1.0, commands, settings and file formats can change between releases.

## 0.12.2 - 2026-10-21

**Supply chain.** Every release now carries Sigstore signatures (`FILE.sigstore.json`, keyless) for the source archive, the SBOM and
`SHA256SUMS`, plus signed build provenance of those files; how to check them is in SECURITY.md ("What you download"). The 0.12.1
files are signed afterwards by the new `sign-release` workflow. `tools/sign-release` does the signing and checks each signature
again before anything is published; the `release` workflow's rehearsal runs it on a throwaway archive. All installs are pinned and
hash-checked: the agent CLIs in the API worker image come from a lockfile (`npm ci`), the test tools from lockfiles, the CI tools
from hash-pinned requirements; Dependabot watches all of them and waits seven days after a new release before it proposes it. The
workflows' checkouts keep no git token (`persist-credentials: false`).

**Logging.** The dashboard writes an access log to the journal (`forge logs web`): every write and every refused or failed request, with
method, path, status and a few plain fields (`action=allow host=pypi.org`); never query strings, cookies, bodies, typed text or
secret values. `DASHBOARD_ACCESS_LOG=all` adds reads, `=off` silences it. Owner actions that left no trace now do: egress decisions
(`--baseline-add`, `--allow`, `--unallow`), storing a secret (name only), importing from upstream, adding and removing a worker,
creating a project all write to the audit trail (`runs/ALERT`, `runs/ledger.jsonl`).

**Security.** When a worker asked for `CONNECT 203.0.113.7:443`, the egress proxy looked up the name that address claims to have (its reverse DNS
record) and let the connection through when that name was on the allowlist. The record is chosen by whoever owns the address, so any server could
be given the name of an allowed host and then be reached from a worker. The domain rules now compare the name in the request only (`dstdomain -n`):
an IP address is refused unless it is listed itself, names behave as before. `forge update` recreates the proxy; a hand-made setup needs
`bin/egress-up`. A new test fails when a domain rule lacks the option.

**Fixed: the merge gate and the foreman.**

- The merge gate (`bin/review-branch`) treated every crash as a pass: an uncaught exception ends a Python script with exit code 1, which
  the foreman reads as "warnings, merge anyway". A deleted or syntactically broken test file, a missing Docker, a diff with bytes that are
  not UTF-8, or a test run that hangs could therefore end in a merge that no test had judged. It now exits with 3 ("the gate itself
  broke") and the foreman queues the task again (four times at most, then it waits for the owner). A hanging suite is cut off by the
  gate's own `GATE_TIMEOUT` (default 1500 s) and goes back to the worker with that reason, because the foreman now waits longer than the
  gate does (it used to wait exactly as long, so it cut the gate off from outside and read that as a pass).
- `git` output that is not UTF-8 (file names, Latin-1 text) no longer stalls a task in a loop, and the automatic rebase of shared files
  (`SHARED_FILES`) merges line by line on the raw bytes: a binary file is a conflict for the worker instead of being replaced by an
  empty one.
- A mutation check that returned no result is stated in the pull request ("not verified") and shown as a warning. A mutant it could not
  set up is reported as `ERROR` instead of stopping the check, and a check that ended early is a warning that says how far it got
  ("stopped after 3 of 7 mutants"); it used to read as a pass for the mutants it never ran.
- A conversation id taken from a worker's last run that `bin/dispatch` refuses (characters outside the allowed set) used to send its
  task back to the queue and put every free worker on a 30-minute cooldown, again and again; the task now starts a new conversation.
- A review whose `mutations` was `null`, a number or a list with entries that are not text raised in the foreman's review step, which is
  retried every tick and paid for the same review again; such entries are skipped.

**Fixed: input a worker can influence.**

- A worker could stop all dispatching for 24 hours with one request: the egress proxy logs some request bytes raw, and a byte that is not
  UTF-8 made the host's reading of that log raise in the preflight of `bin/dispatch` (and in `bin/status`, `bin/doctor` and the dashboard)
  until the line was a day old. Such bytes are now replaced when the log is read.
- The same goes for the other places where the host reads text that a worker or its repository can influence: the run supervisor (the
  docker calls and the agent's stderr), `bin/status`, `bin/doctor`, the dashboard, the token total of a run, `git` output and the output
  of the mutation check's container no longer crash on a byte that is not UTF-8, so a run still ends with its exit code and its result.
- The agent's event stream (`stream.jsonl`) is read defensively everywhere: a line of any JSON shape is skipped instead of crashing the
  supervisor, which left the run orphaned, and `NaN`, `Infinity` or a number beyond the float range (`1e999`) count as 0 tokens; one such
  figure used to put `Infinity` in the ledger and stop all dispatching ("daily token budget exhausted") until the next UTC day.
- The check a worker runs before it finishes crashed on a test file that is not UTF-8 (or on a suite that prints such bytes), and since
  its own crashes count as a pass it silently did not run; it now reads them with replacement characters and still scans them for skips.

**Fixed: the dashboard.**

- The duration could read "-1 s" (or garbage) for a tiny negative or non-numeric value; found by the new property tests.
- The server dropped the request, without an answer, when a backlog position, question number, `days` or `since` held a character that
  `str.isdigit()` accepts and `int()` refuses (`²`, or 5000 digits); it now answers 400. A negative `Content-Length` no longer makes a
  write wait for the client to hang up, a request body that is not a JSON object counts as empty, and an infinite terminal size (`1e999`)
  or a `workers` field that is not a list no longer drops the request either. A worker name read from a run directory is only used in a
  shell command when it matches the `<timestamp>-worker-N` form that `bin/dispatch` creates.
- The search page marked its hits inside the already escaped text, so a query like `lt` broke the `&lt;` of a result and a query with
  `&`, `<`, `>`, `"` or `'` was never highlighted; each hit is now found in the plain text and every piece is escaped on its own.
- A dictionary is text: the button labels of the "Needs you" cards and the task sheet, the stat boxes, the status word of a goal and the
  Appearance card were put into the page as HTML, so a dictionary uploaded in Settings could carry markup (and the `<name>` in the
  Appearance help was swallowed as a tag). They are escaped now, and a new UI test fills every key with an element and fails when one
  shows up on any tab, sub-view, sheet or card.
- The pull request links on the goal and task sheets only appear for an `http(s)` address (escaping keeps a value inside its attribute
  but lets `javascript:` through); anything else shows as plain `#7`.
- A link to the dashboard that is cut off in the middle of a `%`-escape (`#tab=tasks&task=%`, which chat apps do to long links) left a
  blank page: the address was decoded without a guard and the first script stopped. The broken part is now read as plain text and the
  rest of the link still works.
- The "copy report" button of a blocked task ended in the manual-copy prompt even after the report had been copied: the confirmation
  called the task as if it were the translation function. It now shows the toast.
- The System → Egress tab stopped drawing, with its Allow, Ignore and acknowledge buttons, for 24 hours when a worker had asked the proxy
  for a host named like a member of every JavaScript object (`constructor`, `toString`, `__proto__`): the table of who tried which host
  was a plain object, so that name found the object's own method instead of an empty slot. It has no prototype now.
- Enter or Space on a timeline bar or a graph node showed a red "Page error: e.target.click is not a function" banner (the item still
  opened), and on "All checks" in System → Health did nothing at all: two handlers each clicked the element, so it opened and shut again.
  Cards, rows and search results also loaded their sheet two to four times per key press or click. One handler per kind is left, and a
  link or button inside a clickable row keeps its own Enter and its own click (the pull-request link in a goal's task table no longer
  switches the sheet to the task as well).
- A refusal from the kill switch that comes without a list of steps (an unknown project, demo mode) was shown as a bare "hide" button.
  It now says what was refused.
- Two quick clicks on "Add the worker" started two jobs and so added two workers (the button was only switched off when the page redrew).
  A second click is ignored while the first is still working. The server also refuses to add or remove a worker while another worker
  job is running (a second tab, a double submit): both pick or touch worker numbers and containers.
- When the dashboard could not read the container list (the ssh link to the devbox was down, or Docker answered nothing) it picked
  `worker-1` for the new worker, and `bin/worker-add 1` on a worker that exists rewrites its runtime and recreates its container. It now
  stops with "could not list the containers" and adds nothing.
- Another tab or the phone could keep showing the old health, egress, disk or settings for up to two minutes after something was changed:
  the cache was emptied before the write ran, so a read that happened while the write was still going was cached again. It is emptied
  after the write, and an answer that was being computed when the cache was emptied is no longer stored.

**Fixed: commands, docs and CI.**

- `bin/status` no longer crashes on a fresh install (it called `bin/guard-log`, which is not part of this repository).
- `forge pick-ai` and the runtime docs told you to run `bin/worker-add N --runtime ollama` (and `vllm`, `lmstudio`, `llamacpp`,
  `opencode`, `goose`, `qwen-code`, `anthropic-compat`, `copilot`, `mistral-vibe`), which `bin/worker-add` refuses because no worker
  image ships those CLIs. The next steps and the docs now say so and point to `compat` or `aider` for a model server of your own; they
  also show a LAN address for `COMPAT_BASE_URL`, since `127.0.0.1` inside a worker container is the container itself. A new test fails
  when any file names such a runtime after `--runtime`.
- `bin/doctor` warned on every install that the Goose CLI was not found on the host, so a healthy setup exited with 1. The host never runs
  Goose (it would run in the worker container), so that check is gone; the runtime's readiness is still checked under Readiness.
- The uninstall step of the `install` workflow now really fails when a service or container is left behind (a `!` in front of a command
  never stopped a `bash -e` step). `sign-release` copes with a release that has no files, such as v0.12.0.
- `forge update` said "nothing is half-way: run forge update again" when it failed after the checkout had moved (a failed image build,
  no sudo), and the next run answered "already up to date" without rebuilding or restarting anything. It now says what happened, and
  the next `forge update` finishes the steps that did not run (it keeps the starting point in `.git/forge-update-from` until they all have).

**Tests.** `tests/props.js` (fast-check, test-only dependency, part of `make check` when `node` is present and run in CI): generated inputs
against the page's helpers (`esc`, `hilite`, `ext`, `dur`, `ago`, `fmtTok`, `usd`, `tsloc`, `parseHash`). New tests for the access log and the audit entries,
and for the gate's verdicts: `tests/test_review_branch_failclosed.py` runs the real gate against a fake Docker, the foreman's side is in
`tests/test_foreman_merge.py`, and text that is not UTF-8 is covered there, in `tests/test_quality.py` and, for the worker's own check (run without Docker), in `tests/test_self_check.py`.
`tests/test_hostile_bytes.py` feeds bytes that are not UTF-8, and event lines of every JSON shape (with `NaN`, `Infinity` and `1e999`), to the
real run supervisor (against a fake Docker), `bin/status`, the stream scanner of every runtime, the token total and the mutation check's script,
and checks that every text-mode `subprocess` call in `bin/` and `web/` says what happens to such a byte.

## 0.12.1 - 2026-10-21

**Fixed.** The dashboard could stop with `renderLive is not defined` when the language file arrived before the last scripts had loaded (the first redraw is now started after all scripts are in). The empty action columns of the egress and run tables have a hidden header text for screen readers.

**Accessibility** (fixes #5). Keyboard and screen-reader pass over the dashboard: skip link, landmarks and accessible names, dialogs
with focus trap / `Esc` / focus return / inert background, `aria-current` and `aria-pressed` on tabs and toggles, live regions for
errors and status; WCAG 2.1 AA contrast in light and dark (new colour tokens), visible focus ring, underlined links in text,
`prefers-reduced-motion` and `forced-colors` support, reflow at 320 px. New `tests/test_a11y.sh` runs axe-core and the keyboard
checks in headless Chrome (27 views x 2 themes, 0 violations) and is part of the `install` workflow.

**Documentation.** `docs/FIELD-REPORT.md` and `docs/field-report-data.json`: a field report from one self-hosted install (221 merged tasks in five days on this repository, a randomised context-pack comparison on two projects, a before/after for the 0.9.0 efficiency features), with the bad numbers, the incidents and what is still unmeasured.

**Supply chain.** The demo image is built with an SPDX SBOM and signed build provenance (`gh attestation verify oci://ghcr.io/matakla/agent-forge-demo:X.Y.Z --owner MatakLA`), its base image is pinned by digest, and the SBOM is attached to each GitHub release; the `release` workflow can be run by hand as a rehearsal that publishes nothing. New `scorecard` workflow (OpenSSF Scorecard, weekly).

**CI.** The fresh-install proof now runs on Ubuntu 22.04, 24.04 and 24.04 arm64 (the API-runtime worker image downloads the matching, checksum-pinned Node.js and uv for the CPU; `agy` workers stay x86-64 only and `bin/worker-add` refuses them elsewhere). New `portability` workflow runs the pure-Python tests that pass on macOS and Windows from `tests/portable-python.txt` (`tests/run_portable.py`); the real install still needs Linux.

**Repository.** CodeQL code scanning (`.github/workflows/codeql.yml`: Python, JavaScript, workflows); every GitHub Action is
pinned to a commit SHA (Dependabot keeps them current); `CITATION.cff`; the security policy links the private advisory form;
a social preview image (`docs/img/social-preview.png`); removed `tools/publish-snapshot`.

**Documentation and quality.** Shorter README (the long material moved to `docs/`), `SECURITY-AUDIT.md` rewritten as a dated
security review log, dashboard screenshots in `docs/DASHBOARD.md`, a section on several Antigravity accounts in `docs/RUNTIMES.md`.
New `coverage` workflow measures how much Python code `make check` executes (child processes included) and feeds a README badge
(63 % at the time of writing; the Docker-bound parts are covered by `install`, not by this number). New `tests/test_locales.py`
keeps every dashboard translation complete. OpenSSF Scorecard badge in the README.

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
