# Orchestrator providers

The orchestrator in Agent-Forge makes three kinds of high-level decisions:
1. **Plan** (`plan`): Decomposes backlog goals into discrete, verifiable tasks, maps module dependencies, and sets acceptance criteria.
2. **Review** (`review`): Examines pull request diffs produced by workers, evaluates test compliance, and outputs strict JSON review verdicts (`approve`, `rework`, or `reject`).
3. **Ask / Verification** (`ask`): Answers user queries about project status and verifies overarching goal criteria.

## Checking readiness

`bin/doctor` now prints, for every provider, one of three states:
- `ok` ('Ready'): fully configured and ready to use.
- `missing_setting`: a required config value or secret is not set.
- `missing_egress`: its host is not yet in the egress allowlist.

The same summary is shown on the dashboard's Settings page, so the owner no longer has to cross-check these docs by hand to see what is configured. Point to this live check as the source of truth for current status.

## How provider fallback works

The orchestrator tries providers sequentially from left to right as configured in `etc/foreman.conf` until one answers successfully:

```ini
PLAN_PROVIDERS=claude-cli,agy-claude,openai-api
REVIEW_PROVIDERS=agy-claude,claude-cli,gemini-api
```

If a provider encounters an exhausted quota, missing credentials, connection timeout, or invalid JSON response, the foreman logs the failure to `runs/foreman/llm.jsonl` and immediately attempts the next provider in the chain. If every provider in the list fails, the foreman raises `Unavailable` and pauses the orchestrator loop until the configured reset cooldown expires.

> [!NOTE]
> **Orchestrator Providers vs. Worker Runtimes**
> - **Orchestrator providers** run on the host system inside the foreman process (`bin/foreman`). They perform architectural reasoning and code review.
> - **Worker runtimes** run inside isolated Docker containers (`worker-N`). They execute shell commands, edit files, and run tests.
> See [CHOOSING-AN-AI.md](CHOOSING-AN-AI.md) and [RUNTIMES.md](RUNTIMES.md) for details on worker runtimes.

---

## Supported orchestrator providers

### 1. Free with subscriptions you already have

| Provider | What it uses | Cost model | Best for | Notes |
|---|---|---|---|---|
| `claude-cli` | Claude Code CLI (`claude -p`) on host machine | Subscription (Claude Pro/Team/Enterprise) | Planning & Review | Best planner; system prompt is cached across invocations. |
| `agy-claude` | Claude (`AGY_PLAN_MODEL` / `AGY_REVIEW_MODEL`) via Antigravity worker container | Quota ("Claude and GPT models" bucket) | Review | High-fidelity reviews without per-token charges. Automatically skips exhausted worker accounts. |
| `agy-gemini` | Gemini (`AGY_FALLBACK_MODEL`) via Antigravity worker container | Quota (worker Gemini bucket) | Fallback | Free fallback; uses the same Gemini quota that workers use for coding. |

**Quota reality check.** The Antigravity "Claude and GPT models" quota bucket is small: on a 4-worker team, planning and reviews alone can exhaust it across all accounts within two days. That is why reviews try it first (it is otherwise unused) but Claude Code stays in the chain, and why workers code with Gemini rather than Claude.

**Gemini as a reviewer.** In early project telemetry, 64% of Gemini review calls produced no usable verdict before structured output constraints were introduced. Antigravity calls now pass `--json-schema` to constrain outputs, which improves consistency. Before adding `agy-gemini` or `gemini-api` as a primary reviewer, inspect your own team's telemetry in the web dashboard under **Insights &rarr; Team (orchestrator calls)**.

---

### 2. Paid per-token and self-hosted providers

Use these if you want the orchestrator queue to keep moving when subscription quotas are exhausted, or if you do not use Claude Code or Antigravity at all. These adapters (`bin/lib/providers.py`) run in the foreman process directly on the host machine.

