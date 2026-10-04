# Architecture Specification

**Agent-Forge** operates as an autonomous, multi-agent software engineering team. It translates high-level user goals into parallel work packages, dispatches tasks across isolated worker containers, enforces safety guards, runs clean-container integration gates, reviews pull requests, and merges approved code into a self-hosted Git forge.

```
                      ┌────────────────────────────┐
                      │    User / Product Owner    │
                      └─────────────┬──────────────┘
                                    │ bin/foreman goal "..."
                                    ▼
       ┌────────────────────────────────────────────────────────┐
       │             Foreman Engine (Orchestrator)              │
       │                                                        │
       │   [ Autopilot ] ──► [ Goal Planner ] ──► [ Task Queue ] │
       │          ▲                                      │      │
       │          │ (check / follow-up)                  │      │
       │   [ Review & Merge ] ◄── [ Gate Verdict ] ◄─────┤      │
       └──────────────────────────────┬─────────────────────────┘
                                      │
              ┌───────────────────────┼───────────────────────┐
              ▼                       ▼                       ▼
      ┌───────────────┐       ┌───────────────┐       ┌───────────────┐
      │   Worker 1    │       │   Worker 2    │       │   Worker N    │
      │  (Developer)  │       │   (Tester)    │       │  (Security)   │
      └───────┬───────┘       └───────┬───────┘       └───────┬───────┘
              │                       │                       │
              └───────────────────────┼───────────────────────┘
                                      │ Git push feature branch
                                      ▼
                      ┌────────────────────────────┐
                      │   Forgejo Internal Forge   │
                      │   - Protected main branch  │
                      │   - Pull Request lifecycle │
                      └────────────────────────────┘
```

---

## 1. Components & Roles

### 1.1 Foreman (`bin/foreman`)
The Foreman is a deterministic daemon running as a systemd service or background process:
- **No LLM decides timing:** Ticks every `LOOP_SEC` seconds (default: 20s).
- **LLMs are called only for three core decisions:**
  1. `plan`: Breaks a goal down into 2–5 parallel tasks with explicit roles, target files, specifications, and acceptance criteria.
  2. `review`: Evaluates clean-container gate outputs, code diffs, and mutation checks to approve, request rework, or reject.
  3. `check`: Determines whether a goal is fulfilled or requires follow-up tasks.
- **Auto-Recovery:** If a task blocks after repeated attempts, the orchestrator automatically replans the goal or gracefully closes it, preventing stuck queues.

### 1.2 Dispatcher & Supervisor (`bin/dispatch` & `bin/run-supervisor`)
- **`bin/dispatch`:** Evaluates preflight guards (one run per worker, daily token budget, quota floors, and kill switch status) before launching a task.
- **`bin/run-supervisor`:** Runs in the background for each active task:
  - Enforces hard execution timeouts (`RUN_TIMEOUT_MIN`).
  - Monitors token consumption against `RUN_TOKEN_CAP`.
  - Detects network egress anomalies.
  - Terminates container process trees cleanly upon limit violations.

### 1.3 Worker Containers (`worker/`)
- Each worker runs in an isolated container with an unprivileged Linux user (`worker`, UID 1000).
- Connected exclusively to the internal Docker network `workers_internal`.
- Outbound access is strictly routed through the local Squid proxy (`egress-proxy:3128`).
- Uses `agent-run` to launch coding agents with Gemini, Claude, or OpenAI models.

### 1.4 Egress Proxy (`egress/`)
- Intercepts all worker outbound traffic.
- Restricts external connections strictly to configured model API endpoints and package repositories.
- Blocks access to internal LAN IP addresses, preventing lateral movement or port scanning.

### 1.5 Internal Git Forge (`forgejo/`)
- Self-hosted Forgejo instance running as a rootless container.
- Main branch protection enabled: direct pushes to `main` are rejected.
- All code integration occurs via Pull Requests managed by the orchestrator bot.

---

## 2. Multi-Provider LLM Integration

Agent-Forge supports three major model families interchangeably:

| Provider | Supported Models | Recommended Use |
| :--- | :--- | :--- |
| **Anthropic / Claude** | `claude-3-7-sonnet`, `claude-3-5-sonnet`, `claude-3-5-haiku` | Strategic planning, deep architectural review, complex backend logic |
| **OpenAI / ChatGPT** | `gpt-4o`, `o1`, `o3-mini`, `gpt-4.5-preview` | Fast code generation, test authoring, code refactoring |
| **Google / Gemini** | `gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-3.1-pro` | High-throughput routines, large-context exploration, headless workers |

### Fallback Cascading
If the primary provider hits quota limits or encounters rate limits, the orchestrator automatically cascades to the next configured fallback provider, ensuring zero downtime.
