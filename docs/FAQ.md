# FAQ

Short, honest answers. Where a topic has its own page, the answer links to it.

- [Is it safe to run?](#is-it-safe-to-run)
- [Do I need a VM?](#do-i-need-a-vm)
- [Which AI does it need?](#which-ai-does-it-need)
- [What does it cost?](#what-does-it-cost)
- [Can I use a subscription instead of API keys?](#can-i-use-a-subscription-instead-of-api-keys)
- [Do I need GitHub or GitLab?](#do-i-need-github-or-gitlab)
- [Can I use local models?](#can-i-use-local-models)
- [Does it run on Windows or macOS?](#does-it-run-on-windows-or-macos)
- [How is it different from Claude Code, Codex or Devin-style tools?](#how-is-it-different-from-claude-code-codex-or-devin-style-tools)
- [Can the workers touch my repository?](#can-the-workers-touch-my-repository)
- [How do I stop it?](#how-do-i-stop-it)
- [Does my code leave the machine?](#does-my-code-leave-the-machine)
- [Is it production ready?](#is-it-production-ready)

## Is it safe to run?

Safer than running an agent in your own shell, but not risk-free. That is why the project is labelled experimental.

What protects you:

- Every worker is a container with no capabilities, no Docker socket and no access to your files.
- The workers can only reach hosts on an **egress allow-list**, through a proxy. Everything else is refused.
- Every branch is tested in a **fresh container with no network** before it is merged.
- USD caps and a kill switch limit what a run can spend and how long it can go on.

What does not protect you: a container is not a virtual machine, all workers share one egress proxy, and an allowed host
accepts any payload. The AI provider you choose sees the prompts and the code in them. The full list is in
[SECURITY.md](../SECURITY.md#honest-limits). Run it on a dedicated VM, with test data, on repositories where an AI merging code is
acceptable to you.

## Do I need a VM?

No, but it is the recommended setup. The installer runs on any Ubuntu 22.04+ machine with systemd and Docker. Agent-Forge starts
containers, installs system services and keeps API keys on that machine, so a VM keeps all of that away from the computer you
work on. The [demo](DEMO.md) needs neither: it is one `docker run`.

## Which AI does it need?

At least one for the orchestrator (planning and review) and at least one for the workers (writing code). They can be the same
account. Common choices:

- **Orchestrator:** the Claude Code CLI on the host (`claude-cli`), Claude or Gemini through an Antigravity account, or a paid API
  (`anthropic-api`, `openai-api`, `gemini-api`). Details: [PROVIDERS.md](PROVIDERS.md).
- **Workers:** `agy` (Antigravity), `claude`, `codex`, `gemini`, or your own model server through `compat` or `aider`. Details:
  [RUNTIMES.md](RUNTIMES.md).

You can mix runtimes in one team. [CHOOSING-AN-AI.md](CHOOSING-AN-AI.md) compares them, and `scripts/pick-ai.sh` asks a few
questions and recommends one.

## What does it cost?

Agent-Forge itself is free (Apache-2.0). You pay your AI provider, in one of three ways:

- **Subscription quota** - no per-token charge, but a quota. When it runs low, the worker is paused until it resets.
- **Per token** (API keys) - real money. Agent-Forge tracks the estimated spend and enforces USD caps per run, day, month and
  worker (`etc/limits.conf`). When a cap is reached, dispatch stops.
- **Your own hardware** - no token price; you pay for the machine and the power.

A run that fails and is reworked costs again. Start with small caps and watch **Insights → Money & quota** in the dashboard for the first few
goals. Do not assume a number from this page; it depends on your model, your project and your tests.

## Can I use a subscription instead of API keys?

Yes. The `agy` runtime works with an Antigravity (Google) subscription, one account per worker, and the `claude-cli` orchestrator
uses a Claude Code login. Both are quota-based. The quotas are limited, and a busy team can use them up within days. See the
"quota reality check" in [PROVIDERS.md](PROVIDERS.md). Check the provider's current terms for automated use before you rely on a
subscription.

## Do I need GitHub or GitLab?

No. By default a project lives on the built-in Forgejo, and the dashboard shows its pull requests. If the real home of your code is
GitHub or GitLab (cloud or self-hosted), connect it and Agent-Forge opens and merges pull requests there, under your branch
protection. Setup and token permissions: [UPSTREAM.md](UPSTREAM.md).

## Can I use local models?

Yes. Point `COMPAT_BASE_URL` at an OpenAI-compatible server (Ollama, vLLM, LM Studio, llama.cpp) and add a worker with
`--runtime compat` or `--runtime aider`. Then no code leaves your network. Two honest caveats: small local models write weaker
code and fail the gate more often, and the worker is allowed to reach that server on purpose. The orchestrator can also use a local
server through `openai-api` with `OPENAI_API_BASE`. See [RUNTIMES.md](RUNTIMES.md) and [PROVIDERS.md](PROVIDERS.md).

## Does it run on Windows or macOS?

The demo does, with Docker Desktop (on Windows, in a WSL2 terminal). The real install needs Linux with systemd, so on a Mac or a
Windows PC run an Ubuntu VM and install inside it. See [INSTALL.md](INSTALL.md#macos-and-windows).

## How is it different from Claude Code, Codex or Devin-style tools?

Claude Code and Codex are agents: you give one a task and watch it work, usually on your machine. Agent-Forge is not an agent. It
is a loop around agents: it plans a goal into tasks, runs several agents in isolated containers, tests and reviews each result,
merges it and takes the next goal. It uses those CLIs as workers. Hosted tools such as Devin run in the vendor's cloud. Agent-Forge
runs on your own machine, and the isolation and the caps are yours to inspect.

If you want to pair with one agent on one task, use the agent directly. Agent-Forge is for a queue you leave running.

## Can the workers touch my repository?

Not directly. Workers see only a checkout on the internal Forgejo, never your GitHub or GitLab, and they never hold your token. After
a branch passes the gate and the review, the orchestrator pushes it and opens a pull request with your token, which is kept in
the secrets directory on the host. Your required checks, required reviews and CODEOWNERS apply as for any contributor. With
`AUTO_MERGE=0` the pull request waits for you. See [UPSTREAM.md](UPSTREAM.md).

## How do I stop it?

From mild to hard:

```bash
forge pause              # stop taking new steps; running worker runs are not killed
forge resume             # continue
bin/killswitch on        # refuse new dispatches and kill running runs within seconds
bin/killswitch on --hard # also stop the worker containers
bin/killswitch off       # clear the switch (add --start to restart the containers)
```

The dashboard has the same controls, and a **Pause** toggle for the autopilot. To remove everything, see
[Uninstall](INSTALL.md#uninstall).

## Does my code leave the machine?

Yes, to the AI provider you choose: prompts contain code, and the provider sees them. With your own model server it does not leave
your network. If you connect GitHub or GitLab, branches and pull requests go there. Agent-Forge itself has no telemetry. The
egress allow-list (`egress/allowlist.txt`) lists the hosts the workers may reach, and `bin/egress-report` shows which hosts they tried.

## Is it production ready?

No. It is alpha software. The loop, the gate and the isolation are tested (`make test`, and a quickstart check in CI), but there is no stability promise yet: commands, settings and file formats can change between
releases, as the [CHANGELOG](../CHANGELOG.md) shows. Use it where an AI merging code is acceptable, keep your own branch protection
on, and read what it merged. See the [ROADMAP](../ROADMAP.md) for what is missing.

Found something wrong? See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) or open an issue.
