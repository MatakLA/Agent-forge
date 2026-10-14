# Setup

This guide walks you through setting up Agent-Forge from scratch on a clean Ubuntu virtual machine in approximately 30 minutes.

## Prerequisites

Ensure your host machine meets the following minimum requirements before starting:

- **Operating System:** Ubuntu 22.04 LTS or newer (Debian-based distributions with `apt`). Use a dedicated virtual machine with non-production test data.
- **Hardware:**
  - 4 vCPU minimum
  - 8 GB RAM minimum
  - 40 GB available disk space minimum
- **User Privileges:** Standard non-root user with `sudo` access.
- **Open Ports and Network Access:**
  - Port `3000`: Forgejo internal Git server web interface and REST API (`http://127.0.0.1:3000`).
  - Port `8171`: Agent-Forge web dashboard and admin console (`http://127.0.0.1:8171`).
  - Egress Port `3128`: Internal Squid egress proxy (`127.0.0.1:3128`) mediating all outbound traffic from worker containers.

For security, the dashboard and Forgejo services bind to `127.0.0.1` on the VM. Access them from your local machine using an SSH tunnel:

```bash
ssh -L 8171:127.0.0.1:8171 -L 3000:127.0.0.1:3000 <user>@<vm-host>
```

## Install in one command

Clone the repository and run the automated installer:

```bash
git clone <repository-url> agent-forge && cd agent-forge && bash scripts/install.sh
```

To install and immediately configure the demo vision project from `examples/demo/vision.md`, append `--demo`:

```bash
bash scripts/install.sh --demo
```

If bootstrapping directly from a script:

```bash
curl -fsSL <install-url> | bash
```

### What the installer does

The installer runs sequentially, skipping steps that have already completed:

1. **Docker installation:** Verifies Docker Engine and the `docker` user group via `scripts/vm-baseline-ubuntu.sh`. Installs updates, base tools (`git`, `jq`, `tmux`, `python3-venv`), registers the official Docker repository with GPG key validation, and checks for Python 3.10+ and Git 2.31+.
2. **Configuration initialization:** Creates `etc/` with mode `0700`. Copies template files (`etc/limits.example.conf`, `etc/foreman.example.conf`, `etc/forge.example.conf`, `etc/team-rules.example.md`) to active configurations with mode `0600`. Initializes the secrets directory (`~/.config/sandbox/secrets`) with mode `0700`.
3. **Egress proxy and network:** Builds and starts the Squid egress proxy container (`egress-proxy`) and creates the isolated Docker network `workers_internal` via `bin/egress-up`.
4. **Internal Forgejo Git service:** Launches the Forgejo rootless container on port 3000 via `scripts/forge-bootstrap`, creates service accounts (`forge-admin`, `orchestrator`, `monitor`), initializes the default repository, creates the `workers` team, and configures branch protection on `main`.
5. **Worker container image:** Inspects or builds the default worker container image (`sandbox-worker:1.2.11`).
6. **Self-tests:** Runs `python3 bin/foreman --selftest` to verify core orchestrator functionality.
7. **Systemd services:** Installs and enables `agent-forge-foreman.service` (the autonomous loop) and `agent-forge-web.service` (dashboard server on port 8171) via `sudo scripts/install-services`.
8. **Health check:** Runs `python3 bin/doctor --quick` to confirm the installation state.

### Expected duration

The installation takes 15 to 25 minutes depending on VM network speed and image build times.

### Verify completion

Run `bin/doctor` to verify all components:

```bash
bin/doctor
```

## Choose your AI

Agent-Forge separates high-level planning and code review (orchestrator) from hands-on coding (workers). Review the available worker runtimes below to select the option that matches your subscriptions, API keys, or infrastructure:

