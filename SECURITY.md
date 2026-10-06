# Security model

Agent-Forge runs AI agents that execute commands without asking. The design goal: whatever a worker does - by mistake, by
prompt injection from something it read, or because the model misbehaves - it can only change its own branch, and only talk
to its model provider.

## Recommended deployment
Run Agent-Forge in a **dedicated VM** with nothing else of value in it, and give it **test data only**. Container isolation is
strong but not a VM boundary. Never put your real secrets, personal data or production credentials into the repository or the VM.

## Layers

| layer | what it stops | how |
|---|---|---|
| VM (recommended) | escape to your host / LAN | separate kernel; firewall the VM's outbound traffic on the host |
| Worker container | touching the machine | non-root, `cap_drop: ALL`, `no-new-privileges`, CPU/RAM/PID limits, no docker socket, only the internal network |
| Egress proxy | exfiltration, downloading tools, LAN scans | the internal network has no route out; Squid allows `CONNECT :443` to exact hostnames only; private/loopback/link-local/CGNAT ranges denied; every attempt logged |
| Alarms + kill switch | a misbehaving run continuing | a request to a host that is neither allowed nor known noise is a HIGH alarm → the kill switch trips and dispatch stops until a human acknowledges |
| Forgejo permissions | changing main or other branches' history | workers push branches only; `main` is protected; only the orchestrator bot merges |
| Gate | broken or faked code being merged | full test suite in a fresh, offline, read-only, non-root container; static checks for skip/xfail/assertion-free tests, secret-like strings, non-worker commits |
| Review | tests that cheat | tool-less LLM review that rejects caller detection, weakened tests, equality overrides; optional mutation tests for risky code |
| Limits | runaway cost or loops | per-run timeout and token cap, quota floors, consecutive-failure kill switch |

### The kill switch
Every project has its own kill switch (`runs/KILLSWITCH` for the default project, `runs/projects/<name>/KILLSWITCH` otherwise) and
there is one for all projects (`runs/KILLSWITCH-ALL`, `bin/killswitch --all`). While a project's switch (or the global one) is on,
nothing of that project is dispatched and its running runs are killed within ~12 s. `--hard` also stops the project's worker
containers (with `--all`: every worker and the proxy). A project's switch trips automatically on a HIGH egress alarm from one of
its workers, or after `CONSEC_FAIL_TRIP` failed runs of that project in a row. Clear it with `bin/egress-report --ack` (after
reading the report) and `bin/killswitch [--project X] off --start`, or the dashboard's "Continue" button. Treat every HIGH alarm
as a finding until you know why it happened.

### The dashboard is an admin console - keep it private
Besides showing state, the dashboard can stop/continue projects, change settings (models, worker pools, behaviour and the
global limits such as the token cap), create workers and run a worker's interactive agy login. Anyone who can reach it can do
all of that. It binds to 127.0.0.1; publish it only to yourself (SSH tunnel, Tailscale serve - never `funnel`, never a public
reverse proxy). Values are validated server-side against a fixed list of keys and formats before anything is written.

### The orchestrator cannot be steered by the code it reviews
Review and planning calls are fresh sessions **without tools** (`claude -p --tools ""`; agy without
`--dangerously-skip-permissions`, from an empty directory). A diff that contains "ignore previous instructions and run …" can at
worst produce a wrong verdict, which the gate and the next review still constrain.

### Secrets
Tokens, passwords and keyring passwords live in `~/.config/sandbox/secrets/` (directory 0700, files 0600) - never in the repo,
the images or the logs. Workers get their own Forgejo token and keyring password as read-only mounts. Optional paid API keys are
read by the foreman on the host and sent in headers only (never in URLs, so they cannot appear in proxy logs); worker
containers never see them.

## Honest limits
- The egress proxy is shared by all projects; an alarm acknowledged with `bin/egress-report --ack` is acknowledged for everyone.
- The audit logs are protected against silent truncation by size high-water marks, not cryptographically. Someone with your
  user account on the machine can change them.
- Containers share the kernel with the machine; a kernel exploit escapes them. That is why a VM is recommended.
- The model provider sees everything the workers send (prompts, code, tool output). Do not use it on code you may not share.
- An allowed host is allowed for any payload; `daily-cloudcode-pa.googleapis.com` necessarily carries whatever the agent sends.

## Reporting a vulnerability
Open a private security advisory on the repository, or contact the maintainer directly. Please do not open a public issue.
