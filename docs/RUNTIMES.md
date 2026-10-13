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
| `goose` | Block's Goose CLI agent on your model server | `bin/worker-add N --runtime goose` | configured provider host only |
| `qwen-code` | Alibaba DashScope API key, per token | `bin/worker-add N --runtime qwen-code` | `dashscope.aliyuncs.com`, `dashscope-intl.aliyuncs.com` |
| `anthropic-compat` | your own model server (nothing, for a local one) - Claude Code CLI over an Anthropic-compatible API | `bin/worker-add N --runtime anthropic-compat` | that server only |

The dashboard does the same: Settings → "+ Add worker" → pick the runtime; for an API runtime it asks for the key once.

- `agy`: **Cost / quota model**: quota (tracked via `agy /usage`, not USD).
- `claude`: **Cost / quota model**: metered (a real USD estimate is shown on the Money page).
- `codex`: **Cost / quota model**: metered (a real USD estimate is shown on the Money page).
- `gemini`: **Cost / quota model**: metered (a real USD estimate is shown on the Money page).


## Checking readiness

`bin/doctor` now prints, for every runtime, one of three states:
- `ok` ('Ready'): fully configured and ready to use.
- `missing_setting`: a required config value or secret is not set.
- `missing_egress`: its host is not yet in the egress allowlist.

The same summary is shown on the dashboard's Settings page, so the owner no longer has to cross-check these docs by hand to see what is configured. Point to this live check as the source of truth for current status.

## Egress host readiness

When a runtime's required egress host is missing from the egress proxy allowlist, `bin/doctor` reports an error during worker inspection (`check_runtime_egress`):

```
  ✘ [FAIL] <runtime>: missing egress host '<host>'
      → add <host> to egress/allowlist.txt, or allow it from the dashboard's Egress page
```

For example, when a `claude` worker is active but `api.anthropic.com` is missing from the egress allowlist, `bin/doctor` prints:

```
  ✘ [FAIL] claude: missing egress host 'api.anthropic.com'
      → add api.anthropic.com to egress/allowlist.txt, or allow it from the dashboard's Egress page
```

### How to fix a missing egress host

