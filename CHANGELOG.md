# Changelog

## Unreleased
**Finite goals.** The planner refuses open-ended goals ("more", "improve", "continue") and asks the owner for a finite end state - the answer replaces the goal. The autopilot proposes only finite goals; when nothing is left in the vision it switches itself off and sends a `project_done` notification.

**Open source.** Agent-Forge is now licensed under the Apache License 2.0 (was: Sustainable Use License, source-available).
Selling it, hosting it and building on it are allowed; the commercial license (`LICENSE_EE.md`) and the contributor license
agreement are gone, contributions are accepted under Apache-2.0 (`git commit -s`).

`NOTIFY_URL=outbox`: notifications are appended to `runs/notify-outbox.jsonl` for a relay outside the machine (when the
sandbox may not reach your notification server itself); the file can be deleted at any time.

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
