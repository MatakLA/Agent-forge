# Architecture

Agent-Forge is a handful of small, standard-library Python and bash programs around four long-running pieces: the foreman loop,
worker containers, an egress proxy and an internal Forgejo. All state is plain files.

```
                         ┌──────────────────────────── the Agent-Forge machine (ideally a dedicated VM) ─────────────────────────┐
  you ── bin/foreman ──► │ bin/foreman run ── runs/foreman/ (goals, tasks, events, llm log)        web/server.py (dashboard)     │
                         │      │  plan / review / check                                                                         │
                         │      ├──► claude -p (Claude Code)  ·  agy in a worker (Claude/Gemini)  ·  optional paid API            │
                         │      │  dispatch                                                                                      │
                         │      ├──► bin/dispatch ─► bin/run-supervisor ─► docker exec worker-N agy-run …   (one run per worker)  │
                         │      │                                              │ git push feat/…                                  │
                         │      │                          ┌────────── workers_internal (no route out) ──────────┐               │
                         │      │                          │ worker-1 … worker-N ──► egress-proxy ──► allow-list │──► model API  │
                         │      │                          │            └──────────► forgejo                     │               │
                         │      │  gate                    └─────────────────────────────────────────────────────┘               │
                         │      ├──► bin/review-branch: fresh clone, full test suite in a new offline container (+ mutations)    │
                         │      │  merge                                                                                         │
                         │      └──► bin/open-pr + bin/merge-pr (orchestrator bot; main is protected)                            │
                         └───────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Components

### bin/foreman - the loop
A daemon that ticks every `LOOP_SEC` (20 s). It never asks an LLM *when* to act; it calls one only for judgement:

| call | input | output |
|---|---|---|
| `plan` | goal, vision file, repo tree, module heads, goals in progress, recent tasks | tasks: role, files, spec, acceptance criteria, depends_on, risk |
| `review` | task, role's definition of done, gate output, diff | approve / rework (feedback) / reject (+ mutations for risky code) |
| `check` | goal + results of its tasks | done (summary) or follow-up tasks |
| `next` / `roadmap` | vision, goals so far | the next goal / an ordered backlog of goals + questions for the owner |

Every call is a new, tool-less session. A stable system prompt - `prompts/orchestrator.md`, the project's `etc/team-rules.md` and
the role catalogue - goes in as a system prompt (Claude Code caches it). Providers are tried in the order of `PLAN_PROVIDERS` /
`REVIEW_PROVIDERS`; a provider out of quota is skipped until its printed reset (Claude Code: "resets 3pm (Europe/Prague)";
agy: "Resets in 110h11m52s"). Antigravity calls go to the worker account with the most remaining quota for that model family
and skip accounts `agy /usage` reports as exhausted.

Scheduling rules the loop enforces:
- **Parallel by default.** A task is ready when its dependencies are merged and no running task owns one of its files
  (`SHARED_FILES` such as a registry are exempt). Free workers get ready tasks; while idle workers outnumber the work that is
  coming (ready tasks + ~2 per goal being planned), further backlog goals are started in the same tick and planned in parallel.
- **Even quota drain.** A task goes to the free worker with the most remaining worker-model quota.
- **Rework, not blame.** A failing gate or a review asking for changes sends the task back with the output (up to `MAX_REWORK`).
  A review that produced no usable verdict is retried without costing the worker an attempt. A merge conflict is sent back as
  "rebase" without costing an attempt.
- **Crash-safe.** Before re-reviewing a branch the loop checks Forgejo for an already-merged PR (a restart between merge and
  bookkeeping does not re-open finished work).
- **Goals close themselves.** When all of a goal's tasks are done/blocked, a `check` call decides "done" or plans follow-ups,
  up to `MAX_GOAL_ROUNDS`.

### bin/dispatch and bin/run-supervisor - guarded runs
`dispatch` refuses (fail closed) when: the kill switch is on, the worker is busy, `MAX_PARALLEL` is reached, the day's run/token
budget is used, the worker's subscription quota is under the floor (`agy /usage`), the egress proxy is unhealthy, or there are
unacknowledged HIGH egress alarms. `run-supervisor` prepares the worker's clone (aborting a rebase/merge a killed run left
behind), runs `agy-run` with the task prompt, and kills the run on timeout, token cap or kill switch. A run that ends with only a
transient network error on the final API call after a complete answer is not counted as a failure. After the run it checks the
proxy log for that worker; any HIGH alarm trips the kill switch. `CONSEC_FAIL_TRIP` non-ok runs in a row trip it as well.

### Worker runtimes (bin/lib/runtimes.py)
A worker runs `agy` (subscription) or, with an API key, `claude` / `codex` / `gemini` (worker/Dockerfile.api, worker/af-agent).
The runtime decides the command line and how the event stream is read (tokens, tool calls, conversation id, result, cost);
everything else - dispatch, limits, self-check, salvage, gate, review - is the same. API workers are overflow by default and
capped in USD per run and per day. docs/RUNTIMES.md.

### Upstream (bin/lib/upstream.py, bin/upstream)
With `FORGE_KIND=github|gitlab` the orchestrator pushes approved branches from Forgejo to the project's real repository, opens
and merges pull / merge requests there (tasks wait in status `merging` while upstream checks run) and syncs upstream main back
into Forgejo every minute. Workers and the gate keep using Forgejo only. docs/UPSTREAM.md.

### Worker containers (worker/)
Ubuntu image with Python, git and `agy` (pinned version, SHA-512 verified, installed outside HOME). Non-root user, all
capabilities dropped, `no-new-privileges`, CPU/memory/PID limits, only on the internal network. Each worker has its own HOME
volume (its agy login lives in a private gnome-keyring unlocked per run), its own Forgejo account and token (read-only mount),
and its own clone of the repository. Workers read `AGENTS.md` / `GEMINI.md` automatically.

### Egress proxy (egress/)
Squid on the internal network, the only way out. `CONNECT :443` to exact hostnames in `egress/allowlist.txt` (no wildcards);
private, loopback, link-local and CGNAT destinations are denied whatever a name resolves to. `etc/egress-baseline.txt` lists
denials that are known and harmless so they do not raise alarms; they stay blocked.

### Forgejo (forgejo/, scripts/forge-bootstrap)
Rootless Forgejo on the internal network and `127.0.0.1`. Bots: `orchestrator` (opens and merges PRs), `monitor` (read-only,
used by the gate and the dashboard), `worker-N` (team `workers`, push branches). `main` is protected: no direct pushes, only the
orchestrator merges.

### Projects
`bin/foreman --project NAME` (or `FORGE_PROJECT`) selects `projects/NAME/project.conf` (on top of `etc/foreman.conf`), its
repository (`FORGE_REPO`), worker pool (`WORKERS`), team rules, vision, lessons and state (`runs/projects/NAME/foreman/`). Everything
the foreman starts inherits the project, so dispatch, the gate and the PR tools work on that repository and workers use the clone
`~/work/NAME`. The default project (no `--project`) keeps the original layout (`etc/`, `runs/foreman/`, `~/playground`). Each project
has its own kill switch (`runs/projects/NAME/KILLSWITCH`; `runs/KILLSWITCH-ALL` stops all). Run directories, the ledger, the
global limits (`etc/limits.conf`) and the egress proxy are shared.

### Self-healing details
- `auto_rebase()` (before every gate and on a merge conflict): orchestrator-bot rebase onto main; conflicts only in line-based
  `SHARED_FILES` are resolved with `git merge-file --union`; anything else goes back to the worker without costing an attempt.
- `run-supervisor` salvage: after a token-cap/timeout kill, `git add -A && commit && push` on the task branch (never main).
- Split: `SPLIT_AFTER_CAPS` cap hits → `step_split` asks the planner for 2-3 smaller tasks; dependents are re-pointed.
- Continuation: the agy `conversation_id` of each run is kept; a rework on the same worker passes `--conversation` and a short prompt.
- Lessons: reviews may return `lesson`; stored in `lessons.md` per project, shown to planner, reviewer and workers.
- Context pack: `bin/lib/codemap.py` maps every module on main (purpose from its docstring, public signatures); each task gets
  its own files, the interfaces it uses and a one-line map of the rest (`CONTEXT_PACK`, capped by `CONTEXT_PACK_CHARS`).
- Self-check: `run-supervisor` runs the suite in the worker's clone after the run; on red it resumes the same agy conversation
  with the output (`SELF_CHECK_ROUNDS`). After every run it commits + pushes what is left on the task branch.
- Pre-split: tasks owning more than `MAX_TASK_FILES` files go to `step_split` before dispatch.
- Robustness: shared state files are changed under a thread + file lock (`locked()`); runs whose supervisor died are closed and
  re-queued (`orphaned()`); quota stops mid-run re-queue without an attempt; gate infrastructure failures (Forgejo, Docker) are
  retried, not blamed on the worker; the rebase clone is used by one step at a time.
- Plan ahead + maintenance: when the whole pool is under its quota floor, backlog goals are planned ahead and main is tested 3x
  and mapped without any LLM. Details and the data behind all of this: docs/EFFICIENCY.md.

### Roles (roles/) and prompts (prompts/)
Each role file is a playbook - mission, what it owns, how it works, what it never does, definition of done. The planner sees the
catalogue; the worker gets its role's full playbook in the task; the reviewer gets that role's definition of done.

### Dashboard (web/)
One file, standard library. Reads `bin/foreman status --dashboard`, run logs and `agy /usage` locally (or over ssh), serves a
single page on 127.0.0.1. Write actions: queue goals / roadmaps, reorder the backlog, answer the planner's questions, stop/continue
a project (or all), edit settings (a fixed list of keys, validated by `bin/foreman settings apply`; the loop reloads its config on
the next tick), add a worker (`bin/worker-add`) and run its agy login in a browser terminal, create a project. Owner text reaches
`bin/foreman` on stdin. Access control (Host check, CSRF header, optional token) - see SECURITY.md.

## State on disk

| path | what |
|---|---|
| `projects/<name>/` | per-project config: project.conf, team-rules.md, vision.md, lessons.md, decisions.md (owner answers) |
| `runs/foreman/` or `runs/projects/<name>/foreman/` | the foreman state of the default / a named project (the rows below) |
| `runs/foreman/goals/*.json`, `tasks/*.json`, `*.md` | goals, tasks (spec, history, diff stats), the exact prompt each worker got |
| `runs/foreman/diffs/<task>.diff` | the diff each review saw (dashboard diff viewer) |
| `runs/foreman/events.jsonl`, `llm.jsonl` | everything the loop did; every orchestrator call with provider, duration and usage |
| `runs/<UTC>-worker-N/` | one worker run: prompt, full agy event stream, meta (tokens, reason, quota before/after) |
| `runs/ledger.jsonl`, `runs/ALERT` | run ledger, alerts |
| `runs/KILLSWITCH`, `runs/projects/<name>/KILLSWITCH`, `runs/KILLSWITCH-ALL` | kill switch of the default project / a project / all projects |
| `runs/agy-models.json` | the cached `agy models` list for the dashboard's model pickers |
| `runs/foreman/codemap.json`, `run-stats.json` | code map of main (per commit); per-run model, variant, quota used (Metrics) |