1. **Which file to edit**:
   - `bin/doctor`'s diagnostic message directs the owner to: `add <host> to egress/allowlist.txt, or allow it from the dashboard's Egress page`.
   - Both `egress/allowlist.txt` (shipped base allowlist) and `egress/allowlist.local.txt` (local additions) are loaded by `egress.allowlist()`: adding the host to either file satisfies `bin/doctor` and permits traffic through Squid.
   - Per `bin/lib/egress.py`'s `allow_host(host)` (and the dashboard's **System &rarr; Egress** page **Allow** action), the host is appended to `egress/allowlist.local.txt` so the shipped `egress/allowlist.txt` remains untouched.
   - For manual edits from the shell, you can append the host to `egress/allowlist.local.txt` (per `allow_host()` convention for local additions) or directly to `egress/allowlist.txt` (as quoted in `bin/doctor`'s output).
2. **Proxy reload**:
   - `allow_host()` (or the dashboard **Allow** button) reloads the proxy automatically via `reload_proxy()` (`squid -k reconfigure`).
   - If editing `egress/allowlist.local.txt` or `egress/allowlist.txt` manually from a shell, apply the changes by running `bin/egress-up` or invoking `reload_proxy()`.
3. **No worker restart is needed**:
   - The Squid proxy reloads its allowlists in place (`squid -k reconfigure`) without dropping active tunnels. Worker containers do not need to be restarted.

### Required egress hosts by runtime

The required egress hosts for each runtime, derived from `bin/lib/runtimes.py` `hosts(rt, endpoint_url='')`:

| runtime | required egress host(s) | notes |
|---|---|---|
| `agy` | none (`[]`) | `hosts(rt, endpoint_url='')` returns `[]`; subscription runtime. |
| `claude` | `api.anthropic.com` | Anthropic Messages API host in `RUNTIMES['claude']['hosts']`. |
| `codex` | `api.openai.com` | OpenAI API host in `RUNTIMES['codex']['hosts']`. |
| `gemini` | `generativelanguage.googleapis.com` | Google Gemini API host in `RUNTIMES['gemini']['hosts']`. |
| `compat` | derived dynamically from `COMPAT_BASE_URL` (`[]` when unset) | Endpoint runtime; `hosts()` derives the host from `COMPAT_BASE_URL`. |
| `aider` | derived dynamically from `COMPAT_BASE_URL` (`[]` when unset) | Endpoint runtime; `hosts()` derives the host from `COMPAT_BASE_URL`. |
| `opencode` | derived dynamically from `COMPAT_BASE_URL` (`[]` when unset) | Endpoint runtime; `hosts()` derives the host from `COMPAT_BASE_URL`. |
| `goose` | derived dynamically from `COMPAT_BASE_URL` (`[]` when unset) | Endpoint runtime; `hosts()` derives the host from `COMPAT_BASE_URL`. |
| `qwen-code` | `dashscope.aliyuncs.com`, `dashscope-intl.aliyuncs.com` | Alibaba Cloud DashScope API hosts in `RUNTIMES['qwen-code']['hosts']`. |
| `anthropic-compat` | derived dynamically from `COMPAT_BASE_URL` (`[]` when unset) | Endpoint runtime; `hosts()` derives the host from `COMPAT_BASE_URL`. |

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
- **Cost / quota model**: metered for `compat` and `aider` (a real USD estimate is shown on the Money page, calculated from `PRICE_COMPAT`: `0,0,0` for your own hardware, or the provider's prices for OpenRouter). The budgets below apply as for any API runtime.
- Network: a public HTTPS server is added to the allow-list; a server on your network or without TLS gets an exception for
  exactly its address and port (see SECURITY.md). A proxy created before this feature needs `bin/egress-up` once - `bin/doctor`
  says so.
- Nothing else is contacted: Aider would download litellm's price table and tokenizers at run time - the image ships the
  tokenizers and `af-agent` gives Aider the model's metadata from a local file. `tests/test_runtimes.sh` routes every other
  connection to a logging proxy and fails if a CLI tries one.

## Anthropic-compatible endpoint
An Anthropic-compatible endpoint connects worker containers running Claude Code CLI against your own model server, proxy, or provider implementing the Anthropic Messages API (e.g. LiteLLM, Cloudflare AI Gateway, OpenRouter, AWS Bedrock proxy, or local models).

- **Setup**: Add workers with `bin/worker-add N --runtime anthropic-compat` (or in Settings → "+ Add worker" → Anthropic-compatible endpoint). Set `COMPAT_BASE_URL` (Settings → Workers, or `etc/limits.conf`) to the server's Anthropic-compatible base URL.
- **Endpoint and Network**: The owner pins the endpoint host via Settings > System > Egress exactly like `compat` (or allow the host using `bin/egress-up` / `allow_host`). The egress proxy restricts outbound network traffic to that configured endpoint host only.
- **Credentials**: Needs an API key secret (e.g. `compat.key`), set the same way as other API runtimes: in the dashboard under Settings → Workers, or from a shell with `printf '%s' "$KEY" | bin/secret-set compat.key`. For an unauthenticated local server, an empty key or local configuration is supported.
- **Cost / quota model**: unknown (Agent-Forge cannot estimate cost; Anthropic-compat points to an external or custom model server whose pricing is not set in Agent-Forge).

## OpenCode
OpenCode (SST's OpenCode CLI) runs inside the worker container using the standard agy worker image (`sandbox-worker:1.2.11`, `AGY_IMAGE`). It is provider-agnostic, connecting to whichever AI model provider or local server the owner configures (Anthropic, OpenAI, OpenRouter, Ollama, etc.).

- **Setup**: Add workers with `bin/worker-add N --runtime opencode` (or in Settings → "+ Add worker" → OpenCode). Configure the model server or provider endpoint in `COMPAT_BASE_URL` (Settings → Workers, or `etc/limits.conf`). Its model is now configurable from the dashboard's Settings page (key `OPENCODE_MODEL`) and its readiness is shown in the same Settings page readiness summary as Claude Code/Codex/Gemini.
- **Endpoint and Network**: OpenCode is endpoint-based with no fixed host; `hosts()` derives the destination host dynamically from `COMPAT_BASE_URL`. The owner must pin the provider host via Settings > System > Egress before use (or allow the host using `bin/egress-up` / `allow_host`). The egress proxy restricts outbound network traffic to that configured endpoint host only.
- **Invocation**: `bin/run-supervisor` starts OpenCode non-interactively with `opencode run --format json` (using `OPENCODE_QUIET` flags). The task prompt is passed on stdin rather than in argv, keeping prompts and sensitive data out of `/proc/*/cmdline`. A model can be passed via `--model`, and a rework continues the existing session with `--session <conversation_id>`.
- **Cost / quota model**: unknown (Agent-Forge cannot estimate cost; OpenCode's backend model and its price are set in the user's own OpenCode config, not in Agent-Forge).
- **Testing**: Tested offline against `tests/fixtures/fake-opencode-server.js` with zero real network calls in `tests/test_runtime_opencode.sh`.

## Goose
Goose (Block's open-source CLI agent, `goose`) runs inside the worker container using the standard worker image (`sandbox-worker:1.2.11`, `AGY_IMAGE`). It is provider-agnostic, connecting to whichever AI model provider or local model server the owner configures (Ollama, vLLM, LM Studio, OpenRouter, or other OpenAI-compatible endpoints).

- **Setup**: Add workers with `bin/worker-add N --runtime goose` (or in Settings → "+ Add worker" → Goose). Configure the model server or provider endpoint in `COMPAT_BASE_URL` (Settings → Workers, or `etc/limits.conf`). Its model is now configurable from the dashboard's Settings page (key `GOOSE_MODEL`) and its readiness is shown in the same Settings page readiness summary as Claude Code/Codex/Gemini.
- **Endpoint and Network**: Goose is provider-agnostic and endpoint-based with no fixed host; `hosts()` derives the destination host dynamically from `COMPAT_BASE_URL`. The owner must pin the provider host via Settings > System > Egress before use (or allow the host using `bin/egress-up` / `allow_host`). The egress proxy restricts outbound network traffic to that configured endpoint host only.
- **Environment and Config**: Goose configures its target model, provider, and server endpoint through environment variables (such as `GOOSE_PROVIDER`, `GOOSE_MODEL`, and provider base URLs / keys) or via its configuration file (`~/.config/goose/config.yaml`), pointing at the configured endpoint.
- **Invocation**: `bin/run-supervisor` starts Goose non-interactively using `goose run --instructions -` (using `GOOSE_QUIET` flags). The task prompt is supplied on stdin (per Goose CLI documentation) rather than in argv, preventing sensitive instructions and task data from leaking in `/proc/*/cmdline`.
- **Cost / quota model**: unknown (Agent-Forge cannot estimate cost; Goose's provider and model pricing are configured in the user's own Goose environment, not in Agent-Forge).
- **Testing**: Tested offline against `tests/fixtures/fake-goose-server.js` with zero real network calls in `tests/test_runtime_goose.sh`.

## Qwen Code
Qwen Code runs Alibaba's Qwen Code CLI (`qwen`) inside the worker container using the standard worker image (`sandbox-worker:1.2.11`, `AGY_IMAGE`) to interact with Alibaba Cloud DashScope models.

- **Setup**: Add workers with `bin/worker-add N --runtime qwen-code` (or in Settings → "+ Add worker" → Qwen Code). The worker requires a DashScope API key secret (`qwen.key`), configured via `printf '%s' "$KEY" | bin/secret-set qwen.key` or in Settings → Workers, which is mounted into the container as a read-only secret. Its model is now configurable from the dashboard's Settings page (key `QWEN_MODEL`) and its readiness is shown in the same Settings page readiness summary as Claude Code/Codex/Gemini.
- **Egress and Network**: Egress is allowed via pinned entries in `egress/allowlist.txt` (`dashscope.aliyuncs.com` and `dashscope-intl.aliyuncs.com`) rather than through the dynamic endpoint mechanism. The egress proxy restricts outbound network traffic to these verified DashScope API hosts only.
- **Invocation**: `bin/run-supervisor` starts the CLI non-interactively using `qwen --output-format stream-json --yolo` (the `QWEN_QUIET` flags) with the prompt supplied on stdin and `-p "Do the task described above."` (`QWEN_TRAILER`) appended. This keeps prompts and sensitive instructions out of `/proc/*/cmdline`. A rework continues the existing conversation with `--resume <conversation_id>`, and an optional model is passed with `-m <model>`.
- **Cost / quota model**: unknown (Agent-Forge cannot estimate cost; Qwen Code talks to Alibaba DashScope whose model pricing is not tracked in Agent-Forge).

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
goose run --instructions -
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
