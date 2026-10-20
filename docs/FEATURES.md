# Features

What Agent-Forge does, in more detail than the [README](../README.md). For the design behind it, see
[ARCHITECTURE.md](../ARCHITECTURE.md); for the isolation, [SECURITY.md](../SECURITY.md).

## The loop

```
 you ──goal──► bin/foreman (a deterministic loop, 20 s tick)
                 ├ plan ──► orchestrator LLM → tasks: role · files it owns · spec · acceptance criteria · dependencies
                 ├ dispatch ──► worker container (its own key or account, its own branch, egress only to its model API)
                 ├ gate ──► fresh offline container: full test suite + static checks
                 ├ review ──► orchestrator LLM: approve / rework (with feedback) / reject
                 └ merge ──► pull request: GitHub / GitLab (under its branch protection) or the internal Forgejo
```

No LLM decides *when* anything happens; the loop does. LLMs are called only for judgement (plan, review, "is the goal done?"),
each time as a fresh call without tools, so text inside a diff cannot make the reviewer run anything. Dependent tasks start only
after the merge, so they always build on merged code.

## What the loop does so you do not have to

- **Rebases for the worker.** The orchestrator rebases branches onto main. Conflicts that only append to a shared registry are
  merged keeping both sides. Only real conflicts go back to the worker.
- **Never loses work.** Whatever a run leaves uncommitted is pushed to its branch, and a run stopped by its cap continues from
  there next time. A task that hits the cap twice (`SPLIT_AFTER_CAPS`) is split into smaller tasks. Tasks that own too many files
  (`MAX_TASK_FILES`) are split before anyone starts.
- **Fixes red tests in the same conversation.** Before a run ends, the worker's clone is tested the way the gate would test it. A
  red suite goes back into the same agent conversation, which still has the context (`SELF_CHECK_ROUNDS`).
- **Continues instead of restarting.** A rework resumes the worker's own conversation with a short prompt.
- **Spends tokens on work, not on reading.** Each task carries a context pack: its files and the interfaces it uses, taken from a
  code map generated without any LLM (`CONTEXT_PACK`). Measured: [EFFICIENCY.md](EFFICIENCY.md).
- **Learns.** A review that sends work back may record one general lesson; lessons go into every later plan, review and task. What
  merged tasks really cost here (by size and role) goes into every plan, and each open task gets an estimate. With
  `MODEL_ROUTING=auto`, each role gets the cheapest worker model that has proven itself in this project.
- **Closes goals on evidence.** Each goal gets observable acceptance criteria, checked one by one before it is closed.
- **Works with your stack.** `TEST_CMD` and `GATE_IMAGE` are set per project: pytest by default, or `npm test`, `go test ./...`, ...
- **Survives crashes and reboots,** and does not blame the worker for Forgejo, Docker or provider outages.
- **Uses idle time.** While every subscription waits for its reset, the planner prepares the next goals, and main is tested without
  any LLM (a red main, flaky tests or missing docstrings become work items).
