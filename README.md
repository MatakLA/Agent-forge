# Agent-Forge

**A self-hosted AI team that works through a queue on its own, inside a sandbox it cannot escape.**

You write a goal ("add a contacts module with birthday reminders", "write the user guide", "translate the app to German").
An orchestrator splits it into small tasks, each with a role (backend developer, tester, security tester, technical writer,
UX designer, translator, ... 19 playbooks in `roles/`). Worker agents build the tasks in parallel, each in its own locked-down
container on its own branch. Every branch runs the full test suite in a fresh offline container, gets reviewed, and is merged
through a pull request on your own Forgejo - or sent back with concrete feedback until it is right.

It is built to run on subscriptions you already pay for: **Claude Code** for the orchestrator and **Google Antigravity** (`agy`)
for the workers. Paid per-token APIs are optional (see [docs/PROVIDERS.md](docs/PROVIDERS.md)).

> Status: alpha. Extracted from a setup that ran a 4-worker team on one owner's project for two weeks (~400 worker runs,
> ~190 pull requests). Expect rough edges and read [SECURITY.md](SECURITY.md) before you run it.

---

## What is different from Vibe Kanban, Conductor, Claude Squad & co.

Most parallel-agent tools run agents on your machine in git worktrees and leave review and merging to you. Agent-Forge
assumes you are asleep:

| | typical parallel-agent UI | Agent-Forge |
|---|---|---|
| Isolation | git worktree on your machine, full network | a container per worker, all capabilities dropped, no docker socket, **egress only to an allow-list** through a proxy |
| What happens next | you decide | the orchestrator plans goals into role-tagged tasks; an optional autopilot picks the next goal from your vision file |
| Verification | you read the diff | full test suite in a **fresh offline container** + an AI review + optional **mutation tests** before any merge |
| Failures | you notice | automatic rework with the gate output, review retries that do not blame the worker, crash recovery, kill switch |
| Capacity | one key or one subscription | several worker accounts; work goes to the account with the most quota left, exhausted accounts are skipped |
| Team | coding agents | 19 roles incl. technical writer, translator, UX designer, product manager, data analyst |

If you want an interactive cockpit for agents you watch, those tools are great. Agent-Forge is for a queue you leave running.

## How it works

```
 you ──goal──► bin/foreman (deterministic loop, 20 s tick)
                 ├ plan ──► orchestrator LLM (Claude Code · Claude or Gemini via Antigravity · optional paid API)
                 │          → tasks: role · files it owns · spec · acceptance criteria · dependencies
                 ├ dispatch ──► worker-N container (agy, its own account, its own branch, egress only to the model API)
                 ├ gate ──► fresh offline container: full test suite + static checks (+ mutation tests for risky code)
                 ├ review ──► orchestrator LLM: approve / rework (with feedback) / reject
                 └ merge ──► pull request on the internal Forgejo; only the orchestrator bot may merge to main
```

No LLM decides *when* anything happens - the loop does. LLMs are called only for judgement (plan, review, "is the goal done?"),
each time as a fresh, tool-less call, so worker-written content in a diff cannot make the reviewer run anything.
Details: [ARCHITECTURE.md](ARCHITECTURE.md) · security model: [SECURITY.md](SECURITY.md) · roles: [docs/ROLES.md](docs/ROLES.md).

## Day to day

```bash
bin/foreman goal "Add a CSV export to the expenses module, with tests"
bin/foreman project "Build the app described in docs/vision.md"   # → a roadmap of goals in the backlog
bin/foreman status            # goals, tasks, workers, recent events
bin/foreman backlog           # what is queued
bin/foreman questions         # what the planner needs you to decide
bin/foreman pause | resume
bin/foreman retry <task> | cancel <goal>
bin/killswitch on [--hard]    # stop this project now (--all: every project)
bin/doctor                    # is everything healthy? says how to fix what is not
```

The web dashboard (`web/server.py`, installed by `scripts/install-services`) shows the team, the task board with each task's
spec, history and diff, a 24-hour timeline per worker, dependency graphs, team metrics and live worker transcripts, and lets you
ask the team questions about the code. You queue goals (now, into the backlog, or as a big project the planner turns into a
roadmap), reorder the backlog and answer the planner's questions there; answers become binding decisions for later plans.
Optional notifications (ntfy, Slack, Discord, any webhook) tell you when a goal is done, a task is blocked, the kill switch
tripped or all accounts are out of quota. Its **Settings** tab stops/continues a project (soft or hard, or all projects), picks the
orchestrator providers and models and the worker models (from the live `agy models` list), assigns workers to the project, edits
behaviour and the global limits, and adds a worker including its Antigravity login in a browser terminal. The dashboard speaks English and Slovak.

