<p align="center">
  <img src="docs/img/logo.svg" alt="Agent-Forge" width="320">
</p>

<p align="center"><b>A self-hosted AI dev team that works through a queue on its own - inside a sandbox it cannot escape.</b></p>

<p align="center">
  <a href="https://github.com/MatakLA/Agent-forge/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/MatakLA/Agent-forge/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/MatakLA/Agent-forge/actions/workflows/install.yml"><img alt="install" src="https://github.com/MatakLA/Agent-forge/actions/workflows/install.yml/badge.svg"></a>
  <a href="https://github.com/MatakLA/Agent-forge/releases"><img alt="release" src="https://img.shields.io/github/v/release/MatakLA/Agent-forge?include_prereleases"></a>
  <a href="LICENSE"><img alt="license: Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
  <img alt="python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-informational">
  <img alt="dependencies: stdlib only" src="https://img.shields.io/badge/dependencies-stdlib%20only-brightgreen">
</p>

![Agent-Forge dashboard tour](docs/img/tour.gif)

You write a goal - *"add due dates to tasks, with tests"*, *"write the user guide"*, *"translate the app to German"*. An
orchestrator splits it into small tasks, each with a role (19 playbooks: backend developer, tester, security tester, technical
writer, translator, UX designer, ...). Worker agents build the tasks in parallel, each in its own locked-down container on its own
branch. Every branch runs the full test suite in a fresh offline container, gets an AI review, and is merged through a pull
request - on your GitHub or GitLab repository, or on the built-in Forgejo - or is sent back with concrete feedback until it is
right. Then the next goal starts, from your backlog or your product vision.

> **Experimental - for testing and evaluation.** Agent-Forge is young (alpha). Run it on a dedicated VM with test data, point it
> only at repositories where an AI merging code is acceptable to you, and read [SECURITY.md](SECURITY.md) first.

## Try it

| | | |
|---|---|---|
| **Look around** (1 min) | `docker run --rm -p 8171:8171 ghcr.io/matakla/agent-forge-demo:latest` | The real dashboard on invented data. Needs only Docker. Open <http://localhost:8171>. |
| **Install it** (~20 min) | `curl -fsSL https://raw.githubusercontent.com/MatakLA/Agent-forge/main/install.sh \| bash` | On an Ubuntu 22.04+ machine or VM (4+ vCPU, 8+ GB RAM, 40 GB disk), as a normal user with sudo. |
| **By hand** | [docs/INSTALL.md](docs/INSTALL.md) | Cloud-init, `git clone` + `./forge install`, update, uninstall, macOS and Windows. |