- **Tells you what matters.** See [Notifications](#notifications).

## Verification before a merge

1. **Self-check** in the worker's clone (above).
2. **Gate:** the full test suite and static checks, in a fresh container with no network, on a fresh clone of the branch. A branch
   that adds a key or token is refused.
3. **Review:** a tool-less LLM call that checks the diff against the task's spec and the role's definition of done, and rejects
   tests that cheat (weakened tests, caller detection, equality overrides).
4. **Mutation check** (optional, for risky code): the reviewer proposes up to four small deliberate bugs. The gate applies each one
   and expects the tests to fail. A bug that survives sends the branch back with a request for tests that catch it.
5. **Merge** by pull request. If upstream main moved and the branch conflicts, it is rebased and tested again.

A failure at any step becomes a rework with the output attached. After `MAX_REWORK` attempts the task is blocked and shows up in
the "needs you" list.

## Roles

Every task has exactly one role, a playbook in [`roles/`](../roles/) with a mission, what the role owns, what it never does and its
definition of done. There are 19:

| Group | Roles |
|---|---|
| Engineering | backend, frontend and fullstack developer, DevOps engineer, performance engineer, refactoring specialist |
| Quality and security | tester, security developer, security tester, accessibility specialist, code reviewer |
| Product, design, content | product manager, UX designer, technical writer, copywriter, editor, translator |
| Analysis | researcher, data analyst |

So a goal like "write the user guide and translate it" is planned as technical-writer and translator tasks, reviewed for fitness
for its readers and merged like any other branch. To add or change a role, see [ROLES.md](ROLES.md).

## Workers

A worker is one container running one agent CLI. You can mix runtimes in one team; the key is entered once, stored on the server
(mode 0600) and given only to that agent's environment.

| Runtime | Pays with |
|---|---|
| `claude` (Claude Code) | Anthropic API key, with a hard USD cap per run |
| `codex` (Codex CLI) | OpenAI API key |
| `gemini` (Gemini CLI) | Gemini API key |
| `agy` (Antigravity) | the subscription quota of a Google account, one account per worker |
| `compat`, `aider` | your own OpenAI-compatible server (Ollama, vLLM, LM Studio, llama.cpp) or OpenRouter credits |

More runtimes are registered in the code (cost model, egress hosts, readiness checks) but no worker image ships their CLI yet, so
`bin/worker-add` refuses them. The current list is in [RUNTIMES.md](RUNTIMES.md); how to choose is in
[CHOOSING-AN-AI.md](CHOOSING-AN-AI.md).

## Orchestrator

The orchestrator plans goals, reviews branches and judges whether a goal is done. It can use the Claude Code CLI on the host, Claude
or Gemini through an Antigravity account, or a paid API (`anthropic-api`, `openai-api`, `gemini-api`). The providers are tried in
the order you set, so an exhausted quota falls through to the next one. See [PROVIDERS.md](PROVIDERS.md).

## Cost and safety controls

- **USD caps** per run, day, month, project and worker (`etc/limits.conf`, Settings → Money). Token and tool-call caps per run.
- **Quota awareness** for subscription runtimes: a worker whose quota is low waits for the reset.
- **Egress allow-list:** a worker reaches its provider's API host and nothing else. The Egress view raises alarms for new domains
  and for attempts to reach private address ranges.
- **Kill switch:** `bin/killswitch on [--hard] [--all]` refuses new dispatches and stops running runs; `--hard` also stops the
  worker containers.
- **Approvals:** `APPROVE=plan,risky,merge` makes the team wait for you before a plan is executed, before a risky task runs or
  before a merge. Pending approvals, questions and blocked tasks are in one "needs you" list.
- **Questions:** the planner can ask you a question instead of guessing. Your answer becomes a binding decision for later plans.

## Where the code lives

By default a project lives on the built-in Forgejo. Connect GitHub or GitLab (cloud or self-hosted) and the orchestrator syncs
upstream main, pushes branches that passed the gate and the review, opens pull requests and merges them under your branch
protection. Workers never see the token. See [UPSTREAM.md](UPSTREAM.md).

## Several projects

One install runs several projects side by side. Each has its own orchestrator, repository, worker pool, settings and kill switch
(dashboard → project picker → "+ New project", or `bin/foreman init-project`).

## Notifications

Set `NOTIFY_URL` and `NOTIFY_FORMAT` (`ntfy`, `slack`, `discord` or `json`) in the project settings. `NOTIFY_EVENTS` chooses what
is sent; the default is `goal_done`, `task_blocked`, `killswitch`, `questions`, `quota_out`, `stalled` and `project_done`.
`auth` and `approval` are also available. The comments in [`etc/foreman.example.conf`](../etc/foreman.example.conf) explain each
setting.

## Dashboard

Overview, Tasks, Live, Timeline, Insights, System and Settings, with light and dark themes and switchable languages. See
[DASHBOARD.md](DASHBOARD.md).

## From a shell

```bash
forge goal "Add a CSV export to the expenses module, with tests"
forge project "Build the app described in docs/vision.md"     # a roadmap of goals in the backlog
forge status | backlog | questions
forge pause | resume
forge autopilot on|off                                        # propose goals from the vision automatically
forge goal <goal-id> approve | pause | prioritize | cancel
echo "Use the existing parser" | forge task <task-id> feedback -   # send its work back with your words
bin/killswitch on [--hard] [--all]
```

`forge` passes everything it does not handle itself to `bin/foreman`; `bin/foreman --help` lists every command.
