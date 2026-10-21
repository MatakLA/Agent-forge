# Troubleshooting

Start here, whatever is wrong:

```bash
forge doctor          # checks the whole install end to end; says how to fix every ✘ and ⚠. Read-only.
forge logs            # the foreman's log (add -f to follow). Also: forge logs web | egress | forgejo | worker-1
forge status          # goals, tasks, workers, recent events
```

`forge doctor` exits 0 when all is good, 1 on warnings only and 2 when something is broken. `--quick` skips the per-worker quota
check, which takes a few seconds per worker. The dashboard has the same checks under its Health view.

Each entry below is **symptom → cause → fix**. Every install step is safe to run again, so after a fix, re-run what failed.

- [Installing](#installing)
- [The dashboard](#the-dashboard)
- [Workers and runs](#workers-and-runs)
- [GitHub and GitLab](#github-and-gitlab)
- [Where the logs are](#where-the-logs-are)
- [Still stuck?](#still-stuck)

## Installing

### "Docker is installed but this user cannot use it" / `permission denied ... docker.sock`

**Cause:** the installer added you to the `docker` group, but your login session does not know yet. **Fix:** log out and in again
(over SSH, reconnect), or run `newgrp docker`, then run the installer again. Check with `docker info`. If the daemon itself is down:
`sudo systemctl enable --now docker`.

### `Could not get lock /var/lib/dpkg/lock-frontend`, or the install waits at `apt`

**Cause:** another process holds the package manager. On a fresh VM that is usually `unattended-upgrades` or cloud-init finishing
its own updates. The installer already waits up to 10 minutes for the lock. **Fix:** wait, then run the installer again. See who
holds it with `ps aux | grep -E 'apt|dpkg'`. Do not delete lock files or kill `dpkg` in the middle of an upgrade.

### "Only N GB free where Docker keeps its images"

**Cause:** the installer stops below 5 GB free in `/var/lib` (it warns below 15 GB). The images need about 5 GB, and runs add more.
**Fix:** free space or grow the disk, then run it again. On a machine that has run for a while, `bin/foreman disk` shows what takes
the space and `bin/foreman cleanup compress|transcripts|docker [days]` reclaims it. `docker system df` shows what Docker holds.

### "Python 3.10+ is needed" / "This installer supports Ubuntu / Debian (apt)"

**Cause:** an old distribution, or one without `apt`. **Fix:** use Ubuntu 22.04 or newer. On other systems, a VM is the easy way
([INSTALL.md](INSTALL.md#macos-and-windows)); [SETUP.md](SETUP.md) lists every step if you want to adapt them.

### "Run it as your normal user, not root"

**Cause:** the installer refuses root on purpose: the services, the secrets and the Docker group belong to a normal user. **Fix:**
create one (`adduser forge && adduser forge sudo`), log in as it, run the installer there.

### No systemd: a container, WSL1, or a minimal image

**Symptom:** the installer says "no systemd on this machine, so the services are not installed". **Cause:** the two services need
systemd as PID 1. **Fix:** use a real VM, or WSL2 with systemd enabled. Without systemd you can start them by hand
(`python3 bin/foreman run &` and `python3 web/server.py &`), but they will not survive a reboot and this is not a supported setup.

### Behind a proxy

`apt`, `curl` and `git` follow `http_proxy` / `https_proxy` when `sudo` keeps them (`sudo -E`), or when the proxy is set in
`/etc/apt/apt.conf.d/`. The Docker daemon needs its own proxy setting (a systemd drop-in for `docker.service`) to pull images.
Agent-Forge's egress proxy connects to the internet directly and has no upstream-proxy setting: behind a proxy that is mandatory,
the workers cannot reach their model API. Use a machine with direct access to your provider.

### "Forgejo does not answer on http://127.0.0.1:3000"

**Cause:** Forgejo needs some time on its first start, a small VM needs more, or the container died. **Fix:**

```bash
docker ps | grep forgejo        # is it running?
forge logs forgejo              # why not?
scripts/forge-bootstrap         # starts it again and finishes the setup; safe to repeat
```

### Port 3000 (Forgejo) or 8171 (dashboard) is already in use

**Cause:** another program listens there. Find it with `sudo ss -ltnp | grep -E ':3000|:8171'`.

- **Dashboard:** `sudo scripts/install-services --web-port 8172` reinstalls the dashboard service on another port. `forge url`
  prints the right address afterwards.
- **Forgejo:** stop the other program if you can. Moving Forgejo needs two settings to agree: `FORGE_HTTP_PORT=3001` when it is
  started, and `FORGE_URL=http://127.0.0.1:3001` in `etc/forge.conf`. That path is not covered by the tests; prefer freeing 3000.

### The installer stopped half-way

It prints the line that failed and says to run it again. Do: fix the cause named in the message, run `forge install` (or the
one-liner) again. Finished steps are skipped. `forge install --check` shows the plan without changing anything.

## The dashboard

### The page does not open

**Cause:** the dashboard listens on `127.0.0.1` of the install machine only. **Fix:** on your laptop open an SSH tunnel, then
the address on your own machine: `forge url` prints the exact command, which looks like
`ssh -L 8171:127.0.0.1:8171 <user>@<machine>`. If the tunnel is up and the page is still empty, the service may be down:

```bash
systemctl status agent-forge-web
forge logs web
sudo systemctl restart agent-forge-web
```

### "host not allowed", or it asks for a token

**Cause:** the dashboard checks the `Host` header, against DNS-rebinding tricks. Only `127.0.0.1`, `localhost`, `*.ts.net` and names in
`DASHBOARD_HOSTS` pass. A sign-in is required when `DASHBOARD_TOKEN` is set. **Fix:** use the tunnel address, or add your name to
`DASHBOARD_HOSTS` (an environment variable of the dashboard service), as described in
[SECURITY.md](../SECURITY.md#the-dashboard-is-an-admin-console---keep-it-private). Never expose the dashboard to the internet.

### The Overview says the foreman is not running

**Cause:** the loop service is stopped or crashed. **Fix:** `systemctl status agent-forge-foreman`, `forge logs` for the reason,
`sudo systemctl restart agent-forge-foreman`. Running worker runs keep going (they are separate processes).

### Nothing is happening, but nothing is wrong

Look at **Overview → the team**: it says why each idle worker is idle (no goal, a task waits for another, kill switch, no key, signed
out, quota used up). A new goal needs up to half a minute before it is planned; the foreman ticks every 20 seconds.
If the foreman is *paused* (`forge pause`), run `forge resume`.

## Workers and runs

### A worker is signed out (Antigravity)

**Symptom:** `forge doctor` says `agy /usage unreadable - not logged in?`, or the worker's runs fail at once. **Fix:** Settings → Workers →
sign in (a terminal opens in the page), or `docker exec -it worker-1 agy-run`. One Google account per worker.

### A worker has no or a wrong API key

**Symptom:** `bin/worker-add` says "no API key for claude yet", or runs fail with an authentication error. **Fix:** store the key
again, from stdin so it never lands in your shell history, and recreate the worker:

```bash
bin/secret-set --list                                 # which keys exist (names only)
printf '%s' "$KEY" | bin/secret-set anthropic.key     # openai.key, gemini.key, compat.key
bin/worker-up 1
```

### The provider's quota or your cap is used up

**Symptom:** `forge status` shows a refused dispatch ("quota low"), or `forge doctor` flags the worker. **Cause:** an Antigravity
account is out of quota, or an API worker hit a USD cap in `etc/limits.conf` (per run, day, month, project or worker). **Fix:** see
the reset time with `docker exec worker-1 agy-run -p /usage` (Antigravity), or in the dashboard under Money & quota. Wait for the
reset, add another worker, or raise the cap in Settings (or `etc/limits.conf`) if you meant to spend more.

### Runs fail because a host is blocked (egress)

**Symptom:** a run dies on a network call, `forge doctor` says `egress allow-list lacks <host>`, or the proxy log shows `403`.
**Cause:** workers may reach only the hosts on the allow-list, by design: their model API and nothing else. **Fix:** look at what
was denied, and allow a host only if you trust it:

```bash
bin/egress-report --since 1h          # denied hosts and alarms
bin/egress-report --allow HOST        # let workers reach HOST (egress/allowlist.local.txt); --unallow removes it
```

The proxy reloads without interrupting runs. If a *task* needs packages from the internet, vendor them into the repository instead:
the gate runs offline, so a dependency the gate cannot find fails there anyway ([SECURITY.md](../SECURITY.md)).

### The kill switch tripped

**Symptom:** nothing is dispatched; `bin/killswitch status` says it is on. **Cause:** you turned it on, or the egress monitor found
a HIGH alarm (for example a worker tried to reach a private address) and switched it on for you. **Fix:** read the report first,
then acknowledge and restart:

```bash
bin/egress-report                         # what happened
bin/egress-report --ack && bin/killswitch off --start
```

`--start` also brings back the containers that `bin/killswitch on --hard` stopped.

### A task keeps failing the gate

The worker gets the test output and tries again. If that does not fix it, the task turns *Blocked* and appears in **Needs you**.
Common causes: `TEST_CMD` is empty or wrong for your stack, or the tests need a package the gate image lacks (the gate has no
network). Set `TEST_CMD` and `GATE_IMAGE` for the project (Settings → Project) and press retry on the task:
`forge task <id> retry`. Read the gate output with `forge task <id> show`.

### A worker container is stopped or missing

`docker ps -a | grep worker`. `bin/worker-up 1` recreates worker 1 and keeps its home volume (its login and clone).

### A goal is Blocked, or a plan waits for you

The **Needs you** list on the Overview shows what is waiting and offers the actions. From a shell: `forge status`, then
`forge task <id> show`, `forge task <id> approve|retry|cancel`, or `forge questions` and `echo "..." | forge answer <n>`.

## GitHub and GitLab

### `bin/upstream check` fails, or pull requests are not opened

```bash
bin/upstream check      # the token reaches the repository and may push
bin/upstream status     # settings, and how the internal main relates to upstream's main
```

Usual causes: the token is for another repository, lacks *Contents* and *Pull requests* write access (GitLab: scopes `api` and
`write_repository`), or has expired. Store a new one with `printf '%s' "$TOKEN" | bin/secret-set github.token`. A required review
on `main` keeps a pull request open when the token's user is also its author: use `AUTO_MERGE=0`, or get a reviewer.
[UPSTREAM.md](UPSTREAM.md) has the details.

## Where the logs are

| what | where |
|---|---|
| the foreman (the loop) | `forge logs` = `journalctl -u agent-forge-foreman` |
| the dashboard (and who changed what in it: every write and refused request, one line each, no secrets; `DASHBOARD_ACCESS_LOG=all` adds reads) | `forge logs web` = `journalctl -u agent-forge-web` |
| audit trail of owner actions (settings, secrets by name, workers, projects, egress decisions, kill switch) | `runs/ledger.jsonl`, `runs/ALERT` |
| a further project's foreman | `journalctl -u agent-forge-foreman-<project>` |
| what the foreman decided, as data | `runs/foreman/events.jsonl` (`runs/projects/<name>/foreman/` for other projects) |
| one worker run (its transcript and result) | `runs/<timestamp>-<worker>/` |
| egress proxy: allowed and denied hosts | `forge logs egress`, `bin/egress-report` |
| Forgejo | `forge logs forgejo` |
| a worker container | `forge logs worker-1` |
| a cloud-init install | `~/agent-forge/install.log` of the `forge` user |

## Still stuck?

1. Run `forge doctor` and keep its output.
2. Search the [FAQ](FAQ.md) and the [existing issues](https://github.com/MatakLA/Agent-forge/issues).
3. Ask in [Discussions](https://github.com/MatakLA/Agent-forge/discussions) (questions) or open an
   [issue](https://github.com/MatakLA/Agent-forge/issues/new/choose) (bugs). Include the output of `forge version` and
   `forge doctor`, what you ran, and the log lines around the error. **Remove keys, tokens and private repository names first.**
4. Security problems go to the address in [SECURITY.md](../SECURITY.md), not into a public issue.
