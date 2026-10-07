# Worker runtimes: subscriptions and API keys

Each worker is one container running one agent CLI. You can mix them in one team:

| runtime | what pays | how a worker is added | network the worker may reach |
|---|---|---|---|
| `agy` | an Antigravity (Google) subscription, one account per worker | `bin/worker-add N`, then sign it in once from the dashboard | Antigravity's hosts (`egress/allowlist.txt`) |
| `claude` | Anthropic API key, per token | `bin/worker-add N --runtime claude` | `api.anthropic.com` |
| `codex` | OpenAI API key, per token | `bin/worker-add N --runtime codex` | `api.openai.com` |
| `gemini` | Gemini API key, per token | `bin/worker-add N --runtime gemini` | `generativelanguage.googleapis.com` |

The dashboard does the same: Settings → "+ Add worker" → pick the runtime; for an API runtime it asks for the key once.

## Keys
API keys are stored in the secrets directory (`~/.config/sandbox/secrets/anthropic.key`, `openai.key`, `gemini.key`; directory
0700, files 0600) - by the dashboard, or from a shell with `printf '%s' "$KEY" | bin/secret-set anthropic.key`. They never pass a
command line (visible to other local users in `/proc`), never go into the repository or a log.

Inside the worker the key is a read-only file mount; `worker/af-agent` puts it into the agent's environment only. The agent
itself can read its own environment - that is unavoidable for any agent that calls an API - so:
- the egress proxy lets the worker reach only that provider's API host, so the key cannot be sent anywhere else;
- the gate refuses any branch whose diff contains key- or token-like strings (Anthropic, OpenAI, Google, GitHub, GitLab, AWS,
  Slack, private keys), so it cannot leak through the code either;
- use a key made for Agent-Forge, with a spend limit set at the provider (Anthropic workspaces, OpenAI projects, Google Cloud
  budgets), and rotate it if you ever suspect it leaked.

## Money
API runtimes cost real money, so they have hard limits (`etc/limits.conf`, also in Settings → API workers):

| setting | default | what it does |
|---|---|---|
| `API_RUN_BUDGET_USD` | 2.0 | cap per run. Claude Code enforces it itself (`--max-budget-usd`); codex / gemini runs are stopped by `RUN_TOKEN_CAP` and `RUN_MAX_TOOL_CALLS` |
| `API_DAILY_BUDGET_USD` | 10.0 | cap per day (UTC) for all projects; `bin/dispatch` refuses API runs once spent + one run's cap would exceed it |
| `RUN_MAX_TOOL_CALLS` | 250 | any runtime: a run that makes more tool calls is stopped like at the token cap (its work is saved) |
| `PRICE_CODEX`, `PRICE_GEMINI` | see file | USD per million tokens (input, cached input, output) for the cost estimate - those CLIs report tokens, not money. Set them to your model's prices |

`API_WORKERS_POLICY` (per project): `overflow` (default) gives API workers a task only when no subscription worker can take it -
the subscriptions are spent first; `always` uses API workers first. A run that stops because the key was rejected (wrong,
revoked, no credit) re-queues its task without counting an attempt and pauses that worker for an hour; you get an `auth`
notification. Rate limits and quota stops are handled like an Antigravity quota stop.

Spend shows up in the dashboard (pill "API today", Settings → API workers) and in Metrics (cost per merged task by runtime and
model).

## Models
`WORKER_MODEL_CLAUDE` (default `sonnet`), `WORKER_MODEL_CODEX`, `WORKER_MODEL_GEMINI` (empty = the CLI's default). Set Codex's
model explicitly (e.g. `gpt-5.1-codex` or the current Codex model): in tests, Codex offered no tools for a default model it had
no metadata for.

## What runs where, exactly
`bin/run-supervisor` starts the CLI inside the worker with the prompt on stdin:

```
claude -p --output-format stream-json --verbose --dangerously-skip-permissions --model M --max-budget-usd B [--resume ID]
codex exec --json --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check <quiet flags> -m M [resume ID] -
gemini --output-format stream-json --yolo --skip-trust -m M [--resume ID] -p "Do the task described above."
```

Skipping the CLIs' own permission prompts and sandboxes is intended: the container is the sandbox (no capabilities, internal
network only, egress allow-list, CPU/RAM/PID caps), and an unattended run has nobody to answer prompts. Codex's quiet flags
switch off update checks, analytics, feedback, plugins, apps, model discovery, browser and computer use - with them, a logging
proxy saw Codex contact only `api.openai.com` (without them also `chatgpt.com` and `api.github.com`). Claude Code runs with
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, Gemini CLI with usage statistics and auto-update off; each then contacts only its
API host.

Event streams (verified against the real CLIs - claude 2.1.293, codex 0.161.0, gemini 0.63.0 - running against a fake API
server, `tests/test_runtimes.sh`) are read by `bin/lib/runtimes.py`: tokens, tool calls, the conversation id (used to resume a
rework in the same conversation), the result and its cost. The dashboard shows each runtime's live transcript.

## Versions
`worker/Dockerfile.api` pins Node.js (SHA-256 checked) and the three CLIs. To upgrade: change the versions, rebuild
(`docker compose -f worker/docker-compose.api.yml build`), run `tests/test_runtimes.sh`, then `bin/worker-up N` for each
API worker.
