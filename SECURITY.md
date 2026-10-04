# Security Architecture & Threat Model

**Agent-Forge** is designed from the ground up for safe, autonomous multi-agent software development. Unlike naive agent frameworks that run untrusted AI-generated code directly on the host with open internet access, Agent-Forge implements defense-in-depth isolation across five strict layers.

---

## 1. Five-Layer Defense Model

```
[ Host System / Production Network ]
             │
             ▼
  ┌──────────────────────────────────────────────┐
  │ 1. Virtual Machine / Host Boundary           │
  │    Unprivileged execution, strict firewalls  │
  └──────────────────────┬───────────────────────┘
                         │
                         ▼
  ┌──────────────────────────────────────────────┐
  │ 2. Isolated Docker Bridge Network            │
  │    Internal-only bridge; NO default gateway  │
  └──────────────┬───────────────────────────────┘
                 │
        ┌────────┴────────┐
        ▼                 ▼
 ┌──────────────┐  ┌──────────────┐
 │ Worker-1     │  │ Worker-N     │  ◄── 3. Container Isolation:
 │ Non-root     │  │ Non-root     │      cap_drop: ALL
 │ PIDs: 1024   │  │ PIDs: 1024   │      no-new-privileges: true
 └──────┬───────┘  └──────┬───────┘      NO docker.sock mount
        │                 │
        ▼                 ▼
 ┌──────────────────────────────────────────────┐
 │ 4. Egress Whitelisting Proxy (Squid)         │
 │    CONNECT :443 exact hostname matching      │
 │    Google / Anthropic / OpenAI APIs ONLY     │
 └──────────────────────────────────────────────┘
                         │
                         ▼
 ┌──────────────────────────────────────────────┐
 │ 5. Ephemeral Gate Containers                 │
 │    Clean pytest runs before PR merges        │
 └──────────────────────────────────────────────┘
```

---

## 2. Core Security Controls

### Layer 1: Container Hardening
- **Unprivileged User:** Workers run exclusively under non-root UID 1000 (`worker`).
- **Capability Dropping:** All Linux capabilities are dropped (`cap_drop: ["ALL"]`).
- **Privilege Escalation Prevention:** Enforces `no-new-privileges: true`.
- **Process & Resource Caps:** Hard CPU and memory quotas with a strict `pids_limit: 1024` to thwart fork bombs.
- **Docker Socket Isolation:** `/var/run/docker.sock` is **NEVER** mounted inside worker containers. Workers cannot control or escape into the host container engine.

### Layer 2: Egress Proxy & Network Whitelisting
Workers reside on an internal Docker bridge (`workers_internal`) that has no default internet route.
- Outbound traffic must traverse the Squid egress proxy (`egress-proxy:3128`).
- Squid enforces exact-match destination hostnames (`allowlist.txt`). Wildcard domains are prohibited.
- Only official model inference endpoints (Anthropic, OpenAI, Google) and package registries (PyPI, npm) are allowed.
- Outbound requests to private RFC-1918 subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) or localhost are strictly denied (HTTP 403) and log alarms.

### Layer 3: Secret Hygiene & Keyring Management
- Tokens and API keys are stored in `~/.config/sandbox/secrets/` with `0600` permissions.
- Inside worker containers, secrets are mounted **read-only** (`:ro`) at `/run/secrets/`.
- Agents never have permission or capability to modify host credentials.
- Headless sessions use transient D-Bus sessions and private GNOME Keyrings to prevent credential leakage between containers.

### Layer 4: Independent Clean-Container Gates
AI code is **never** merged directly into the `main` branch:
1. Workers commit changes to isolated feature branches (`feat/NNNN-*`).
2. When a worker completes a task, the orchestrator triggers `bin/review-branch`.
3. A **fresh, ephemeral test container** clones the repository, checks out the branch, and executes the test suite from scratch.
4. If the gate fails or tests are manipulated, the branch is rejected immediately.

### Layer 5: Tamper-Evident Audit Ledger
All execution telemetry is logged to `runs/ledger.jsonl`:
- Records run duration, token consumption, exit codes, tool invocation counts, and egress alerts.
- A cryptographic high-water mark monitor (`bin/audit-rotate`) detects if audit logs are truncated or deleted.
- Repeated non-zero exits automatically trigger the **hardware killswitch** (`bin/killswitch on`), pausing the system before any damage can occur.
