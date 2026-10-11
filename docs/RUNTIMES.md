# Worker runtimes: subscriptions and API keys

Each worker is one container running one agent CLI. You can mix them in one team:

| runtime | what pays | how a worker is added | network the worker may reach |
|---|---|---|---|
| `agy` | an Antigravity (Google) subscription, one account per worker | `bin/worker-add N`, then sign it in once from the dashboard | Antigravity's hosts (`egress/allowlist.txt`) |
| `claude` | Anthropic API key, per token | `bin/worker-add N --runtime claude` | `api.anthropic.com` |
| `codex` | OpenAI API key, per token | `bin/worker-add N --runtime codex` | `api.openai.com` |
| `gemini` | Gemini API key, per token | `bin/worker-add N --runtime gemini` | `generativelanguage.googleapis.com` |
| `compat` | your own model server (nothing, for a local one) - Codex CLI over the OpenAI Responses API | `bin/worker-add N --runtime compat` | that server only |
| `aider` | your own model server - Aider over the OpenAI chat-completions API | `bin/worker-add N --runtime aider` | that server only |
| `opencode` | SST's OpenCode CLI on your configured provider / subscription | `bin/worker-add N --runtime opencode` | configured provider host only |
| `qwen-code` | Alibaba DashScope API key, per token | `bin/worker-add N --runtime qwen-code` | `dashscope.aliyuncs.com`, `dashscope-intl.aliyuncs.com` |

The dashboard does the same: Settings → "+ Add worker" → pick the runtime; for an API runtime it asks for the key once.

## Your own model server: Ollama, vLLM, LM Studio, OpenRouter
Set `COMPAT_BASE_URL` (Settings → Workers, or `etc/limits.conf`) to the server's OpenAI-compatible base URL, then add workers
with the runtime `compat` or `aider`:

| server | `COMPAT_BASE_URL` | runtime |
|---|---|---|
| Ollama on another machine | `http://192.168.1.20:11434/v1` | `compat` (Ollama 0.13+ has the Responses API) or `aider` |
| vLLM / SGLang | `http://gpu-box:8000/v1` | `compat` or `aider` |
| LM Studio | `http://192.168.1.30:1234/v1` | `compat` (0.3.29+) or `aider` |
| OpenRouter | `https://openrouter.ai/api/v1` (+ key: `bin/secret-set compat.key`) | `compat` or `aider` |
| anything with only `/v1/chat/completions` | its base URL | `aider` |

- **compat** runs Codex CLI with a custom provider. Codex speaks only the OpenAI *Responses* API (it dropped chat completions);
  most current servers have it.
- **aider** runs Aider (0.86, in its own Python 3.12 inside the image) against `/chat/completions`. Aider edits files and commits
  by itself; the supervisor checks out the task branch before the run, Aider runs the project's `TEST_CMD` after each edit and
  fixes failures, and its chat history is kept per task branch outside the repository, so a rework continues the conversation.
  Its text output becomes JSON events (`worker/af-aider-events`) for the live transcript and the token count.
- Models: `WORKER_MODEL_COMPAT` / `WORKER_MODEL_AIDER` (per project) - the name the server knows (`qwen3-coder:30b`,
  `Qwen/Qwen3-Coder-30B-A3B-Instruct`, `anthropic/claude-sonnet-4.5` on OpenRouter). A coding model with tool use and a long
  context works best; small local models do small tasks.
- Cost: `PRICE_COMPAT` (USD per million tokens, input,cached,output) - `0,0,0` for your own hardware, the provider's prices for
  OpenRouter. The budgets below apply as for any API runtime.
- Network: a public HTTPS server is added to the allow-list; a server on your network or without TLS gets an exception for
  exactly its address and port (see SECURITY.md). A proxy created before this feature needs `bin/egress-up` once - `bin/doctor`
  says so.
