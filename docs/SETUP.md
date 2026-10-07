# Setup

Target: a dedicated Ubuntu 22.04+ VM (4+ vCPU, 8+ GB RAM, 40+ GB disk) with test data only. Everything below runs inside it as
your normal user unless it says `sudo`.

## The short way
`scripts/install.sh [--demo]` does steps 1-5 below on Ubuntu/Debian, safely re-runnable, and prints what to do in the dashboard.
The rest of this page is the same by hand, and the options.

## 0. What you need
- **Claude Code** logged in on the VM (`claude` → `/login`) - the default orchestrator. Optional but recommended.
- Workers: **one Google account per worker** with Antigravity access (read the terms note in the README first), and/or **API keys**
  for Claude Code, Codex or Gemini CLI ([RUNTIMES.md](RUNTIMES.md)).
- Python 3.10+, git (installed in step 1).

## 1. Base system
```bash
git clone <this repository> agent-forge && cd agent-forge
sudo bash scripts/vm-baseline-ubuntu.sh 2>&1 | tee vm-baseline.log
```
Installs updates, base tools and Docker Engine from Docker's repository (key checked by fingerprint), and adds you to the
`docker` group. **Log out and in again** (or reboot) before step 2. Check: `docker run --rm hello-world` works without sudo.

## 2. Configuration
```bash
cp etc/limits.example.conf etc/limits.conf        # required - nothing runs without limits
cp etc/foreman.example.conf etc/foreman.conf      # orchestrator providers, project dir, vision file, language
cp etc/team-rules.example.md etc/team-rules.md    # your project's rules (stack, conventions) - the reviewer enforces them
cp etc/forge.example.conf etc/forge.conf          # only if you want another org/repo name than sandbox/playground
```
Write your product vision to the file `VISION_FILE` points at (default `docs/vision.md`; see `docs/vision.example.md`).

Not a Python project? Set `TEST_CMD` (e.g. `npm test --silent`, `go test ./...`) and a `GATE_IMAGE` that has your toolchain and
dependencies - the gate runs offline. Want to be told when something needs you? Set `NOTIFY_URL` (ntfy topic, Slack or Discord
webhook). Both are in `etc/foreman.example.conf`.

## 3. Network and forge
```bash
bin/egress-up                 # builds the proxy, creates the internal network workers_internal (no route out)
scripts/forge-bootstrap       # Forgejo + forge-admin/orchestrator/monitor + org/repo + team workers + protected main
```
Passwords and tokens are written to `~/.config/sandbox/secrets/` (0600). Forgejo's web UI is on http://127.0.0.1:3000
(`ssh -L 3000:127.0.0.1:3000 <vm>` from your laptop; log in as `forge-admin` with the password from the secrets directory).

## 4. Workers
Easiest: dashboard → Settings → "+ Add worker" → pick Antigravity (it opens a terminal for the login: open the link with that
worker's Google account, paste the code) or an API runtime (it asks for the key once). API workers by hand:
```bash
printf '%s' "$ANTHROPIC_API_KEY" | bin/secret-set anthropic.key     # openai.key / gemini.key
bin/worker-add 5 --runtime claude                                    # codex / gemini
```
Antigravity workers by hand:
```bash
bin/worker-add 1                       # Forgejo user + token, keyring password, HOME volume, container (builds the image once)
docker exec -it worker-1 agy-run       # once per worker: log in with THAT worker's Google account (URL + code), then /exit
docker exec worker-1 agy-run -p "reply ok" --print-timeout 2m      # check: answers "ok"
```
Repeat for workers 2..N. Set `MAX_PARALLEL` in `etc/limits.conf` to the number of workers.

## 5. Check and start
```bash
bin/doctor                             # checks everything end to end and says how to fix what is wrong
make check                             # selftests (no Docker, no model calls); make test adds the supervisor integration test
sudo scripts/install-services          # agent-forge-foreman + agent-forge-web (dashboard on 127.0.0.1:8171)
bin/foreman goal "Create a hello module with one function and a test"
```
Watch `bin/foreman status` or the dashboard. The first goal takes a few minutes: plan → run → gate → review → merge.
Goals, the backlog and the planner's questions can also be handled in the dashboard (Overview).

The dashboard listens on 127.0.0.1 only. To use it from your phone, publish it with `tailscale serve` (names `*.ts.net` are
accepted) or a reverse proxy - then add its host name to `DASHBOARD_HOSTS` and set `DASHBOARD_TOKEN` (open the page once as
`/?token=<token>`). It is an admin console; see SECURITY.md.

## 5b. GitHub or GitLab (optional)
The dashboard asks where a new project's code lives. By hand: [UPSTREAM.md](UPSTREAM.md) (`FORGE_KIND`, `UPSTREAM_REPO`, a token
for that repository only, `bin/upstream import`).

## 6. More projects (optional)
```bash
bin/foreman init-project shop --repo acme/shop --workers worker-5,worker-6 --lang English   # projects/shop/
$EDITOR projects/shop/vision.md projects/shop/team-rules.md
FORGE_REPO=acme/shop scripts/forge-bootstrap
sudo scripts/install-services --project shop
bin/foreman --project shop goal "..."
```
Each worker in the pool clones the project's repository on first use (`~/work/<project>` inside the worker). Give every project
its own pool. `bin/worker-add N` puts a new worker into the `workers` team of every organisation, so it can serve any project.

## 7. Day to day
- Big requests: `bin/foreman project "…"` turns them into a roadmap in the backlog; workers drain it day and night.
- Decisions only you can make end up in `bin/foreman questions`.
- After a soft kill-switch trip: read `bin/egress-report`, then `bin/egress-report --ack && bin/killswitch off --start`
  (or the dashboard's "Continue").
- Answer them in the dashboard or with `echo "…" | bin/foreman answer <n>`: answers go to `decisions.md`, which every later
  plan reads.
- Updating Agent-Forge: `git pull`, `make check`, `bin/doctor`, `sudo systemctl restart agent-forge-foreman agent-forge-web`
  (see CHANGELOG.md).

## Troubleshooting
| symptom | cause / fix |
|---|---|
| `dispatch refused: quota low` | the worker account is under `QUOTA_MIN_*`; it continues after the reset shown by `docker exec worker-N agy-run -p /usage` |
| a worker stops ~1 h after login | `oauth2.googleapis.com` missing from the allow-list |
| `setup-failed` runs | the worker's clone is dirty; the next dispatch aborts stuck rebases/merges automatically |
| tasks wait although workers are idle | they depend on unfinished tasks or share files with running ones (`bin/foreman status`); add registries to `SHARED_FILES` |
| `no orchestrator provider could answer` | Claude Code out of usage and every worker account's Claude/Gemini quota exhausted - it resumes after the resets |
| anything else | `bin/doctor` - it checks tools, config, secrets, Forgejo, proxy, image, every worker's login and the loop |
| a task stays "running" after a reboot | it is recovered automatically within a tick (re-queued, no attempt counted) |