| Runtime | What you need |
|---|---|
| `agy` (Antigravity) | Google account with Antigravity / Gemini subscription (authenticate once via browser login) |
| `claude` (Claude Code) | Anthropic API key (stored in `~/.config/sandbox/secrets/anthropic.key`) |
| `gemini` (Gemini CLI) | Google Gemini API key (stored in `~/.config/sandbox/secrets/gemini.key`) |
| `anthropic-compat` | Anthropic Messages-compatible model server endpoint (`COMPAT_BASE_URL`) and API key |
| `opencode` | SST OpenCode provider endpoint (`COMPAT_BASE_URL`) and model configuration |
| `qwen-code` | Alibaba Cloud DashScope API key (stored in `~/.config/sandbox/secrets/qwen.key`) |
| `goose` | Model server endpoint (`COMPAT_BASE_URL`) supporting OpenAI-compatible or custom providers |

For detailed comparisons, decision flowcharts, trade-offs, and cost models across runtimes and orchestrator providers, see [docs/CHOOSING-AN-AI.md](CHOOSING-AN-AI.md).

## Your first goal

Follow these steps to run your first automated task:

1. **Open the dashboard:** Navigate to `http://127.0.0.1:8171` in your browser (forward port 8171 over SSH if accessing a remote VM).
2. **Add a worker:**
   - In the dashboard, click **Settings** → **Workers** → **+ Add worker**.
   - Select your chosen runtime (for example, `agy` for Antigravity or `claude` for Claude Code).
   - Alternatively, add a worker from the terminal:
     ```bash
     bin/worker-add 1
     ```
     For API-based runtimes, specify the runtime name:
     ```bash
     bin/worker-add 1 --runtime claude
     ```
   - If using Antigravity (`agy`), authenticate the worker container with your Google account:
     ```bash
     docker exec -it worker-1 agy-run
     ```
     Open the provided authentication URL in your browser, log in, paste the verification code into the prompt, and enter `/exit`.
   - Verify the worker answers:
     ```bash
     docker exec worker-1 agy-run -p "reply ok" --print-timeout 2m
     ```
   - If using an API runtime, set the API key:
     ```bash
     printf '%s' "<your-api-key>" | bin/secret-set anthropic.key
     ```
3. **Paste a test goal:**
   - Refer to `examples/demo/vision.md` for an example product vision: a lightweight command-line task manager written in Python.
   - In the dashboard, navigate to **Overview** and click **New goal**.
   - Paste a test goal prompt:
     ```text
     Create a command-line task manager 'tasks' that stores tasks in a JSON file, with add/list/done commands and pytest tests
     ```
   - Alternatively, submit the goal from the terminal:
     ```bash
     bin/foreman goal "Create a command-line task manager 'tasks' that stores tasks in a JSON file, with add/list/done commands and pytest tests"
     ```
4. **Watch it run:**
   - In the dashboard, monitor the progress on the **Live** tab (real-time worker stream) and the **Tasks** tab (dependency graph and status).
   - Alternatively, check the queue status from the terminal:
     ```bash
     bin/foreman status
     ```

## Verify it worked

### Doctor output meaning

Run `bin/doctor` to inspect the full deployment state:

```bash
bin/doctor
```

The doctor command checks each subsystem:
- **Tools:** Validates required versions of Python (3.10+), Git (2.31+), Docker Engine, Docker Compose plugin, and CLI binaries.
- **Configuration:** Verifies `etc/limits.conf`, `etc/foreman.conf`, the vision document, and notification webhooks.
- **Secrets:** Verifies permissions on `~/.config/sandbox/secrets` (`0700`) and secret tokens (`0600`).
- **Forgejo:** Verifies HTTP health on port 3000 and authenticated access to the project repository.
- **Sandbox:** Verifies Docker network `workers_internal`, Squid proxy health on port 3128, worker container image, and kill switch status.
- **Workers:** Verifies container states, model endpoints, API keys, and remaining subscription quotas.
- **Foreman:** Verifies heartbeat timestamp in `runs/foreman/live.json` and available disk space.

### Status indicators: green, yellow, and red

- **Green (`✔`):** The check passed completely. The subsystem is functioning as expected. Exit code is `0`.
- **Yellow (`!`):** Warning condition. Non-fatal configuration issue (for example, quota running low, missing optional webhook notification, or worker paused). The orchestrator loop continues running. Exit code is `1`.
- **Red (`✘`):** Fatal error. Critical failure preventing automated execution (for example, Docker daemon inactive, Forgejo unreachable, invalid file permissions, or dead foreman service). Exit code is `2`.