| Provider | Configuration keys | Credential key | Endpoint / Network |
|---|---|---|---|
| [`openai-api`](#orchestrator-provider-openai-api) | `OPENAI_MODEL`, optional `OPENAI_API_BASE` | `OPENAI_API_KEY` | `api.openai.com`, `openrouter.ai`, or local HTTP model server |
| [`gemini-api`](#orchestrator-provider-gemini-api) | `GEMINI_MODEL` | `GEMINI_API_KEY` | `generativelanguage.googleapis.com` |
| `anthropic-api` | `ANTHROPIC_MODEL` | `ANTHROPIC_API_KEY` | `api.anthropic.com` |

---

## Orchestrator provider: openai-api

The `openai-api` adapter connects the foreman orchestrator (`bin/lib/foreman/llm.py`) directly to any OpenAI-compatible Chat Completions HTTP endpoint (`/chat/completions`) using the standard library implementation in `bin/lib/providers.py` (`call_openai_api()`). It can execute architectural planning (`kind="plan"`) and code review decisions (`kind="review"`) against official OpenAI endpoints (`api.openai.com`), aggregation routers (such as OpenRouter), or self-hosted local model servers (such as Ollama, vLLM, LM Studio, or llama.cpp).

### Setup

To point the orchestrator at an OpenAI-compatible provider:

1. **Provider chain (`PLAN_PROVIDERS` / `REVIEW_PROVIDERS`)**:
   Add `openai-api` to the comma-separated provider lists in `etc/foreman.conf` (or `project.conf` for a specific project), or configure it in the web dashboard under **Settings &rarr; Models** in the **Who plans (in order)** (`PLAN_PROVIDERS`) and **Who reviews (in order)** (`REVIEW_PROVIDERS`) fields:
   ```ini
   PLAN_PROVIDERS=openai-api
   REVIEW_PROVIDERS=openai-api
   ```
   If omitted, the orchestrator falls back to `ORCHESTRATOR_PROVIDER`. Configured provider tokens are validated against `bin/lib/foreman/settings.py`'s `PROVIDERS_ALL` list (`claude-cli`, `agy-claude`, `agy-gemini`, `anthropic-api`, `openai-api`, `gemini-api`).

2. **Model setting field (`OPENAI_MODEL`)**:
   Specify the model identifier in `etc/foreman.conf` (or `project.conf`), or enter it in the dashboard under **Settings &rarr; Models** in the **OpenAI-compatible model (paid)** field:
   ```ini
   OPENAI_MODEL=gpt-4o
   ```
   For OpenRouter, provide the full repository model string (e.g. `anthropic/claude-3.5-sonnet`). For local servers, supply the model tag (e.g. `qwen2.5-coder:32b`). In `bin/lib/foreman/settings.py`, `OPENAI_MODEL` cannot be blank when `openai-api` is configured in `PLAN_PROVIDERS` or `REVIEW_PROVIDERS`.

3. **Base URL setting field (`OPENAI_API_BASE`)**:
   Specify the HTTP endpoint in `etc/foreman.conf` (or `project.conf`), or set it in the dashboard under **Settings &rarr; Models** in the **OpenAI API base URL** field:
   ```ini
   OPENAI_API_BASE=https://api.openai.com/v1
   ```
   - Direct OpenAI: defaults to `https://api.openai.com/v1` if empty or omitted.
   - OpenRouter: `https://openrouter.ai/api/v1`
   - Local model server: e.g. `http://192.168.1.50:11434/v1` (Ollama) or `http://192.168.1.50:8000/v1` (vLLM).

4. **API key configuration (`bin/secret-set`)**:
   Set the API key securely on the host using `bin/secret-set`:
   ```bash
   printf '%s' "sk-YOUR_API_KEY" | bin/secret-set openai.key
   ```
   `bin/secret-set` reads the secret value strictly from standard input (preventing exposure in shell history or `/proc/*/cmdline`) and writes it with mode `0600` into `~/.config/sandbox/secrets/openai.key` within a mode `0700` secrets directory.
   
   To enable the host orchestrator process (`bin/lib/providers.py`) to access this key without file duplication, create a symbolic link:
   ```bash
   ln -sf openai.key ~/.config/sandbox/secrets/OPENAI_API_KEY
   ```
   Alternatively, export the key in the environment (`export OPENAI_API_KEY="sk-YOUR_API_KEY"`) or store it directly at `~/.config/sandbox/secrets/OPENAI_API_KEY` with permissions mode `0600`.
   
   > [!IMPORTANT]
   > **Credential storage invariants**:
   > API keys must **never** be placed in `etc/foreman.conf`, `project.conf`, or committed to git. In the web dashboard under **Settings &rarr; Models**, the key setting (`OPENAI_API_KEY`) is strictly file-only: it displays whether `openai.key` exists on disk, but never accepts or edits raw tokens in the browser. In `bin/lib/foreman/settings.py`, `SECRET_KEY_RE` (`r"(?!.*\.\.)[A-Za-z0-9_.*•-]{1,128}"`) validates secret references, `mask_secret()` masks raw values with `***`, and `mask_log_value()` ensures secrets are never recorded in settings event logs.

### Cost and quota caveats

- **Paid-per-token API with no quota window**: Unlike Antigravity providers (`agy-claude` and `agy-gemini`), which draw from subscription worker quota pools with recurring quota reset windows and cooldown tracking, `openai-api` is a paid-per-token API. Every planning and review request consumes tokens immediately against the target account or server. There is no quota window or automatic reset cooldown.
- **Honest cost tracking**: For self-hosted model servers or third-party OpenAI-compatible endpoints, the cost per call recorded in `runs/foreman/llm.jsonl` and displayed in the dashboard is recorded as **unknown** (`cost_usd: null`) unless the installation configures explicit per-token pricing (such as `PRICE_CODEX` or `PRICE_COMPAT` in `etc/limits.conf`). In `bin/lib/providers.py`, `call_openai_api()` parses token metrics from the API's `usage` response (`prompt_tokens`, `completion_tokens`, `total_tokens`), but does not calculate or return a `cost_usd` figure.

### Limitations

1. **Schema validation (`REVIEW_SCHEMA` and `OBJECT_SCHEMA`)**:
   All responses received by `bin/lib/foreman/llm.py` are strictly validated using `parse_and_validate()`:
   - **Code reviews (`kind="review"`)**: Must strictly comply with `REVIEW_SCHEMA`. The output must be a JSON object with a mandatory `"verdict"` property whose value is restricted to enum `["approve", "rework", "reject"]` and a mandatory string property `"feedback"`. Optional properties are `"lesson"` (string) and `"mutations"` (array of objects with required fields `"name"`, `"file"`, `"old"`, `"new"`).
   - **Plans (`kind="plan"`)**: Must comply with `OBJECT_SCHEMA` (`{"type": "object"}`).
   If the model emits conversational preamble, markdown backtick fences wrapping malformed text, non-conforming verdict strings (such as `"pass"` or `"looks good"`), or omits required fields, validation fails (`None`). The orchestrator treats this as a provider failure and cascades to the next provider in `PLAN_PROVIDERS` / `REVIEW_PROVIDERS` or raises `Unavailable`.
2. **Base URL validation (`validate_url()`)**:
   `OPENAI_API_BASE` is strictly validated by `validate_url()` in `bin/lib/foreman/settings.py`:
   - **Scheme**: Must be `http://` or `https://`.
   - **Hostname**: Must match regex `^[A-Za-z0-9.-]+$` and cannot start or end with a dot (`.`).
   - **Port**: Optional; if present, must be an integer between 1 and 65535 (`1 <= port <= 65535`).
   - **Path**: Optional; if present, must match regex `(/[A-Za-z0-9._~/-]*)?`.
   - **Rejected inputs**: Path traversal (`..`), URL credentials (`@`), query strings (`?`), fragments (`#`), whitespace, and shell metacharacters (`\r`, `\n`, `\t`, space, `` ` ``, `$`, `;`, `|`, `&`, `<`, `>`, `'`, `"`, `\`) are strictly forbidden.
3. **Model name validation (`ORCHESTRATOR_MODEL_RE`)**:
   `OPENAI_MODEL` is validated in `bin/lib/foreman/settings.py` against `ORCHESTRATOR_MODEL_RE` (`rf"({MODEL_RE})|({SERVER_MODEL_RE})"`):
   - Standard model identifiers match `MODEL_RE = r"[A-Za-z0-9][A-Za-z0-9.\-\[\]]{0,63}"` (1 to 64 alphanumeric characters, dots, dashes, and brackets).
   - Server model identifiers match `SERVER_MODEL_RE = r"[A-Za-z0-9][A-Za-z0-9._:/@\-]{0,99}"` (1 to 100 characters starting with an alphanumeric character, allowing `.`, `_`, `:`, `/`, `@`, `-` for OpenRouter model slugs or Ollama tags).
   - `OPENAI_MODEL` cannot be blank when `openai-api` is configured in `PLAN_PROVIDERS` or `REVIEW_PROVIDERS`.

### Testing note

The `openai-api` orchestrator provider is exercised completely offline against `tests/fixtures/fake-model-api.js` via `tests/test_fake_model_api_providers.py` and `tests/test_fake_model_api_providers.sh`. The test fixture launches a local HTTP server mocking the OpenAI `/chat/completions` endpoint on port 8099. No real API key and no network connection are needed to run these tests.

---

## Orchestrator provider: gemini-api

The `gemini-api` adapter connects the foreman orchestrator (`bin/lib/foreman/llm.py`) directly to Google's official Gemini API (`:generateContent` endpoint) via the standard library adapter in `bin/lib/providers.py` (`call_gemini_api()`). It allows planning and code review to run on Google AI Studio / Gemini models (such as `gemini-1.5-pro` or `gemini-2.0-flash`) directly from the host machine without requiring Antigravity worker container logins or consuming worker Antigravity quotas.

### Setup

To point the orchestrator at the Gemini API provider:

1. **Provider chain (`PLAN_PROVIDERS` / `REVIEW_PROVIDERS`)**:
   Add `gemini-api` to `PLAN_PROVIDERS` and/or `REVIEW_PROVIDERS` in `etc/foreman.conf` (or `project.conf`), or configure it in the web dashboard under **Settings &rarr; Models** in the **Who plans (in order)** (`PLAN_PROVIDERS`) and **Who reviews (in order)** (`REVIEW_PROVIDERS`) fields:
   ```ini
   PLAN_PROVIDERS=gemini-api
   REVIEW_PROVIDERS=gemini-api
   ```
   If omitted, the orchestrator falls back to `ORCHESTRATOR_PROVIDER`. Provider tokens are validated against `PROVIDERS_ALL` in `bin/lib/foreman/settings.py`.

2. **Model setting field (`GEMINI_MODEL`)**:
   Specify the Gemini model in `etc/foreman.conf` (or `project.conf`), or enter it in the dashboard under **Settings &rarr; Models** in the **Gemini API model (paid)** field:
   ```ini
   GEMINI_MODEL=gemini-1.5-pro
   ```
   Supported models include `gemini-1.5-pro`, `gemini-2.0-flash`, and `gemini-1.5-flash`. In `bin/lib/foreman/settings.py`, `GEMINI_MODEL` cannot be blank when `gemini-api` is configured in `PLAN_PROVIDERS` or `REVIEW_PROVIDERS`.

3. **Base URL (`GEMINI_API_BASE`)**:
   The `gemini-api` adapter in `bin/lib/providers.py` connects directly to Google's official endpoint `https://generativelanguage.googleapis.com` (using path `/v1beta/models/{model}:generateContent`). The dashboard under **Settings &rarr; Models** displays this endpoint as `GEMINI_API_BASE` (`https://generativelanguage.googleapis.com`). Unlike `openai-api`, there is no custom base URL override field in `settings.py`'s `EDITABLE` dictionary.

4. **API key configuration (`bin/secret-set`)**:
   Set the Google AI Studio API key securely on the host using `bin/secret-set`:
   ```bash
   printf '%s' "AIzaSyYOUR_API_KEY" | bin/secret-set gemini.key
   ```
   `bin/secret-set` reads the secret from standard input and stores it with mode `0600` into `~/.config/sandbox/secrets/gemini.key`.
   
   To allow the host-based orchestrator adapter (`bin/lib/providers.py`) to access it, link the key:
   ```bash
   ln -sf gemini.key ~/.config/sandbox/secrets/GEMINI_API_KEY
   ```
   Alternatively, set the key as an environment variable (`export GEMINI_API_KEY="AIzaSyYOUR_API_KEY"`) or store it directly at `~/.config/sandbox/secrets/GEMINI_API_KEY` with permissions mode `0600`.
   
   > [!IMPORTANT]
   > **Credential storage invariants**:
   > Never store API keys in `etc/foreman.conf`, `project.conf`, or dashboard inputs. The dashboard's **Settings &rarr; Models** page marks `GEMINI_API_KEY` as file-only: it indicates whether `gemini.key` is set on disk, but is not editable in the browser. In `bin/lib/foreman/settings.py`, `SECRET_KEY_RE` (`r"(?!.*\.\.)[A-Za-z0-9_.*•-]{1,128}"`) validates secret references, `mask_secret()` masks raw keys with `***`, and `mask_log_value()` ensures sensitive tokens never appear in event logs.

### Cost and quota caveats

- **Paid-per-token API with no quota window**: `gemini-api` calls Google's API directly and bills per token on your Google AI Studio or Google Cloud billing account. Unlike Antigravity (`agy-gemini`), it does not draw from worker Antigravity quotas and has no quota reset window or worker cooldown timer. Every planning and review call incurs token usage immediately.
- **Honest cost tracking**: Cost per call recorded in `runs/foreman/llm.jsonl` and displayed in the dashboard is recorded as **unknown** (`cost_usd: null`) unless per-token pricing is explicitly configured (such as `PRICE_GEMINI` in `etc/limits.conf`). In `bin/lib/providers.py`, `call_gemini_api()` parses token counts from Google's `usageMetadata` object (`promptTokenCount`, `candidatesTokenCount`, `totalTokenCount`), but does not return a calculated dollar amount (`cost_usd` is not returned).

### Limitations

1. **Schema validation (`REVIEW_SCHEMA` and `OBJECT_SCHEMA`)**:
   All responses received by `bin/lib/foreman/llm.py` pass through `parse_and_validate()`:
   - **Code reviews (`kind="review"`)**: Must strictly comply with `REVIEW_SCHEMA`: JSON object containing a mandatory `"verdict"` property restricted to enum `["approve", "rework", "reject"]` and a mandatory `"feedback"` string. Optional properties are `"lesson"` (string) and `"mutations"` (array of objects with `"name"`, `"file"`, `"old"`, `"new"`).
   - **Plans (`kind="plan"`)**: Must comply with `OBJECT_SCHEMA` (`{"type": "object"}`).
   Non-conforming outputs (conversational text, markdown formatting without clean JSON, unapproved verdict strings, or missing required fields) fail validation and trigger failover to the next provider in `PLAN_PROVIDERS` / `REVIEW_PROVIDERS` or raise `Unavailable`.
2. **Model name validation (`ORCHESTRATOR_MODEL_RE`)**:
   `GEMINI_MODEL` is validated in `bin/lib/foreman/settings.py` against `ORCHESTRATOR_MODEL_RE` (`rf"({MODEL_RE})|({SERVER_MODEL_RE})"`):
   - Standard model identifiers match `MODEL_RE = r"[A-Za-z0-9][A-Za-z0-9.\-\[\]]{0,63}"` (1 to 64 alphanumeric characters, dots, dashes, and brackets).
   - Server model identifiers match `SERVER_MODEL_RE = r"[A-Za-z0-9][A-Za-z0-9._:/@\-]{0,99}"` (1 to 100 characters starting with an alphanumeric character, allowing `.`, `_`, `:`, `/`, `@`, `-`).
   - `GEMINI_MODEL` cannot be blank when `gemini-api` is configured in `PLAN_PROVIDERS` or `REVIEW_PROVIDERS`.
3. **Network egress**:
   The orchestrator connects from the host machine to `generativelanguage.googleapis.com` over HTTPS. If worker containers also contact Gemini (e.g. the `gemini` runtime), verify that `generativelanguage.googleapis.com` is present in `egress/allowlist.txt`.

### Testing note

The `gemini-api` orchestrator provider is exercised completely offline against `tests/fixtures/fake-model-api.js` via `tests/test_fake_model_api_providers.py` and `tests/test_fake_model_api_providers.sh`. The test fixture emulates the Gemini `:generateContent` endpoint locally on port 8099. No real API key and no network connection are needed to run these tests.

---

## Setup guides for non-Claude orchestrators

### Setup 1: Direct OpenAI (GPT-4o)

Use OpenAI's official API for planning and code review.

#### Prerequisites
- An active OpenAI account with API billing enabled.
- An OpenAI API key (`sk-proj-...`).

#### Step 1: Configure credentials
Store your API key securely without hardcoding it in configuration files:

```bash
# Option A: Store in secrets directory (mode 0600)
printf '%s' "sk-proj-YOUR_OPENAI_API_KEY" > ~/.config/sandbox/secrets/OPENAI_API_KEY
chmod 600 ~/.config/sandbox/secrets/OPENAI_API_KEY

# Option B: Store as an environment variable (in your shell or systemd unit)
export OPENAI_API_KEY="sk-proj-YOUR_OPENAI_API_KEY"
```

If worker containers also use OpenAI (`codex` runtime), store the key for workers via `bin/secret-set`:
```bash
printf '%s' "sk-proj-YOUR_OPENAI_API_KEY" | bin/secret-set openai.key
```
You can symlink the orchestrator key to avoid duplicating the file:
```bash
ln -sf openai.key ~/.config/sandbox/secrets/OPENAI_API_KEY
```

#### Step 2: Configure `etc/foreman.conf`
Add `openai-api` to your provider chains in `etc/foreman.conf`:

```ini
PLAN_PROVIDERS=openai-api
REVIEW_PROVIDERS=openai-api
OPENAI_MODEL=gpt-4o
# OPENAI_API_BASE defaults to https://api.openai.com/v1 if omitted
```

#### Step 3: Network and egress
The foreman runs directly on the host machine and communicates with `api.openai.com` over HTTPS. If worker containers also contact OpenAI (e.g. `codex` workers), ensure `api.openai.com` is present in `egress/allowlist.txt`.

#### Step 4: Verify setup
Run the diagnostic check:
```bash
bin/doctor --quick
```

---

### Setup 2: OpenRouter

Use OpenRouter to route orchestrator requests to hosted models (Claude, Llama 3.3, DeepSeek, Qwen).

#### Prerequisites
- An OpenRouter account and API key (`sk-or-...`).

#### Step 1: Configure credentials
```bash
# Option A: Store in secrets directory (mode 0600)
printf '%s' "sk-or-YOUR_KEY" > ~/.config/sandbox/secrets/OPENAI_API_KEY
chmod 600 ~/.config/sandbox/secrets/OPENAI_API_KEY

# Option B: Store as an environment variable
export OPENAI_API_KEY="sk-or-YOUR_KEY"
```

If worker containers also route through OpenRouter (`compat` or `aider` runtime), store `compat.key`:
```bash
printf '%s' "sk-or-YOUR_KEY" | bin/secret-set compat.key
```

#### Step 2: Configure `etc/foreman.conf`
Point `OPENAI_API_BASE` to OpenRouter's v1 endpoint:

```ini
PLAN_PROVIDERS=openai-api
REVIEW_PROVIDERS=openai-api
OPENAI_API_BASE=https://openrouter.ai/api/v1
OPENAI_MODEL=anthropic/claude-3.5-sonnet
```

#### Step 3: Network and egress
The foreman connects directly from the host. If worker containers also need to reach OpenRouter, append `openrouter.ai` to `egress/allowlist.local.txt` and reload egress:
```bash
printf '%s\n' "openrouter.ai" >> egress/allowlist.local.txt
bin/egress-up
```

#### Step 4: Verify setup
Run the diagnostic check:
```bash
bin/doctor --quick
```

---

### Setup 3: Local OpenAI-compatible model servers

Run the orchestrator completely offline at zero token cost using local inference servers.

#### Supported inference engines
- **Ollama** (v0.13+):
  - Default URL: `http://192.168.1.50:11434/v1`
  - Recommended model: `qwen2.5-coder:32b`
  - Host configuration: Ensure Ollama binds to all network interfaces by starting with `OLLAMA_HOST=0.0.0.0:11434`.
  - Context size: Set context window in Ollama (`num_ctx 32768`).
- **vLLM**:
  - Default URL: `http://192.168.1.50:8000/v1`
  - Launch command:
    ```bash
    python3 -m vllm.entrypoints.openai.api_server \
      --model Qwen/Qwen2.5-Coder-32B-Instruct \
      --host 0.0.0.0 --port 8000 \
      --max-model-len 32768
    ```
- **LM Studio** (v0.3.29+):
  - Default URL: `http://192.168.1.50:1234/v1`
  - Configuration: Enable "Local Inference Server", select model, and set network binding to `0.0.0.0`.
- **llama.cpp** (`llama-server`):
  - Default URL: `http://192.168.1.50:8080/v1`
  - Launch command:
    ```bash
    ./llama-server -m models/qwen2.5-coder-32b-instruct.Q4_K_M.gguf \
      --host 0.0.0.0 --port 8080 -c 32768
    ```

#### Step 1: Configure credentials
Local inference servers rarely validate API keys, but the adapter requires a non-empty key string:
```bash
printf '%s' "local-token" > ~/.config/sandbox/secrets/OPENAI_API_KEY
chmod 600 ~/.config/sandbox/secrets/OPENAI_API_KEY
```

For worker containers using the same local server (`compat` or `aider` runtime), store `compat.key`:
```bash
printf '%s' "local-token" | bin/secret-set compat.key
```

#### Step 2: Configure `etc/foreman.conf`
Configure the orchestrator endpoint in `etc/foreman.conf`:
```ini
PLAN_PROVIDERS=openai-api
REVIEW_PROVIDERS=openai-api
OPENAI_API_BASE=http://192.168.1.50:11434/v1
OPENAI_MODEL=qwen2.5-coder:32b
```

#### Step 3: Configure network egress for local servers
The orchestrator foreman runs directly on the host machine and accesses `OPENAI_API_BASE` directly without going through the Docker proxy.

However, if worker containers (`worker-N`) also need to reach the local server (such as `compat` or `aider` workers), note that the Agent-Forge egress proxy blocks all private IP ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) and plain HTTP traffic by default.

To grant workers access to your local model server, set `COMPAT_BASE_URL` in `etc/limits.conf` (or in the dashboard under **Settings &rarr; Workers**):
```ini
COMPAT_BASE_URL=http://192.168.1.50:11434/v1
```

When saved or applied via `bin/egress-up`, Agent-Forge automatically writes `egress/local-endpoint.conf` with a strict destination rule allowing only that host and port:
```conf
# written by Agent-Forge for COMPAT_BASE_URL - the workers may reach exactly this address and port
acl af_model_server dst 192.168.1.50
acl af_model_port port 11434
http_access allow workers af_model_server af_model_port
```
and reloads the Squid proxy.

#### Step 4: Verify connectivity
Test the endpoint directly from the command line:
```bash
curl -s http://192.168.1.50:11434/v1/models | grep -q "qwen2.5-coder" && echo "Local server reachable"
```

---

### Setup 4: Google AI Studio / Gemini API (`gemini-api`)

Use direct Google AI Studio API keys to run Gemini as the orchestrator without requiring Antigravity worker container logins.

#### Prerequisites
- A Google AI Studio API key.

#### Step 1: Configure credentials
```bash
# Option A: Store in secrets directory (mode 0600)
printf '%s' "AIzaSyYOUR_KEY" > ~/.config/sandbox/secrets/GEMINI_API_KEY
chmod 600 ~/.config/sandbox/secrets/GEMINI_API_KEY

# Option B: Store as an environment variable
export GEMINI_API_KEY="AIzaSyYOUR_KEY"
```

For worker containers running the `gemini` runtime, store the key via `bin/secret-set`:
```bash
printf '%s' "AIzaSyYOUR_KEY" | bin/secret-set gemini.key
```
You can symlink the orchestrator key to share it:
```bash
ln -sf gemini.key ~/.config/sandbox/secrets/GEMINI_API_KEY
```

#### Step 2: Configure `etc/foreman.conf`
```ini
PLAN_PROVIDERS=gemini-api
REVIEW_PROVIDERS=gemini-api
GEMINI_MODEL=gemini-1.5-pro
```
Supported models include `gemini-1.5-pro`, `gemini-2.0-flash`, and `gemini-1.5-flash`.

#### Step 3: Verify egress allowlist
The foreman connects from the host. If worker containers also run the `gemini` runtime, verify that `generativelanguage.googleapis.com` is present in `egress/allowlist.txt`.

#### Step 4: Verify setup
Run the diagnostic check:
```bash
bin/doctor --quick
```

---

## Capability and cost comparison

The following table provides an honest capability and cost comparison across orchestrator model options:

| Provider / Model Family | Context Window | Latency | JSON Schema Reliability | Token Pricing (Input / Cache / Output per 1M)* | Best Orchestrator Role |
|---|---|---|---|---|---|
| **Claude 3.5 / 3.7 Sonnet** (`claude-cli` / `anthropic-api`) | 200,000 | Moderate | **High**: Consistently adheres to JSON review schema without extra prose wrappers. | $3.00 / $0.30 / $15.00 (or $0 on Claude subscription) | **Primary Planner & Primary Reviewer** |
| **OpenAI GPT-4o** (`openai-api`) | 128,000 | Fast | **High**: Dependable JSON mode adherence at temperature 0.2; rarely produces malformed verdicts. | $2.50 / $1.25 / $10.00 | **Primary Planner & Primary Reviewer** |
| **OpenAI GPT-4o-mini** (`openai-api`) | 128,000 | Very Fast | **Moderate**: Handles simple schemas; prone to truncating complex code reviews or losing dependency chains. | $0.15 / $0.075 / $0.60 | Fallback Reviewer (too weak for multi-task planning) |
| **Gemini 1.5 Pro** (`gemini-api` / `agy-gemini`) | 2,000,000 | Moderate | **Moderate to High**: Historically variable on reviews without schema enforcement; Antigravity passes `--json-schema` (measure in dashboard). | $1.25 / $0.3125 / $5.00 (or $0 on Antigravity quota) | **Planner for large repositories** & Fallback Reviewer |
| **Gemini 2.0 Flash** (`gemini-api`) | 1,000,000 | Very Fast | **Moderate**: Fast generation, but requires explicit schema adherence prompts to prevent conversational preamble. | $0.10 / $0.025 / $0.40 | **Fast Reviewer & Rapid Triage** |
| **Qwen 2.5 Coder 32B** (Local vLLM / Ollama) | 32,768–65,536 | Hardware-dependent | **Moderate to High**: Consistently emits valid JSON at temperature 0.2 when given explicit schemas; viable for reviews. | **$0.00** (Local hardware) | **Self-hosted Planner & Reviewer** |
| **DeepSeek-Coder-V2 236B** (OpenRouter / Local) | 64,000–128,000 | Moderate | **High**: Strong code reasoning and disciplined structured JSON adherence. | $0.14 / $0.014 / $0.28 (OpenRouter) | Cost-effective Planner & Reviewer |
| **Small Models (<30B)** (e.g. 7B, 8B, 14B) | 8,192–32,768 | Fast on GPU | **Low**: Frequently emits markdown wrappers, commentary, or non-conforming verdict strings. | **$0.00** (Local hardware) | **Not recommended** (Fails orchestrator duties) |

*\* Note: Token pricing figures are approximate at the time of writing; check each provider's current pricing page since commercial rates drift over time.*

> [!TIP]
> **Measure in your own environment**: Do not rely solely on theoretical figures. Every orchestrator decision, latency duration, token count, and parse failure is recorded in `runs/foreman/llm.jsonl`. You can monitor real provider performance in the web dashboard under **Insights &rarr; Team (orchestrator calls)**.

---

## Model sizing guide: why <30B models fail

Orchestrating autonomous software agents requires significantly higher reasoning capability than code autocomplete or single-file edits.

### The two demanding tasks

1. **Plan generation (`OBJECT_SCHEMA`)**:
   - The planner must ingest the vision document, read existing directory structures, identify dependency ordering, and generate disjoint tasks with precise file ownership boundaries.
   - **Why small models (<30B) struggle**: Small models frequently hallucinate cyclic dependencies, fail to scope task boundaries to 3 files or fewer, invent nonexistent files, or omit required acceptance criteria.

2. **Code review (`REVIEW_SCHEMA`)**:
   - The reviewer inspects a unified git diff against the task prompt, verifies that code logic satisfies acceptance criteria, checks for test regressions, and outputs a strict JSON payload:
     ```json
     {
       "verdict": "approve",
       "feedback": "All requirements met and tests pass.",
       "lesson": "Optional lesson for team memory",
       "mutations": []
     }
     ```
     Allowed verdicts are strictly restricted to `"approve"`, `"rework"`, or `"reject"`.
   - **Why small models (<30B) struggle**: In practice, models under 30B parameters frequently produce unparseable or non-conforming review responses:
     - Outputting conversational filler or markdown code blocks around the JSON (e.g. `Here is the review: ```json ... ``` `), which breaks strict JSON parsers.
     - Returning non-standard verdict strings (such as `"pass"`, `"looks good"`, `"LGTM"`, or `"merged"`) instead of `"approve"`, `"rework"`, or `"reject"`.
     - Omitting mandatory schema keys (such as `verdict` or `feedback`).
     - Hallucinating false syntax or logic errors on valid diffs.

### Historical team telemetry precedent

The orchestrator review loop is unforgiving of schema deviations. In early project telemetry, 64% of Gemini review calls produced no usable verdict before structured output constraints (`--json-schema`) were enforced. Small models lacking rigorous instruction following fail in similar ways. Readers are encouraged to inspect their own logs (`runs/foreman/llm.jsonl`) and the web dashboard (Insights &rarr; Team) to measure real parse success rates before relying on any model as a sole reviewer.

### Models known to succeed

- **Commercial APIs**: Claude 3.5/3.7 Sonnet, OpenAI GPT-4o, Google Gemini 1.5 Pro, Google Gemini 2.0 Flash.
- **Self-hosted / Open-weight**:
  - **Qwen 2.5 Coder 32B-Instruct**: The minimum recommended open-weight model size for dependable orchestration.
  - **DeepSeek-Coder-V2 236B**: Outstanding code reasoning and strict JSON compliance.
  - **Llama 3.1 / 3.3 70B-Instruct**: Reliable planning and architectural review.

---

## Credential management and security invariants

Agent-Forge enforces strict credential handling to prevent token leaks:

1. **No secrets in configuration files**: `etc/foreman.conf` and `etc/limits.conf` contain model names and URLs only. Never place API keys in `.conf` files.
2. **File permissions**: All secret files stored in `~/.config/sandbox/secrets/` must have permission mode `0600` (readable and writable only by the owner), and the directory must have mode `0700`.
3. **Orchestrator provider keys vs. Worker runtime keys**:
   - The orchestrator adapters in `bin/lib/providers.py` check environment variables first (`OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`), and if unset, read from `~/.config/sandbox/secrets/<NAME>` (mode 0600).
   - Worker containers mount specific secrets based on their runtime (`openai.key` for `codex`, `gemini.key` for `gemini`, `compat.key` for `compat`/`aider`, `anthropic.key` for `claude`).
4. **Safe entry via `bin/secret-set`**: Use `bin/secret-set` to securely store worker keys from stdin without exposing tokens in shell histories, process listings (`/proc`), or command-line arguments:
   ```bash
   printf '%s' "sk-SECRET_VALUE" | bin/secret-set openai.key
   printf '%s' "AIza-SECRET_VALUE" | bin/secret-set gemini.key
   printf '%s' "compat-SECRET_VALUE" | bin/secret-set compat.key
   printf '%s' "sk-ant-SECRET_VALUE" | bin/secret-set anthropic.key
   ```
   *Accepted secret names*: `anthropic.key`, `openai.key`, `gemini.key`, `compat.key`, `github.token`, `gitlab.token`, `notify.token`.
   
   To inspect existing configured secrets without revealing secret values:
   ```bash
   bin/secret-set --list
   ```
   To share a worker secret with the orchestrator without duplicating files, create a symlink:
   ```bash
   ln -sf openai.key ~/.config/sandbox/secrets/OPENAI_API_KEY
   ln -sf gemini.key ~/.config/sandbox/secrets/GEMINI_API_KEY
   ln -sf anthropic.key ~/.config/sandbox/secrets/ANTHROPIC_API_KEY
   ```
5. **Header-only transmission**: The orchestrator HTTP adapters (`bin/lib/providers.py`) transmit keys exclusively via standard authorization headers (`Authorization: Bearer <key>` or `x-goog-api-key`), never in URL query strings.
6. **Audit checks**: Running `bin/doctor` verifies that all required secret files exist with correct file permissions:
   ```bash
   bin/doctor
   ```

---

## Troubleshooting common errors

### 1. Quota exhausted or rate limit reached (`HTTP 429`)

- **Symptoms**: `HTTP 429 Too Many Requests` or `RESOURCE_EXHAUSTED` recorded in `runs/foreman/llm.jsonl`.
- **Cause**: Hitting API requests-per-minute (RPM) or tokens-per-minute (TPM) limits on your provider tier.
- **Fix**:
  1. Add secondary fallback providers in `etc/foreman.conf` (for example, `PLAN_PROVIDERS=openai-api,gemini-api`).
  2. The foreman automatically detects rate limits and cascades to the next provider in the chain.
  3. For paid APIs, request a tier upgrade or configure a higher rate limit in your provider console.

### 2. Invalid JSON response or schema validation failure

- **Symptoms**: `Unavailable: no orchestrator provider could answer`, or review logs indicate unparseable verdicts.
- **Cause**: The model emitted conversational filler (e.g. `Here is the review: { ... }`), markdown backticks without valid JSON, or omitted the mandatory `"verdict"` field.
- **Fix**:
  1. Ensure your model is at least 32B parameters.
  2. Verify that the provider temperature is set low (the adapter uses `0.2` by default).
  3. If using local inference servers, check that the chat template matches the model's instruction format.

### 3. Context length limit exceeded (`HTTP 400`)

- **Symptoms**: `HTTP 400 Bad Request: context_length_exceeded` or `Prompt exceeds maximum context length`.
- **Cause**: A large pull request diff or long vision file exceeded the model's configured context window.
- **Fix**:
  1. For local servers (Ollama / vLLM), increase the context parameter (e.g. `num_ctx 32768` in Ollama, or `--max-model-len 32768` in vLLM).
  2. In `etc/foreman.conf`, lower `DIFF_MAX` (e.g. `DIFF_MAX=40000`) and `TASK_TOKEN_BUDGET=500000`. This prompts the planner to split large tasks before code generation.

### 4. Connection refused (`Errno 111` or `HTTP 502/503`)

- **Symptoms**: `urllib.error.URLError: <urlopen error [Errno 111] Connection refused>` or `HTTP 502 Bad Gateway`.
- **Cause**:
  - The local model server is not running or is bound only to `127.0.0.1` instead of `0.0.0.0`.
  - The port specified in `OPENAI_API_BASE` is incorrect.
  - For worker containers: the Squid egress proxy blocked access to an unlisted private IP address.
- **Fix**:
  1. Confirm the local server is listening on all interfaces:
     ```bash
     ss -tulpn | grep -E '11434|8000|1234|8080'
     ```
  2. Test local endpoint access directly from the host:
     ```bash
     curl -s http://192.168.1.50:11434/v1/models
     ```
  3. For worker containers reaching the local server, ensure `COMPAT_BASE_URL` is set in `etc/limits.conf` and run `bin/egress-up` to write `egress/local-endpoint.conf`.

---

## Observing orchestrator calls

Every orchestrator decision is recorded in `runs/foreman/llm.jsonl`. Inspect this log to verify latency, token consumption, and costs:

```bash
tail -n 20 runs/foreman/llm.jsonl | jq '{at, kind, who, rc, secs, usage}'
```

You can also view orchestrator call distribution, token spend, and failure rates in the web dashboard under **Insights &rarr; Team (orchestrator calls)**.
