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
| `opencode` | SST's OpenCode CLI on your configured provider / subscription | not yet - no worker image ships its CLI | configured provider host only |
| `goose` | Block's Goose CLI agent on your model server | not yet - no worker image ships its CLI | configured provider host only |
| `qwen-code` | an Alibaba DashScope API key | not yet - no worker image ships its CLI | `dashscope.aliyuncs.com`, `dashscope-intl.aliyuncs.com` |
| `anthropic-compat` | your own model server (nothing, for a local one) - Claude Code CLI over an Anthropic-compatible API | not yet - no worker image ships its CLI | that server only |
| `copilot` | a GitHub Copilot subscription | not yet - no worker image ships its CLI | `api.githubcopilot.com`, `api.github.com` |
| `mistral-vibe` | a Mistral Vibe subscription or API key | not yet - no worker image ships its CLI | `api.mistral.ai` |
| `ollama` | unmetered - depends on your own hardware (local OpenAI-compatible server, no API key) | not yet - use `compat` or `aider` for this server | none needed - traffic stays local/LAN (no public egress allowlist entry) |
| `vllm` | unmetered - depends on your own hardware (local OpenAI-compatible server, no API key) | not yet - use `compat` or `aider` for this server | none needed - traffic stays local/LAN (no public egress allowlist entry) |
| `lmstudio` | unmetered - depends on your own hardware (local OpenAI-compatible server, no API key) | not yet - use `compat` or `aider` for this server | none needed - traffic stays local/LAN (no public egress allowlist entry) |
| `llamacpp` | unmetered - depends on your own hardware (local OpenAI-compatible server, no API key) | not yet - use `compat` or `aider` for this server | none needed - traffic stays local/LAN (no public egress allowlist entry) |