### Tail the foreman log

To monitor foreman orchestration events in real time:

```bash
journalctl -u agent-forge-foreman.service -f
```

To tail raw event stream entries from the working directory:

```bash
tail -f runs/foreman/events.jsonl
```

## Common problems

### 1. Docker not running

- **Symptom:** `bin/doctor` outputs `✘ docker does not answer`, or the install script fails at the Docker check.
- **Cause:** The Docker daemon is not active, or your current user account has not reloaded group memberships after being added to the `docker` group.
- **Fix:** Enable and start Docker, ensure your user belongs to the `docker` group, and activate the group session:
  ```bash
  sudo systemctl enable --now docker
  sudo usermod -aG docker $USER
  newgrp docker
  ```

### 2. Port 3000 already in use

- **Symptom:** Forgejo fails to start, or `scripts/forge-bootstrap` exits with an address bind error on port `3000`.
- **Cause:** Another process or web server on the VM is already listening on `127.0.0.1:3000`.
- **Fix:** Identify the conflicting process, terminate it, or rebind Forgejo to an alternate port using `FORGE_HTTP_PORT`:
  ```bash
  sudo ss -tulpn | grep 3000
  export FORGE_HTTP_PORT=3001
  scripts/forge-bootstrap
  ```

### 3. Quota exhausted

- **Symptom:** `bin/foreman status` reports `dispatch refused: quota low`, or `bin/doctor` flags that worker quota is below threshold.
- **Cause:** The AI model subscription quota (Antigravity/Gemini) or the configured daily budget limit (`API_DAILY_BUDGET_USD` in `etc/limits.conf`) is exhausted.
- **Fix:** Inspect the remaining quota and reset window:
  ```bash
  docker exec worker-1 agy-run -p /usage
  ```
  Wait for the provider quota reset window, or raise the daily budget cap in `etc/limits.conf`:
  ```bash
  sed -i 's/^API_DAILY_BUDGET_USD=.*/API_DAILY_BUDGET_USD=20.0/' etc/limits.conf
  ```

### 4. Egress proxy blocking a host

- **Symptom:** Worker runs fail during network calls, `bin/doctor` reports `✘ egress allow-list lacks <hostname>`, or proxy logs show HTTP 403 Forbidden responses.
- **Cause:** Outbound network requests to the target provider or repository host are blocked by Squid egress enforcement.
- **Fix:** Add the required domain to `egress/allowlist.local.txt` and reload the proxy:
  ```bash
  echo "<allowed-hostname>" >> egress/allowlist.local.txt
  bin/egress-up
  ```

### 5. Worker stays idle

- **Symptom:** Worker containers show `running` in `docker ps`, but pending tasks remain unassigned and no work starts.
- **Cause:** The kill switch is active, tasks share locked files with other running tasks, task prerequisites are incomplete, or the worker container crashed and requires restart.
- **Fix:** Check foreman status and kill switch state:
  ```bash
  bin/foreman status
  bin/killswitch status
  ```
  If the kill switch tripped, inspect the egress report, acknowledge it, and turn the switch off:
  ```bash
  bin/egress-report
  bin/egress-report --ack && bin/killswitch off --start
  ```
  If the worker container is stopped or uninitialized, start it:
  ```bash
  bin/worker-up 1
  ```

## Next steps

- Read [ARCHITECTURE.md](../ARCHITECTURE.md) to understand the system architecture, sandbox isolation, and verification pipeline.
- Read [docs/RUNTIMES.md](RUNTIMES.md) for worker runtime setup, credentials, and cost models.
- Read [docs/PROVIDERS.md](PROVIDERS.md) for orchestrator provider configuration and planning models.
- Read [docs/CHOOSING-AN-AI.md](CHOOSING-AN-AI.md) for detailed AI model selection criteria.
- Read [CONTRIBUTING.md](../CONTRIBUTING.md) for contribution guidelines, testing standards, and licensing.
- Read [README.md](../README.md) and [SECURITY.md](../SECURITY.md) for general information and security considerations.