- Nothing else is contacted: Aider would download litellm's price table and tokenizers at run time - the image ships the
  tokenizers and `af-agent` gives Aider the model's metadata from a local file. `tests/test_runtimes.sh` routes every other
  connection to a logging proxy and fails if a CLI tries one.

## OpenCode
OpenCode (SST's OpenCode CLI) runs inside the worker container using the standard agy worker image (`sandbox-worker:1.2.11`, `AGY_IMAGE`). It is provider-agnostic, connecting to whichever AI model provider or local server the owner configures (Anthropic, OpenAI, OpenRouter, Ollama, etc.).

- **Setup**: Add workers with `bin/worker-add N --runtime opencode` (or in Settings → "+ Add worker" → OpenCode).
- **Endpoint and Network**: OpenCode is endpoint-based and uses the configurable-endpoint mechanism (`COMPAT_BASE_URL`). The owner must pin the provider host via Settings > System > Egress before use (or allow the host using `bin/egress-up` / `allow_host`). The egress proxy restricts outbound network traffic to that configured endpoint host only.
- **Invocation**: `bin/run-supervisor` starts OpenCode non-interactively with `opencode run --format json` (using `OPENCODE_QUIET` flags). The task prompt is passed on stdin rather than in argv, keeping prompts and sensitive data out of `/proc/*/cmdline`. A rework continues the existing session with `--session <conversation_id>`.
- **Cost model**: The cost model is `unknown`. OpenCode's output stream does not expose token counts, so no USD figure is computed for it (`tokens: None`, no USD spend recorded).

## Qwen Code
Qwen Code runs Alibaba's Qwen Code CLI (`qwen-code`) inside the worker container to interact with Alibaba Cloud DashScope models.

- **Setup**: Add workers with `bin/worker-add N --runtime qwen-code` (or in Settings → "+ Add worker" → Qwen Code). The owner installs and configures the Qwen Code CLI in the worker container and configures the DashScope authentication credentials (such as `DASHSCOPE_API_KEY`) so the CLI can authenticate against the model API.
- **Egress and Network**: dashscope.aliyuncs.com and dashscope-intl.aliyuncs.com are added to the allow-list; verify the exact hostname against the Qwen Code CLI version you run before relying on it in production - do not claim certainty that isn't established. The egress proxy restricts outbound network traffic to verified endpoints only.
- **Cost model**: The cost model is token-based if exposed by the CLI's usage output, else 'unknown' (reporting `tokens: None` and no USD spend if the stream does not yield token metrics).

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
API runtimes cost real money, so they have hard limits (`etc/limits.conf`, also in Settings → Money):

| setting | default | what it does |
|---|---|---|
| `API_RUN_BUDGET_USD` | 2.0 | cap per run. Claude Code enforces it itself (`--max-budget-usd`); codex / gemini runs are stopped by `RUN_TOKEN_CAP` and `RUN_MAX_TOOL_CALLS` |
| `API_DAILY_BUDGET_USD` | 10.0 | cap per day (UTC) for all projects; `bin/dispatch` refuses API runs once spent + one run's cap would exceed it |
| `API_MONTHLY_BUDGET_USD` | 0 (none) | cap per calendar month for all projects |
| `PROJECT_DAILY_BUDGET_USD`, `PROJECT_MONTHLY_BUDGET_USD` | 0 (none) | caps for one project (its own config / Settings → Money) |
| `WORKER_DAILY_BUDGET_USD` | empty | caps per worker per day, e.g. `worker-5=3,worker-6=1.5` |
| `RUN_MAX_TOOL_CALLS` | 250 | any runtime: a run that makes more tool calls is stopped like at the token cap (its work is saved) |
| `PRICE_CODEX`, `PRICE_GEMINI`, `PRICE_COMPAT` | see file | USD per million tokens (input, cached input, output) for the cost estimate - those CLIs report tokens, not money. Set them to your model's prices |

Every check assumes the worst case: the new run and each API run still running may cost a full `API_RUN_BUDGET_USD`.

`API_WORKERS_POLICY` (per project): `overflow` (default) gives API workers a task only when no subscription worker can take it -
the subscriptions are spent first; `always` uses API workers first. A run that stops because the key was rejected (wrong,
revoked, no credit) re-queues its task without counting an attempt and pauses that worker for an hour; you get an `auth`
notification. Rate limits and quota stops are handled like an Antigravity quota stop.

Spend shows up in the dashboard: the pill "API today", Insights → Money & quota (per day, runtime, project and model, the caps,
when today's budget runs out) and Insights → Experiments (cost per merged task by runtime and model).

## Models
`WORKER_MODEL_CLAUDE` (default `sonnet`), `WORKER_MODEL_CODEX`, `WORKER_MODEL_GEMINI` (empty = the CLI's default),
`WORKER_MODEL_COMPAT`, `WORKER_MODEL_AIDER` (the model name on your server). Set Codex's
model explicitly (e.g. `gpt-5.1-codex` or the current Codex model): in tests, Codex offered no tools for a default model it had
no metadata for.

## What runs where, exactly
`bin/run-supervisor` starts the CLI inside the worker with the prompt on stdin:

```
claude -p --output-format stream-json --verbose --dangerously-skip-permissions --model M --max-budget-usd B [--resume ID]
codex exec --json --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check <quiet flags> -m M [resume ID] -
gemini --output-format stream-json --yolo --skip-trust -m M [--resume ID] -p "Do the task described above."
opencode run --format json [--model M] [--session ID]
```

Skipping the CLIs' own permission prompts and sandboxes is intended: the container is the sandbox (no capabilities, internal
network only, egress allow-list, CPU/RAM/PID caps), and an unattended run has nobody to answer prompts. Codex's quiet flags
switch off update checks, analytics, feedback, plugins, apps, model discovery, browser and computer use - with them, a logging
proxy saw Codex contact only `api.openai.com` (without them also `chatgpt.com` and `api.github.com`). Claude Code runs with
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, Gemini CLI with usage statistics and auto-update off; each then contacts only its
API host.

Event streams (verified against the real CLIs - claude 2.1.293, codex 0.161.0, gemini 0.63.0, aider 0.86.2 - running against a
fake API server, `tests/test_runtimes.sh`) are read by `bin/lib/runtimes.py`: tokens, tool calls, the conversation id (used to resume a
rework in the same conversation), the result and its cost. The dashboard shows each runtime's live transcript.

## Adding a runtime
A runtime is an agent CLI that can run unattended in a container, take a prompt, edit files and run commands. To add one:
1. `bin/lib/runtimes.py`: an entry in `RUNTIMES` (label, image, key file, hosts it needs, `api`), its command line in `command()`
   (prompt on stdin if the CLI allows), and its event stream in `scan()` - tokens without cache reads, tool calls, the
   conversation id for resuming, a final result with status and cost. No machine-readable output? Wrap it like
   `worker/af-aider-events` does.
2. `worker/Dockerfile.api`: install it pinned (version and checksum); `worker/af-agent`: put the key into its environment and
   switch off update checks, telemetry and anything else that calls home.
3. `tests/fixtures/fake-model-api.js` answers its API; add it to `tests/test_runtimes.sh` - the logging proxy shows every host
   it tries to reach. Then `web/dashboard/live.py` renders its events and `bin/worker-add` accepts the name.

Not included, and why: Cursor CLI (its backend hosts are not documented, so the egress allow-list cannot be pinned) and
OpenHands (it brings its own sandbox runtime). Contributions that solve that are welcome.

## Versions
`worker/Dockerfile.api` pins Node.js (SHA-256 checked), the three CLIs, uv (hash-checked) and Aider. To upgrade: change the versions, rebuild
(`docker compose -f worker/docker-compose.api.yml build`), run `tests/test_runtimes.sh`, then `bin/worker-up N` for each
API worker.