Want a fixed version instead of `main`? Pin a tag from the [releases](https://github.com/MatakLA/Agent-forge/releases):
`curl -fsSL https://raw.githubusercontent.com/MatakLA/Agent-forge/vX.Y.Z/install.sh | FORGE_REF=vX.Y.Z bash`.

After the install, open the dashboard through an SSH tunnel (`forge url` prints the command), add a worker, give it a goal. A
first goal on a sample project, step by step: [docs/QUICKSTART.md](docs/QUICKSTART.md).

## Why it is different

Most parallel-agent tools run agents on your machine in git worktrees and leave the review and the merging to you. Agent-Forge
assumes you are not watching:

| | typical parallel-agent UI | Agent-Forge |
|---|---|---|
| **What happens next** | you decide | goals are planned into role-tagged tasks; an autopilot can pick the next goal from your vision |
| **Isolation** | git worktree on your machine, full network | a container per worker, no capabilities, no docker socket, **egress only to an allow-list** |
| **Verification** | you read the diff | full test suite in a **fresh offline container**, AI review and optional **mutation tests**, before any merge |
| **Failures** | you notice | automatic rework with the gate output, crash recovery, a kill switch |
| **Your repository** | the agent pushes from your machine | **pull requests on GitHub / GitLab** under your branch protection; workers never see a token |
| **Cost** | whatever the agent spends | **USD caps** per run, day, month, project and worker; subscriptions or **your own model server** |
| **Control** | you drive every step | it drives; you approve what you choose (plans, risky tasks, merges) from one "needs you" list |

If you want a cockpit for agents you watch, those tools are great. Agent-Forge is for a queue you leave running.

## How it works

```
 you ──goal──► bin/foreman (a deterministic loop, 20 s tick)
                 ├ plan ──► orchestrator LLM (Claude Code · Claude/Gemini via Antigravity · optional paid API)
                 │          → tasks: role · files it owns · spec · acceptance criteria · dependencies
                 ├ dispatch ──► worker container (its own key or account, its own branch, egress only to its model API)
                 ├ gate ──► fresh offline container: full test suite + static checks (+ mutation tests for risky code)
                 ├ review ──► orchestrator LLM: approve / rework (with feedback) / reject
                 └ merge ──► pull request: GitHub / GitLab (under its branch protection) or the internal Forgejo
```

No LLM decides *when* anything happens - the loop does. LLMs are called only for judgement (plan, review, "is the goal done?"),
each time as a fresh, tool-less call, so text inside a diff cannot make the reviewer run anything.
More: [ARCHITECTURE.md](ARCHITECTURE.md) · [SECURITY.md](SECURITY.md) · [docs/ROLES.md](docs/ROLES.md).

## What the loop does for you

- **Rebases, reworks, recovers.** Rebases branches itself, sends failures back with the gate output, never loses unpushed work.
- **Fixes red tests in the same conversation,** before the gate sees the branch.
- **Spends tokens on work, not on reading** - a code-map context pack per task ([measured](docs/EFFICIENCY.md)).
- **Learns** lessons from reviews and task costs from history; routes each role to the cheapest model that proved itself.
- **Closes goals on evidence:** observable acceptance criteria, checked one by one.
- **Works with your stack:** `TEST_CMD` and `GATE_IMAGE` per project (pytest, `npm test`, `go test ./...`).
- **Survives crashes and reboots,** and does not blame the worker for Forgejo, Docker or provider outages.
- **Tells you what matters** through ntfy, Slack, Discord or a webhook.

The long version: [docs/FEATURES.md](docs/FEATURES.md).

## Workers: API keys or a subscription

| runtime | pays with | notes |
|---|---|---|
| **Claude Code** | Anthropic API key | hard USD cap per run and per day |
| **Codex CLI** | OpenAI API key | USD per day, token and tool-call caps per run |
| **Gemini CLI** | Gemini API key | USD per day, token and tool-call caps per run |
| **Antigravity** | a Google account with Antigravity | uses its subscription quota; one account per worker |
| **Your model server** | nothing (Ollama, vLLM, LM Studio) or OpenRouter credits | Codex CLI or Aider against any OpenAI-compatible server; the worker may reach exactly that address and port |

Mix them in one team. A key is stored on the server only (mode 0600) and reaches only the agent's environment; a worker can talk to
its provider's API host and nothing else, and the gate refuses any branch that adds a key. Which one to choose:
[docs/CHOOSING-AN-AI.md](docs/CHOOSING-AN-AI.md). Details: [docs/RUNTIMES.md](docs/RUNTIMES.md).

> **Several Antigravity accounts are a testing setup only.** Running more than one subscription account for one person's work may
> conflict with the provider's terms. Agent-Forge supports it for testing and evaluation; it does not encourage pooling accounts to
> get around limits. For regular work use API keys (paid per token, with the caps above) or a single subscription. Read the terms
> of any provider whose CLI you automate.

## Screenshots

| | |
|---|---|
| ![Overview: what needs you, the team and why idle workers are idle, goals, backlog](docs/img/overview.png) | ![A goal's page: tasks, events, cost, criteria](docs/img/goal.png) |
| **Overview** - what needs you, the team, goals, backlog | **Goal page** - its tasks, events, cost, acceptance criteria |
| ![Live transcripts of the workers](docs/img/live.png) | ![Replay of a finished run](docs/img/replay.png) |
| **Live** - what every worker is doing right now | **Replay** - any finished run, searchable |
| ![Task sheet: merge or send back, cost, runs, diff](docs/img/task.png) | ![Dependency graph with the critical path](docs/img/graph.png) |
| **Task** - the actions that fit its state, runs, cost, diff | **Graph** - open work and the critical path |
| ![Money and quota](docs/img/money.png) | ![Experiments: A/B verdicts](docs/img/experiments.png) |
| **Money & quota** - spend, caps, when the budget runs out | **Experiments** - A/B results with 95 % intervals |

Invented demo data; `tools/screenshots.sh` regenerates them. More about the dashboard: [docs/DASHBOARD.md](docs/DASHBOARD.md).

## Day to day

Everything is in the dashboard. The same from a shell, with the `forge` command (it is installed to `~/.local/bin` when that
directory exists, otherwise use `./forge` in the checkout):

```bash
forge goal "Add a CSV export to the expenses module, with tests"
forge project "Build the app described in docs/vision.md"      # a roadmap of goals in the backlog
forge status                                                   # goals, tasks, workers, recent events
forge backlog | questions                                      # waiting goals / questions the planner has for you
echo "Use PostgreSQL" | forge answer 1                         # becomes a binding decision for later plans
forge pause | resume                                           # stop / resume new steps (running runs finish)
forge doctor                                                   # checks the whole install, says how to fix what is wrong
forge logs [foreman|web]                                       # follow a service's log
forge update | uninstall                                       # see docs/INSTALL.md
```

`forge` hands everything it does not know to `bin/foreman`, so every command in `bin/foreman --help` works. The kill switch is
`bin/killswitch on [--hard] [--all]`. One install runs **several projects** side by side, each with its own orchestrator,
repository and worker pool.

## Documentation

**Start:** [Install](docs/INSTALL.md) · [Quickstart](docs/QUICKSTART.md) · [FAQ](docs/FAQ.md) · [Troubleshooting](docs/TROUBLESHOOTING.md)
**Use:** [Features](docs/FEATURES.md) · [Dashboard](docs/DASHBOARD.md) · [Workers and keys](docs/RUNTIMES.md) · [GitHub / GitLab](docs/UPSTREAM.md) · [Roles](docs/ROLES.md)
**Inside:** [Architecture](ARCHITECTURE.md) · [Security](SECURITY.md) · [Efficiency](docs/EFFICIENCY.md) · [Field report](docs/FIELD-REPORT.md) · [Roadmap](ROADMAP.md) · [Changelog](CHANGELOG.md)

All of it, grouped: [docs/README.md](docs/README.md). `make check` runs the tests that need nothing (no Docker, no model calls);
`make test` adds the integration tests against a fake model API.

## Community

- **Questions and ideas:** [GitHub Discussions](https://github.com/MatakLA/Agent-forge/discussions)
- **Bugs and feature requests:** [Issues](https://github.com/MatakLA/Agent-forge/issues) (there are forms for both)
- **Contribute:** [CONTRIBUTING.md](CONTRIBUTING.md) · [Code of Conduct](CODE_OF_CONDUCT.md) · [Support](SUPPORT.md)
- **Security problems:** [report privately](https://github.com/MatakLA/Agent-forge/security/advisories/new) - see [SECURITY.md](SECURITY.md); please do not open a public issue.

## License

[Apache License 2.0](LICENSE): use it, change it, run it for your company, redistribute it - including commercially.
Contributions are accepted under the same license.
