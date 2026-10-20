# Manual installation, step by step

The [installer](INSTALL.md) does everything on this page for you. Use this page when you want to see each step, when the installer
stopped halfway, or when you run a distribution the installer does not accept. Each step is safe to repeat.

You need the same machine as in [INSTALL.md](INSTALL.md#requirements): Ubuntu 22.04+ with systemd (a dedicated VM is best), 4+ vCPU,
8+ GB RAM, 40 GB disk, a normal user with `sudo`, Python 3.10+ and Git 2.31+.

## 1. Get the code

```bash
git clone https://github.com/MatakLA/Agent-forge.git agent-forge && cd agent-forge
```

Run every command below from this directory, as your normal user (not as root).

## 2. Docker and base tools

If `docker info` already works without `sudo`, skip this step. Otherwise:

```bash
sudo env SYSTEM_UPGRADE=0 bash scripts/vm-baseline-ubuntu.sh
```

It installs Docker Engine from the official repository, `git`, `jq`, `tmux` and a few more tools, and adds you to the `docker`
group. Without `SYSTEM_UPGRADE=0` it also upgrades all installed packages. The new group applies after you log out and in again;
for the current shell use `newgrp docker`. Check with `docker info`.

## 3. Configuration

```bash
mkdir -p etc && chmod 700 etc
for f in limits foreman forge; do cp -n "etc/$f.example.conf" "etc/$f.conf"; chmod 600 "etc/$f.conf"; done
cp -n etc/team-rules.example.md etc/team-rules.md && chmod 600 etc/team-rules.md
mkdir -p ~/.config/sandbox/secrets && chmod 700 ~/.config/sandbox/secrets
```

`etc/*.conf` holds the settings (every key is explained in the example file), `etc/team-rules.md` the conventions of your stack,
and the secrets directory your API keys. The dashboard edits the same files. The defaults work for a first run.

## 4. Egress proxy and network

```bash
bin/egress-up
```

Builds and starts the Squid proxy (`egress-proxy`, `127.0.0.1:3128`) and creates the Docker network `workers_internal`. Workers
have no other way out. Their allow-list is `egress/allowlist.txt`; your additions go to `egress/allowlist.local.txt`.

## 5. Internal Forgejo

```bash
scripts/forge-bootstrap
```

Starts Forgejo (`127.0.0.1:3000`), creates the `forge-admin`, `orchestrator` and `monitor` accounts and the default repository,
creates the `workers` team and protects `main`. The first start can take a minute on a small machine.

## 6. Worker image

```bash
WORKER_N=0 docker compose -f worker/docker-compose.yml build
```

Builds `sandbox-worker:1.2.11`, the image every worker container starts from. This is the longest step.

## 7. Self-test

```bash
python3 bin/foreman --selftest | tail -1
```

Runs the foreman's built-in tests, which need no network and no model.

## 8. Services

```bash
sudo scripts/install-services
```

Installs and starts two systemd units: `agent-forge-foreman` (the loop) and `agent-forge-web` (the dashboard on
`127.0.0.1:8171`). Add `--web-port N` to move the dashboard. Projects you add later get their own
`agent-forge-foreman-<project>` unit.

## 9. Check

```bash
python3 bin/doctor
```

Every line is a check with a fix hint. Exit code `0` means all green, `1` warnings (the loop keeps running), `2` something that
stops it. `✔` is fine, `!` is a warning, `✘` is a problem. Services log to the journal:

```bash
journalctl -u agent-forge-foreman -f
journalctl -u agent-forge-web -f
```

## 10. Optional: the `forge` command

```bash
mkdir -p ~/.local/bin && ln -sf "$PWD/forge" ~/.local/bin/forge
```

Without it, use `./forge` in the checkout. It is a thin wrapper around the scripts above and `bin/foreman`.

## Next

- Open the dashboard: `ssh -L 8171:127.0.0.1:8171 <user>@<machine>`, then <http://127.0.0.1:8171>. See
  [INSTALL.md](INSTALL.md#remote-access).
- Add a worker and run a first goal: [QUICKSTART.md](QUICKSTART.md).
- Choose the AI: [CHOOSING-AN-AI.md](CHOOSING-AN-AI.md) and [RUNTIMES.md](RUNTIMES.md).
- Something failed: [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