**What you can add today.** `bin/worker-add` (and the dashboard's "Add a worker", which lists exactly what the server reports as creatable) accepts `agy`, `claude`, `codex`, `gemini`, `compat` and `aider`. The other rows are registered - cost model, egress hosts, readiness and `bin/doctor` checks exist - but no worker image ships their CLI yet, so `bin/worker-add` refuses them and the dashboard does not offer them (their model settings are under Settings → Advanced). For a model server of your own (Ollama, vLLM, LM Studio, llama.cpp) use `compat` or `aider` today - see [Your own model server](#your-own-model-server-ollama-vllm-lm-studio-openrouter).

The dashboard does the same: Settings → Workers → "Add a worker" → pick the runtime; for an API runtime it asks for the key once.

- `agy`: **Cost / quota model**: quota (tracked via `agy /usage`, not USD).
- `claude`: **Cost / quota model**: metered (a real USD estimate is shown on the Money page).
- `codex`: **Cost / quota model**: metered (a real USD estimate is shown on the Money page).
- `gemini`: **Cost / quota model**: metered (a real USD estimate is shown on the Money page).


## Several Antigravity accounts

Running more than one subscription account for one person's work may conflict with the provider's terms. Agent-Forge supports it
for testing and evaluation; it does not encourage pooling accounts to get around limits. For regular work use API keys (paid per
token, with the caps in [Money](#money)) or a single subscription. Read the terms of any provider whose CLI you automate.

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
| `copilot` | `api.githubcopilot.com`, `api.github.com` | Public API hosts for GitHub Copilot CLI in `egress/allowlist.txt`. |
| `mistral-vibe` | `api.mistral.ai` | Public API host for Mistral Vibe CLI in `egress/allowlist.txt`. |
| `ollama` | none needed (traffic stays local/LAN) | Local OpenAI-compatible server; no public egress allowlist entry needed. |
| `vllm` | none needed (traffic stays local/LAN) | Local OpenAI-compatible server; no public egress allowlist entry needed. |
| `lmstudio` | none needed (traffic stays local/LAN) | Local OpenAI-compatible server; no public egress allowlist entry needed. |
| `llamacpp` | none needed (traffic stays local/LAN) | Local OpenAI-compatible server; no public egress allowlist entry needed. |

## Your own model server: Ollama, vLLM, LM Studio, OpenRouter
Set `COMPAT_BASE_URL` (Settings → Runtimes, or `etc/limits.conf`) to the server's OpenAI-compatible base URL, then add workers
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

- **Setup**: Not creatable yet: no worker image ships this CLI, so `bin/worker-add` refuses it and the dashboard does not offer it (see the note under the table at the top of this page). Set `COMPAT_BASE_URL` (Settings → Runtimes, or `etc/limits.conf`) to the server's Anthropic-compatible base URL.
- **Endpoint and Network**: The owner pins the endpoint host via System → Egress exactly like `compat` (or allow the host using `bin/egress-up` / `allow_host`). The egress proxy restricts outbound network traffic to that configured endpoint host only.
- **Credentials**: Needs an API key secret (e.g. `compat.key`), set the same way as other API runtimes: in the dashboard under Settings → Runtimes, or from a shell with `printf '%s' "$KEY" | bin/secret-set compat.key`. For an unauthenticated local server, an empty key or local configuration is supported.
- **Cost / quota model**: unknown (Agent-Forge cannot estimate cost; Anthropic-compat points to an external or custom model server whose pricing is not set in Agent-Forge).

## OpenCode
OpenCode (SST's OpenCode CLI) runs inside the worker container using the standard agy worker image (`sandbox-worker:1.2.11`, `AGY_IMAGE`). It is provider-agnostic, connecting to whichever AI model provider or local server the owner configures (Anthropic, OpenAI, OpenRouter, Ollama, etc.).

- **Setup**: Not creatable yet: no worker image ships this CLI, so `bin/worker-add` refuses it and the dashboard does not offer it (see the note under the table at the top of this page). Configure the model server or provider endpoint in `COMPAT_BASE_URL` (Settings → Runtimes, or `etc/limits.conf`). No API key secret is required in Agent-Forge. Its model is configurable from the dashboard's Settings page (key `OPENCODE_MODEL`) and its readiness is shown in the Settings → Runtimes.
- **Egress and Network**: OpenCode is endpoint-based with no fixed host; `hosts()` derives the destination host dynamically from `COMPAT_BASE_URL`. The owner must allow the provider host via System → Egress before use (or allow the host using `bin/egress-up` / `allow_host`, or configure a local endpoint in `egress/local-endpoint.conf`). The egress proxy restricts outbound network traffic to that configured endpoint host only.
- **Doctor check**: Verified by `bin/doctor` (readiness, cost model, and egress checks) and summarized in the dashboard's Settings → Runtimes. If `COMPAT_BASE_URL` is unset or invalid, `bin/doctor` reports `missing_setting` (`Runtime 'opencode' is missing required setting(s): COMPAT_BASE_URL.`); if the endpoint host is not allowed by the egress proxy, it reports `missing_egress` (`Runtime 'opencode' is missing egress allowlist for: <host>.`); when the endpoint is configured and allowed, it reports `ok` (`Runtime 'opencode' is ready.`).
- **Invocation**: `bin/run-supervisor` starts OpenCode non-interactively with `opencode run --format json` (using `OPENCODE_QUIET` flags). The task prompt is passed on stdin rather than in argv, keeping prompts and sensitive data out of `/proc/*/cmdline`. A model can be passed via `--model`, and a rework continues the existing session with `--session <conversation_id>`.
- **Cost / quota model**: unknown (Agent-Forge cannot estimate cost; OpenCode's backend model and its price are set in the user's own OpenCode config, not in Agent-Forge; `tokens: None`).
- **Testing**: Tested offline against `tests/fixtures/fake-opencode-server.js` with zero real network calls in `tests/test_runtime_opencode.sh`.

## Goose
Goose (Block's open-source CLI agent, `goose`) runs inside the worker container using the standard worker image (`sandbox-worker:1.2.11`, `AGY_IMAGE`). It is provider-agnostic, connecting to whichever AI model provider or local model server the owner configures (Ollama, vLLM, LM Studio, OpenRouter, or other OpenAI-compatible endpoints).

- **Setup**: Not creatable yet: no worker image ships this CLI, so `bin/worker-add` refuses it and the dashboard does not offer it (see the note under the table at the top of this page). Configure the model server or provider endpoint in `COMPAT_BASE_URL` (Settings → Runtimes, or `etc/limits.conf`). No API key secret is required in Agent-Forge. Its model is configurable from the dashboard's Settings page (key `GOOSE_MODEL`) and its readiness is shown in the Settings → Runtimes.
- **Egress and Network**: Goose is provider-agnostic and endpoint-based with no fixed host; `hosts()` derives the destination host dynamically from `COMPAT_BASE_URL`. The owner must allow the provider host via System → Egress before use (or allow the host using `bin/egress-up` / `allow_host`, or configure a local endpoint in `egress/local-endpoint.conf`). The egress proxy restricts outbound network traffic to that configured endpoint host only.
- **Environment and Config**: Goose configures its target model, provider, and server endpoint through environment variables (such as `GOOSE_PROVIDER`, `GOOSE_MODEL`, and provider base URLs / keys) or via its configuration file (`~/.config/goose/config.yaml`), pointing at the configured endpoint.
- **Doctor check**: Verified by `bin/doctor` (readiness, cost model, and egress checks) and summarized in the dashboard's Settings → Runtimes. If `COMPAT_BASE_URL` is unset or invalid, `bin/doctor` reports `missing_setting` (`Runtime 'goose' is missing required setting(s): COMPAT_BASE_URL.`); if the endpoint host is not allowed by the egress proxy, it reports `missing_egress` (`Runtime 'goose' is missing egress allowlist for: <host>.`); when the endpoint is configured and allowed, it reports `ok` (`Runtime 'goose' is ready.`).
- **Invocation**: `bin/run-supervisor` starts Goose non-interactively using `goose run --instructions -` (using `GOOSE_QUIET` flags). The task prompt is supplied on stdin (per Goose CLI documentation) rather than in argv, preventing sensitive instructions and task data from leaking in `/proc/*/cmdline`.
- **Cost / quota model**: unknown (Agent-Forge cannot estimate cost; Goose's provider and model pricing are configured in the user's own Goose environment, not in Agent-Forge; `tokens: None`).
- **Testing**: Tested offline against `tests/fixtures/fake-goose-server.js` with zero real network calls in `tests/test_runtime_goose.sh`.

## Qwen Code
Qwen Code runs Alibaba's Qwen Code CLI (`qwen`) inside the worker container using the standard worker image (`sandbox-worker:1.2.11`, `AGY_IMAGE`) to interact with Alibaba Cloud DashScope models.

- **Setup**: Not creatable yet: no worker image ships this CLI, so `bin/worker-add` refuses it and the dashboard does not offer it (see the note under the table at the top of this page). The worker requires a DashScope API key secret (`qwen.key`), configured via `printf '%s' "$KEY" | bin/secret-set qwen.key` or in Settings → Workers, which is mounted into the container as a read-only secret (readiness also recognizes `DASHSCOPE_API_KEY` or `QWEN_API_KEY` environment variables). Its model is configurable from the dashboard's Settings page (key `QWEN_MODEL`) and its readiness is shown in the Settings → Runtimes.
- **Egress and Network**: Requires public API hosts `dashscope.aliyuncs.com` and `dashscope-intl.aliyuncs.com` in `egress/allowlist.txt` rather than through the dynamic endpoint mechanism. The egress proxy restricts outbound network traffic to these verified DashScope API hosts only.
- **Doctor check**: Verified by `bin/doctor` (readiness, cost model, and egress checks; verifies `qwen.key` secret and DashScope egress allowlist hosts `dashscope.aliyuncs.com` and `dashscope-intl.aliyuncs.com`) and summarized in the dashboard's Settings → Runtimes. If `qwen.key` is missing, `bin/doctor` reports `missing_setting` (`Runtime 'qwen-code' is missing required setting(s): qwen.key.`); if either DashScope host is missing from `egress/allowlist.txt`, it reports `missing_egress` (`Runtime 'qwen-code' is missing egress allowlist for: ...`); when the secret is set and both hosts are allowed, it reports `ok` (`Runtime 'qwen-code' is ready.`).
- **Invocation**: `bin/run-supervisor` starts the CLI non-interactively using `qwen --output-format stream-json --yolo` (the `QWEN_QUIET` flags) with the prompt supplied on stdin and `-p "Do the task described above."` (`QWEN_TRAILER`) appended. This keeps prompts and sensitive instructions out of `/proc/*/cmdline`. A rework continues the existing conversation with `--resume <conversation_id>`, and an optional model is passed with `-m <model>`.
- **Cost / quota model**: unknown (Agent-Forge cannot estimate cost; Qwen Code (`qwen-code`) talks to Alibaba DashScope whose model pricing is not tracked in Agent-Forge).
- **Testing**: Tested offline against `tests/fixtures/fake-qwen-server.js` with zero real network calls in `tests/test_runtime_qwen.sh`.

## GitHub Copilot
GitHub Copilot runs GitHub's Copilot CLI (`copilot`) inside the worker container using the standard worker image (`sandbox-worker:1.2.11`, `AGY_IMAGE`).

- **Setup**: Not creatable yet: no worker image ships this CLI, so `bin/worker-add` refuses it and the dashboard does not offer it (see the note under the table at the top of this page). Authentication requires a GitHub account with an active Copilot subscription, authenticated via `gh` or Copilot CLI credentials mounted into the container.
- **Egress and Network**: Requires public API hosts `api.githubcopilot.com` and `api.github.com` in `egress/allowlist.txt`. The egress proxy restricts outbound network traffic to these verified GitHub Copilot API hosts only.
- **Doctor check**: Verified by `bin/doctor` (readiness and cost model checks; verifies Copilot CLI authentication and required egress hosts `api.githubcopilot.com` and `api.github.com`) and summarized in the dashboard's Settings → Runtimes.
- **Cost / quota model**: covered by a GitHub Copilot subscription rather than per-token pricing tracked in Agent-Forge.

## Mistral Vibe
Mistral Vibe runs Mistral's Vibe CLI (`mistral-vibe`) inside the worker container using the standard worker image (`sandbox-worker:1.2.11`, `AGY_IMAGE`) to execute coding tasks against Mistral AI models.

- **Setup**: Not creatable yet: no worker image ships this CLI, so `bin/worker-add` refuses it and the dashboard does not offer it (see the note under the table at the top of this page). Authentication requires a Mistral Vibe subscription or Mistral API key, configured via `bin/secret-set` or in Settings → Workers.
- **Egress and Network**: Requires public API host `api.mistral.ai` in `egress/allowlist.txt`. The egress proxy restricts outbound network traffic to this verified Mistral API host only.
- **Doctor check**: Verified by `bin/doctor` (readiness and cost model checks; verifies `mistral.key` secret and `api.mistral.ai` egress allowlist) and summarized in the dashboard's Settings → Runtimes.
- **Cost / quota model**: covered by a Mistral Vibe subscription or Mistral API key rather than per-token pricing tracked in Agent-Forge.

## Ollama
Ollama runs worker coding tasks using the Ollama runtime (`ollama`). It runs against a local OpenAI-compatible server that the owner points Agent-Forge at, requiring no API key.

- **Setup**: Not creatable yet: no worker image ships this CLI, so `bin/worker-add` refuses it and the dashboard does not offer it. For Ollama use the `compat` or `aider` runtime (see [Your own model server](#your-own-model-server-ollama-vllm-lm-studio-openrouter)). Point Agent-Forge at the server by setting `COMPAT_BASE_URL` (in Settings → Runtimes, or `etc/limits.conf`) to an address the worker containers can reach, e.g. `http://192.168.1.20:11434/v1` - not `127.0.0.1`, which inside a container is the container itself - and start Ollama with `OLLAMA_HOST=0.0.0.0`. No API key is required.
- **Egress and Network**: No public egress allowlist entry is needed because traffic stays local/LAN. The egress proxy dynamically directs traffic to the configured local endpoint host and port.
- **Doctor check**: `bin/doctor` checks whether the configured local endpoint is reachable (verifying endpoint configuration and attempting a connection to the server) and summarizes status in the doctor output and the dashboard's Settings → Runtimes.
- **Cost / quota model**: unmetered - depends on your own hardware rather than a subscription or per-token price. All model inference executes locally on your hardware without token fees or external charges.

## vLLM
vLLM runs worker coding tasks using the vLLM runtime (`vllm`). It runs against a local OpenAI-compatible server that the owner points Agent-Forge at, requiring no API key.

- **Setup**: Not creatable yet: no worker image ships this CLI, so `bin/worker-add` refuses it and the dashboard does not offer it. For vLLM use the `compat` or `aider` runtime (see [Your own model server](#your-own-model-server-ollama-vllm-lm-studio-openrouter)). Point Agent-Forge at the server by setting `COMPAT_BASE_URL` (in Settings → Runtimes, or `etc/limits.conf`) to an address the worker containers can reach, e.g. `http://gpu-box:8000/v1` - not `127.0.0.1`, which inside a container is the container itself - and start vLLM with `--host 0.0.0.0`. No API key is required.
- **Egress and Network**: No public egress allowlist entry is needed because traffic stays local/LAN. The egress proxy dynamically directs traffic to the configured local endpoint host and port.
- **Doctor check**: `bin/doctor` checks whether the configured local endpoint is reachable (verifying endpoint configuration and attempting a connection to the server) and summarizes status in the doctor output and the dashboard's Settings → Runtimes.
- **Cost / quota model**: unmetered - depends on your own hardware rather than a subscription or per-token price. All model inference executes locally on your hardware without token fees or external charges.

## LM Studio
LM Studio runs worker coding tasks using the LM Studio runtime (`lmstudio`). It runs against a local OpenAI-compatible server that the owner points Agent-Forge at, requiring no API key.

- **Setup**: Not creatable yet: no worker image ships this CLI, so `bin/worker-add` refuses it and the dashboard does not offer it. For LM Studio use the `compat` or `aider` runtime (see [Your own model server](#your-own-model-server-ollama-vllm-lm-studio-openrouter)). Point Agent-Forge at the server by setting `COMPAT_BASE_URL` (in Settings → Runtimes, or `etc/limits.conf`) to an address the worker containers can reach, e.g. `http://192.168.1.30:1234/v1` - not `127.0.0.1`, which inside a container is the container itself - and set the Local Inference Server's network binding to `0.0.0.0`. No API key is required.
- **Egress and Network**: No public egress allowlist entry is needed because traffic stays local/LAN. The egress proxy dynamically directs traffic to the configured local endpoint host and port.
- **Doctor check**: `bin/doctor` checks whether the configured local endpoint is reachable (verifying endpoint configuration and attempting a connection to the server) and summarizes status in the doctor output and the dashboard's Settings → Runtimes.
- **Cost / quota model**: unmetered - depends on your own hardware rather than a subscription or per-token price. All model inference executes locally on your hardware without token fees or external charges.

## llama.cpp
llama.cpp runs worker coding tasks using the llama.cpp runtime (`llamacpp`). It runs against a local OpenAI-compatible server that the owner points Agent-Forge at, requiring no API key.

- **Setup**: Not creatable yet: no worker image ships this CLI, so `bin/worker-add` refuses it and the dashboard does not offer it. For llama.cpp use the `compat` or `aider` runtime (see [Your own model server](#your-own-model-server-ollama-vllm-lm-studio-openrouter)). Point Agent-Forge at the server by setting `COMPAT_BASE_URL` (in Settings → Runtimes, or `etc/limits.conf`) to an address the worker containers can reach, e.g. `http://192.168.1.40:8080/v1` - not `127.0.0.1`, which inside a container is the container itself - and start `llama-server` with `--host 0.0.0.0`. No API key is required.
- **Egress and Network**: No public egress allowlist entry is needed because traffic stays local/LAN. The egress proxy dynamically directs traffic to the configured local endpoint host and port.
- **Doctor check**: `bin/doctor` checks whether the configured local endpoint is reachable (verifying endpoint configuration and attempting a connection to the server) and summarizes status in the doctor output and the dashboard's Settings → Runtimes.
- **Cost / quota model**: unmetered - depends on your own hardware rather than a subscription or per-token price. All model inference executes locally on your hardware without token fees or external charges.

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
qwen --output-format stream-json --yolo [-m M] [--resume ID] -p "Do the task described above."
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
2. `worker/Dockerfile.api`: install it pinned (an npm CLI goes into `worker/agent-clis/package.json` and its lockfile; anything else by version and checksum); `worker/af-agent`: put the key into its environment and
   switch off update checks, telemetry and anything else that calls home.
3. `tests/fixtures/fake-model-api.js` answers its API; add it to `tests/test_runtimes.sh` - the logging proxy shows every host
   it tries to reach. Then `web/dashboard/live.py` renders its events and `bin/worker-add` accepts the name.

Not included, and why: Cursor CLI (its backend hosts are not documented, so the egress allow-list cannot be pinned) and
OpenHands (it brings its own sandbox runtime). Contributions that solve that are welcome.

## Versions
`worker/Dockerfile.api` pins Node.js (SHA-256 checked), uv (hash-checked) and Aider; the three CLIs are pinned by
`worker/agent-clis/package-lock.json` and installed with `npm ci`, which refuses any tarball whose hash differs. To upgrade a CLI:
change its version in `worker/agent-clis/package.json`, run `npm install --package-lock-only` in that directory (Dependabot does the
same every month), rebuild (`docker compose -f worker/docker-compose.api.yml build`), run `tests/test_runtimes.sh`, then
`bin/worker-up N` for each API worker.
