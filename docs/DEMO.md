# Docker-Only Demo Mode

Agent-Forge provides a Docker-only demo mode that lets anyone evaluate the full system in five minutes. It requires only Docker and Docker Compose—no cloud virtual machine, no AI provider accounts or API keys, no secrets, and no host Python installation.

This document contains two parts:
1. **[Part 1: Design Specification](#part-1-design-specification)** (~60%) — Architecture, services, environment variables, component contracts, and invariants for contributors.
2. **[Part 2: User Guide](#part-2-user-guide)** (~40%) — Task-based walkthrough for running and exploring the demo on macOS, Linux, and Windows (WSL2), followed by instructions for moving to a real install.

---

# Part 1: Design Specification

## 1. Objective and Architecture

The demo mode simulates a fully populated Agent-Forge installation working on the `tasks` CLI demo project ([examples/demo/vision.md](../examples/demo/vision.md)). It executes offline using mock worker transcripts, pre-baked team state, and an internal Git forge.

The target implementation packages all components into a single Docker Compose manifest at `examples/demo/docker-compose.yml` (do not create this file yet; this section specifies its design).

```
 ┌────────────────────────────────────────────────────────────────────────┐
 │                      docker-compose.yml (DEMO=1)                       │
 │                                                                        │
 │  ┌───────────────────────┐          ┌───────────────────────────────┐  │
 │  │       dashboard       │◄─────────┤         foreman-stub          │  │
 │  │    (web/server.py)    │   reads  │  replays tools/demo_state.py  │  │
 │  │    port 8171 (HTTP)   │   state  │   advances ticks & events     │  │
 │  └───────────┬───────────┘          └───────────────┬───────────────┘  │
 │              │ queries                              │                  │
 │              ▼ worker quota & ps                    ▼ mock git PRs     │
 │  ┌───────────────────────┐          ┌───────────────────────────────┐  │
 │  │      worker-stub      │          │            forgejo            │  │
 │  │  emits stream.jsonl   │          │    codeberg.org/forgejo       │  │
 │  │  fake-docker usage    │          │    port 3000 (HTTP / git)     │  │
 │  └───────────────────────┘          └───────────────────────────────┘  │
 └────────────────────────────────────────────────────────────────────────┘
```

### Services

1. **`dashboard` (`web/server.py`)**:
   - Runs the standard Python standard-library HTTP server and Server-Sent Events (SSE) dispatcher (`/api/stream`).
   - Serves the static dashboard UI (`web/static/`) on port `8171`.
   - Reads project configuration, running goals, task states, timeline runs, and metrics from the shared state volume.
   - Discovers workers and queries subscription quota through the `worker-stub` environment without accessing the host Docker daemon.

2. **`foreman-stub` (Foreman Emulator)**:
   - On container startup, populates the shared volume with the realistic demo dataset from `tools/demo_state.py` (3 active/completed goals, 15 tasks across all lifecycle states, 24 hours of worker ledger runs, and owner questions).
   - Executes an offline simulation loop. Instead of calling external LLMs (Claude Code, Gemini, or Anthropic APIs), it replays pre-recorded planning steps and advances task states on simulated ticks.
   - Writes periodic updates to `runs/foreman/live.json`, `events.jsonl`, and `llm.jsonl`.

3. **`worker-stub` (Fake Worker Agent)**:
   - Simulates active workers (`worker-1` through `worker-4`).
   - Provides container metadata matching `tools/fake-docker`: reports `worker-1`..`worker-3` running `agy` (Antigravity) and `worker-4` running `claude` with an API key.
   - Emits streaming JSON lines into active run directories (`runs/<run_id>/stream.jsonl`) simulating real-time agent output (code edits, pytest invocations, tool results).
   - Responds to quota inquiries (`docker exec worker-N agy-run -p /usage`) with realistic remaining percentages parsed from `.demo-usage`.

4. **`forgejo` (Internal Git Forge)**:
   - Runs rootless Forgejo (`codeberg.org/forgejo/forgejo:16.0.5-rootless`) with SQLite.
   - Listens on port `3000` (HTTP) and port `2222` (SSH).
   - Automatically bootstrapped with site admin `forge-admin`, service bots `orchestrator` and `monitor`, and team `workers`.
   - Houses the demo repository `acme/tasks-cli` pre-seeded with initial commits, branches (`feat/0004-due-store`, `feat/0005-due-cli`, `test/0007-date-tests`), and open pull requests.

### Networking and Storage
- **Network**: All demo containers connect to an internal Docker bridge network (`demo_internal`). No external network bridges or egress proxies are created.
- **Volume**: A shared named volume `demo-state` mounted at `/af` holds `runs/`, `etc/`, `projects/`, and `.demo`.

---

## 2. Environment Variables and Component Contracts

The master environment variable `DEMO=1` signals all Agent-Forge components to operate in mock/demo mode.

### Configuration Variables

| Variable | Default (Demo) | Purpose |
|---|---|---|
| `DEMO` | `1` | Master toggle switching components into demo replay mode. |
| `FORGE_DIR` | `/af` | Absolute path to the shared Agent-Forge workspace directory. |
| `BIND_PORT` | `8171` | Host port mapped to the web dashboard. |
| `FORGE_HTTP_PORT` | `3000` | Host port mapped to Forgejo web and git interface. |
| `FORGE_URL` | `http://forgejo:3000` | URL used by services to communicate with Forgejo. |
| `DASHBOARD_HOSTS` | `localhost,127.0.0.1` | Permitted Host headers for dashboard requests. |
| `DASHBOARD_TOKEN` | *(empty)* | Disables mandatory token authentication for local evaluation. |

### Component Contracts (Behavior when `DEMO=1`)

#### 1. Web Dashboard (`web/server.py` and `web/dashboard/`)
When `DEMO=1`:
- **Local mode enforcement**: Disables SSH-based remote tunnels (`VM_IP=""`). All operations target local container paths under `FORGE_DIR`.
- **Systemd bypass**: Disables systemctl calls (`systemctl restart agent-forge-foreman`). Service restart commands return instant simulated success.
- **Fake Docker discovery**: Replaces host Docker socket calls (`/var/run/docker.sock`) with the stub command handler from `tools/fake-docker`. Discovers workers `worker-1`..`worker-4` and reads quota from `/af/.demo-usage`.
- **Host access**: Pre-populates `DASHBOARD_HOSTS` with `localhost,127.0.0.1` and skips token cookie requirements if `DASHBOARD_TOKEN` is unset.
- **Mutating actions**: Actions triggered in the UI (approving a plan, pausing a goal, sending feedback) update local JSON state files in `runs/foreman/` without invoking live LLM CLI tools.

#### 2. Foreman Stub (`bin/foreman` and `bin/lib/foreman/`)
When `DEMO=1`:
- **Safety check**: Checks for the existence of `/af/.demo`. Refuses execution if `runs/ledger.jsonl` exists without `.demo` to prevent touching real installations.
- **No LLM calls**: Bypasses `PLAN_PROVIDERS` and `REVIEW_PROVIDERS` in `bin/lib/foreman/llm.py`. Replaces calls to Claude Code, Antigravity, or paid API endpoints with deterministic mock responses.
- **Deterministic state replay**: Uses `tools/demo_state.py` to seed tasks, diffs, metrics, and ledger runs.
- **Simulation loop**: On every loop tick (`bin/foreman tick`), advances timestamps in `live.json`, appends simulated events to `events.jsonl`, and updates task progress.
- **Local forge only**: Merges pull requests against the internal Forgejo instance; never attempts external synchronization with GitHub or GitLab.

#### 3. Sandbox and Dispatch (`bin/lib/sbx.py`)
When `DEMO=1`:
- **No container spawning**: Bypasses `bin/dispatch` Docker container instantiation. Workers do not launch real Docker sandboxes.
- **Mock secrets**: Reads static demo tokens directly from `/af/etc/` or environment variables without requiring host `~/.config/sandbox/secrets`.
- **Test mode compliance**: Sets `SANDBOX_TEST=1` to enforce isolated file paths and protect system configuration.

#### 4. Fake Worker Agent (`worker/entrypoint.sh` / `tools/fake-docker`)
When `DEMO=1`:
- **Transcript streaming**: Streams pre-generated agent interaction events into `runs/<run_id>/stream.jsonl` with simulated delays, matching both Antigravity JSON-L format and Claude Code message structures.
- **Usage reporting**: Intercepts `agy-run -p /usage` and outputs the mock subscription quota table:
  ```tsv
  Gemini Models	Five Hour Limit Remaining	72%	Resets in 2h
  Gemini Models	Weekly Limit Remaining	61%	Resets in 4d
  ```

---

## 3. One-Command Invocation

The entire demo environment starts with a single command:

```bash
docker compose -f examples/demo/docker-compose.yml up
```

### What Happens on Launch
1. Docker Compose pulls or builds the lightweight service containers.
2. The initialization step executes `tools/demo_state.py /af` inside the shared volume.
3. Forgejo starts, initializes its SQLite database, and creates the demo repository `acme/tasks-cli`.
4. The dashboard server begins listening on `http://127.0.0.1:8171`.
5. The foreman stub initiates the replay loop.

### What the User Sees
- **Dashboard URL**: Printed in stdout logs: `http://127.0.0.1:8171`.
- **Running Fake Goal**: Goal `G20261008-062000` (*"Add due dates to tasks: sorting, overdue marks and a `today` command, with tests."*) active in the Overview tab.
- **Live Transcripts**: Worker-2 and Worker-4 visibly active in the Live tab, executing commands, editing code, and running tests.
- **Kanban Board**: 15 tasks populated across states: `done` (4), `running` (2), `gate` (1), `merging` (1), `approval` (4), `blocked` (1), and `pending` (2).
- **History and Metrics**: 24 hours of simulated worker runs, token burn charts, and model routing metrics.

---

## 4. Intentionally Absent Features

To ensure zero security exposure, zero cost, and zero host interference, the following components are intentionally absent from demo mode:

| Feature | Production Mode | Demo Mode Status | Rationale |
|---|---|---|---|
| **Real LLM Calls** | Dispatches prompts to Claude Code, Antigravity, OpenAI, or Gemini | **Completely Absent** | Guarantees $0 cost and runs with zero credentials or accounts. |
| **External Git Pushes** | Pushes to GitHub or GitLab via API tokens | **Completely Absent** | All git operations remain local to the internal Forgejo container. |
| **Egress Proxy (`egress/`)** | Squid forward proxy with iptables domain filtering | **Completely Absent** | Eliminates host networking dependencies and `CAP_NET_ADMIN` privileges. |
| **Docker Socket Mount** | Mounts `/var/run/docker.sock` to manage containers | **Completely Absent** | Prevents container escape risks; runs safely in restricted environments. |
| **Host Secrets** | Stored in `~/.config/sandbox/secrets` (0600) | **Completely Absent** | Uses ephemeral mock tokens; leaves no trace on the host filesystem. |

---

# Part 2: User Guide

## 1. Prerequisites

Before running the demo, ensure Docker and Docker Compose are installed and running on your system.

### Operating System Requirements

- **macOS**:
  - macOS 12+ (Apple Silicon or Intel).
  - Install **Docker Desktop for Mac**. Ensure Docker Desktop is running.
  - Verify in Terminal: `docker compose version`
- **Linux**:
  - Ubuntu 20.04+, Debian 11+, Fedora 36+, or any modern distribution.
  - Install **Docker Engine** and **Docker Compose plugin** (`docker-compose-v2`).
  - Ensure your user belongs to the `docker` group (`sudo usermod -aG docker $USER`), then log out and back in.
  - Verify in terminal: `docker compose version` (must run without `sudo`).
- **Windows (WSL2)**:
  - Windows 10 or 11 with WSL2 enabled.
  - Install **Docker Desktop for Windows** with the **WSL2 backend** enabled (Settings → General → Use the WSL 2 based engine, and Settings → Resources → WSL Integration → enable your distro).
  - Open your WSL2 terminal (e.g. Ubuntu) and verify: `docker compose version`

### Hardware Requirements
- **CPU**: 2 vCPUs minimum.
- **RAM**: 2 GB available memory.
- **Disk**: 2 GB free space.

---

## 2. Running the Demo Step by Step

### Step 1: Clone the Repository
```bash
git clone https://github.com/agent-forge/agent-forge.git
cd agent-forge
```

### Step 2: Start Demo Mode
Run the single compose command from the repository root:
```bash
docker compose -f examples/demo/docker-compose.yml up
```

Expected output:
```text
[+] Running 4/4
 ✔ Network demo_internal        Created
 ✔ Container demo-forgejo-1     Started
 ✔ Container demo-foreman-1     Started
 ✔ Container demo-dashboard-1   Started
demo-dashboard-1  | Agent-Forge web server listening on http://127.0.0.1:8171
```

### Step 3: Open the Dashboard
Open your web browser and navigate to:
**[http://127.0.0.1:8171](http://127.0.0.1:8171)**

You will see the Agent-Forge dashboard loaded with the active demo project.

[screenshot] *Overview tab showing the active goal, team status, idle worker reasons, and backlog.*

---

## 3. The 5-Minute Tour: Three Things to Click

To understand how Agent-Forge works, explore these three key areas of the dashboard:

### 1. The Active Goal and Acceptance Criteria (Overview Tab)
Navigate to **Overview** and click on the active goal **`G20261008-062000`**:
*"Add due dates to tasks: sorting, overdue marks and a `today` command, with tests."*

What to observe:
- **Parallel Task Breakdown**: Notice how the orchestrator split this high-level goal into parallel tasks:
  - `0004-due-store` (backend-developer: store due dates and sort)
  - `0005-due-cli` (backend-developer: `--due` option and overdue marks)
  - `0006-today` (backend-developer: `tasks today` command)
  - `0007-date-tests` (tester: edge cases and date handling)
- **Observable Acceptance Criteria**: Scroll to the acceptance criteria section. Agent-Forge does not close goals based on agent claims; it requires observable test evidence for each criterion before marking a goal complete.

[screenshot] *Goal detail view displaying the parallel task breakdown, criteria checks, and execution cost.*

### 2. Live Transcripts and Finished Replay (Live Tab)
Click the **Live** tab in the top navigation bar.

What to observe:
- **Concurrent Workers**: See `worker-2` and `worker-4` executing tasks in parallel.
- **Live Tool Invocations**: Watch the live streaming transcript as agents read context packs, edit files (`tasks/cli.py`), run test suites (`pytest tests/test_cli.py`), and commit code.
- **Run Replay**: Click on any finished run in the left sidebar (or navigate to run `0004-due-store`). You can scrub through the entire step-by-step history of prompts, tool calls, bash executions, and outputs.

[screenshot] *Live tab displaying concurrent streaming agent transcripts with real-time tool calls.*

### 3. Dependency Graph, Approvals, and Insights (Tasks & Insights Tabs)
Click the **Tasks** tab, then toggle to the **Graph** view (`tab=tasks&view=graph`).

What to observe:
- **Critical Path Dependency Graph**: Notice how `0005-due-cli` depends on `0004-due-store`. The foreman automatically holds dependent tasks until prerequisite branches pass the offline gate and merge into main.
- **Owner Approval Queue**: Notice tasks marked `approval` (such as `0010-overdue-color` awaiting merge approval and `0011-import-check` awaiting risky plan approval). You choose what the team can merge autonomously and what requires your sign-off.
- **Insights (Money & Quota)**: Switch to the **Insights** tab and select **Money**. Inspect the spend breakdown per model, per worker, and per day, alongside remaining Antigravity subscription quotas.

[screenshot] *Dependency graph highlighting the critical path alongside the Insights quota and cost overview.*

---

## 4. Stopping and Cleaning Up

To stop the demo:
- In the terminal where docker-compose is running, press `Ctrl+C`.
- Or run from another terminal:
  ```bash
  docker compose -f examples/demo/docker-compose.yml down
  ```

To wipe the demo containers and reset the demo volume state completely:
```bash
docker compose -f examples/demo/docker-compose.yml down -v
```

---

## 5. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `bind: address already in use` (port 8171) | Another local service is using port 8171. | Set a custom port: `BIND_PORT=8182 docker compose -f examples/demo/docker-compose.yml up` and visit `http://127.0.0.1:8182`. |
| `bind: address already in use` (port 3000) | Another service (such as Gitea/Grafana) is using port 3000. | Set a custom forge port: `FORGE_HTTP_PORT=3002 docker compose -f examples/demo/docker-compose.yml up`. |
| `Cannot connect to the Docker daemon` | Docker daemon is stopped or not responding. | Start Docker Desktop, or run `sudo systemctl start docker` on Linux. |
| WSL2 cannot reach `http://127.0.0.1:8171` | Windows port forwarding is blocked or inactive. | Ensure `localhostForwarding=true` in `~/.wslconfig`, or browse to your WSL2 IP address (`ip addr show eth0`). |
| Permission denied creating files in volume | File permissions conflict on the mounted volume. | Ensure your user has write access to the checkout directory: `chmod -R u+rw .`. |
| Dashboard displays "not loaded yet" | Services are still completing initial state seeding. | Wait 5–10 seconds for the foreman stub to complete initial JSON state generation. |

---

## 6. Going from Demo to a Real Install

When you are ready to deploy real AI agents that write, test, review, and merge code on real repositories, move from demo mode to a production installation.

Follow the full setup guide in **[docs/SETUP.md](SETUP.md)**.

### What Changes in a Real Install

1. **Dedicated Host Machine**:
   - Production runs on a dedicated Ubuntu 22.04+ VM (4+ vCPU, 8+ GB RAM) to safely isolate worker containers ([docs/SETUP.md](SETUP.md)).

2. **Network Egress Proxy**:
   - A dedicated Squid proxy container (`bin/egress-up`) locks worker containers into an internal network (`workers_internal`) with no direct internet route. Outbound requests are restricted to approved model provider hosts.

3. **Real Agent Runtimes**:
   - Workers use real AI models: Google Antigravity subscription accounts, Anthropic Claude Code, OpenAI Codex, Gemini CLI, or self-hosted Ollama/vLLM endpoints ([docs/RUNTIMES.md](RUNTIMES.md)).

4. **Production Code Repositories**:
   - Integrates with your real GitHub or GitLab repositories under branch protection rules ([docs/UPSTREAM.md](UPSTREAM.md)), or persists repositories in production Forgejo.

5. **24/7 Autonomous Autopilot**:
   - Production systemd background services (`agent-forge-foreman` and `agent-forge-web`) run continuously, autonomously pulling new goals from your product vision ([examples/demo/vision.md](../examples/demo/vision.md) or `docs/vision.md`) whenever workers are idle.
