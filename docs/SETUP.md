# Setup

Target: a dedicated Ubuntu 22.04+ VM (4+ vCPU, 8+ GB RAM, 40+ GB disk) with test data only. Everything below runs inside it as
your normal user unless it says `sudo`.

## 0. What you need
- **Claude Code** logged in on the VM (`claude` → `/login`) - the default orchestrator. Optional but recommended.
- **One Google account per worker** with Antigravity access. Read the terms note in the README first.
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

## 3. Network and forge
```bash
bin/egress-up                 # builds the proxy, creates the internal network workers_internal (no route out)
scripts/forge-bootstrap       # Forgejo + forge-admin/orchestrator/monitor + org/repo + team workers + protected main
```
Passwords and tokens are written to `~/.config/sandbox/secrets/` (0600). Forgejo's web UI is on http://127.0.0.1:3000
(`ssh -L 3000:127.0.0.1:3000 <vm>` from your laptop; log in as `forge-admin` with the password from the secrets directory).

## 4. Workers
```bash
bin/worker-add 1                       # Forgejo user + token, keyring password, HOME volume, container (builds the image once)
docker exec -it worker-1 agy-run       # once per worker: log in with THAT worker's Google account (URL + code), then /exit
docker exec worker-1 agy-run -p "reply ok" --print-timeout 2m      # check: answers "ok"
```
Repeat for workers 2..N. Set `MAX_PARALLEL` in `etc/limits.conf` to the number of workers.

## 5. Check and start
```bash
bin/foreman --selftest                 # pure-function tests, spends nothing
bin/status                             # proxy, forge, workers, limits, kill switch
sudo scripts/install-services          # agent-forge-foreman + agent-forge-web (dashboard on 127.0.0.1:8171)
bin/foreman goal "Create a hello module with one function and a test"
```
Watch `bin/foreman status` or the dashboard. The first goal takes a few minutes: plan → run → gate → review → merge.

## 6. Day to day
- Big requests: `bin/foreman project "…"` turns them into a roadmap in the backlog; workers drain it day and night.
- Decisions only you can make end up in `bin/foreman questions`.
- After a soft kill-switch trip: read `bin/egress-report`, then `bin/egress-report --ack && bin/killswitch off --start`
  (or the dashboard's "Continue").
- Updating Agent-Forge: `git pull`, `bin/foreman --selftest`, `sudo systemctl restart agent-forge-foreman agent-forge-web`.

## Troubleshooting
| symptom | cause / fix |
|---|---|
| `dispatch refused: quota low` | the worker account is under `QUOTA_MIN_*`; it continues after the reset shown by `docker exec worker-N agy-run -p /usage` |
| a worker stops ~1 h after login | `oauth2.googleapis.com` missing from the allow-list |
| `setup-failed` runs | the worker's clone is dirty; the next dispatch aborts stuck rebases/merges automatically |
| tasks wait although workers are idle | they depend on unfinished tasks or share files with running ones (`bin/foreman status`); add registries to `SHARED_FILES` |
| `no orchestrator provider could answer` | Claude Code out of usage and every worker account's Claude/Gemini quota exhausted - it resumes after the resets |