## Several projects at once

One install can run several projects side by side, each with its own orchestrator (foreman), repository, vision and worker pool:

```bash
bin/foreman init-project shop --repo acme/shop --workers worker-5,worker-6,worker-7,worker-8
FORGE_REPO=acme/shop scripts/forge-bootstrap          # repository, access, protected main
sudo scripts/install-services --project shop          # its own foreman service
bin/foreman --project shop goal "Add a cart page"
bin/foreman projects                                   # every project with its pool and state
```

With 8 workers that is e.g. workers 1-4 on the default project and 5-8 on `shop`. Each project has its own kill switch
(`bin/killswitch --project shop on`), and `bin/killswitch --all on` stops every project at once; goals, tasks, lessons and quotas
are per project too. The egress proxy is shared. The dashboard has a project selector.

## What the loop does so you do not have to

- **Works with your stack.** The gate, the workers' self-check and maintenance run your project's `TEST_CMD` in your
  `GATE_IMAGE` - pytest by default, `npm test`, `go test ./...` or anything else with a command-line runner.
- **Survives crashes and reboots.** Runs whose supervisor died are closed and re-queued without costing an attempt; a merge that
  Forgejo was still checking is retried instead of being reported as a conflict; Forgejo or Docker outages are not blamed on
  the worker.

- **Rebases for the worker.** Before a branch is tested it is rebased onto main by the orchestrator bot; append-conflicts in shared
  registries (`SHARED_FILES` with .jsonl/.md/.txt/.csv) are merged keeping both sides. Only real conflicts go back to the worker.
- **Never loses work.** A run stopped by its token cap or timeout has its unfinished work committed and pushed as a WIP commit; the
  next attempt continues from it. A task stopped by the cap twice is split by the planner into smaller tasks.
- **Continues instead of restarting.** A rework on the same worker resumes the worker's own agy conversation with a short prompt.
- **Learns.** A review that sends work back may record one general lesson; lessons go into every later plan, review and task.
- **Goal-level acceptance.** Each goal gets observable acceptance criteria; tasks are reviewed against them and the goal is only
  closed when they hold.
- **Structured answers.** Antigravity orchestrator calls use `--json-schema`, so plans and verdicts come back as valid JSON.
- **Spends quota on work, not on reading.** Each task carries a context pack (its files and the interfaces it uses, generated
  from main - no LLM), the worker fixes a red test suite in the same conversation before its run ends, tasks over
  `MAX_TASK_FILES` files are split before work starts, and the Metrics tab shows quota per merged task by model and variant
  (A/B tests built in). While all accounts wait for their quota, the planner prepares the next goals and main is checked
  without any LLM. See [docs/EFFICIENCY.md](docs/EFFICIENCY.md).

## Setup

About an hour, most of it waiting for images. Full guide: **[docs/SETUP.md](docs/SETUP.md)**. In short:

```bash
sudo bash scripts/vm-baseline-ubuntu.sh        # Docker + basics, ideally on a dedicated Ubuntu VM; log in again afterwards
cp etc/limits.example.conf etc/limits.conf && cp etc/foreman.example.conf etc/foreman.conf
bin/egress-up                                  # the allow-list proxy + the internal network
scripts/forge-bootstrap                        # Forgejo, bot accounts, repository, branch protection
bin/worker-add 1 && docker exec -it worker-1 agy-run    # one worker; log it in once with its own Google account
bin/foreman --selftest && sudo scripts/install-services
bin/foreman goal "..."
```

## Read this before you add workers

Each worker logs into Antigravity with its own Google account. Google's terms prohibit **bypassing system measures or
circumventing usage limits**, and Google has banned accounts for it. Running several accounts you pay for, for your own work,
is your judgement and your risk - Agent-Forge does not encourage pooling accounts to get around limits, and you should not use
it that way. The same goes for any provider whose CLI you automate: read its terms first.

## License

Apache 2.0 - see [LICENSE](LICENSE).
