# Install Agent-Forge

Pick a way in, from "look around in a minute" to "a team that works through a queue".

| | what you get | what you need | time |
|---|---|---|---|
| [**Demo**](#a-try-it-in-a-minute-docker-demo) | the real dashboard on invented data; nothing is built | Docker | ~1 min |
| [**One-command install**](#b-the-real-install-one-command) | a working install on an Ubuntu machine or VM | Ubuntu 22.04+, sudo | ~20 min |
| [**Cloud-init**](#c-a-fresh-vm-with-cloud-init) | the same as B, done on the first boot of a new VM | a cloud or local hypervisor that takes cloud-init | ~20 min |
| [By hand](SETUP.md) | the same as B, every step spelled out | the same as B | ~30 min |

Not sure? Start with the demo. It changes nothing on your machine and needs no accounts or keys.

## Requirements

| | demo | real install |
|---|---|---|
| System | anything that runs Docker: Linux, macOS, Windows (Docker Desktop with WSL2) | Ubuntu 22.04 or newer. The installer also accepts Debian (anything with `apt`), but only Ubuntu is tested. |
| Hardware | nothing special | 4+ vCPU, 8+ GB RAM, 40+ GB disk. Each worker is a container with its own limits. |
| Software | Docker | `bash` and `curl`. The installer adds Docker, `git` (2.31+) and `python3` (3.10+) when they are missing. |
| User | any | a normal user with `sudo`. **Not root.** |
| Accounts | none | at least one way to pay for a model: an API key or an Antigravity subscription, entered after the install ([docs/CHOOSING-AN-AI.md](CHOOSING-AN-AI.md)) |

**Use a dedicated VM for the real install** and give it test data only. The workers run commands without asking; containers are
strong isolation but not a VM boundary. Read [SECURITY.md](../SECURITY.md) before you put anything valuable near it.

Ports, all bound to `127.0.0.1` of the machine (nothing is exposed to the network):

| port | what |
|---|---|
| 8171 | the dashboard (an admin console - keep it private) |
| 3000, 2222 | the internal Forgejo (web / git over ssh) |
| 3128 | the egress proxy the workers go through |

## A. Try it in a minute: Docker demo

```bash
docker run --rm -p 8171:8171 ghcr.io/matakla/agent-forge-demo:latest
```

Open <http://localhost:8171>. The image is published for amd64 and arm64 when a version is released. Stop it with `Ctrl+C`.

If you have a clone of the repository instead, `./forge demo` builds and starts the same thing from `demo/docker-compose.yml`
(`./forge demo down` stops it).

What it is: the real dashboard, read-only, on invented data: a team that "worked" on the example project. There are no workers, no
Forgejo and no model calls, so nothing here shows how fast or good the real thing is. It shows what you would be looking at.
More in [DEMO.md](DEMO.md).

## B. The real install: one command

On the Ubuntu machine or VM, as your normal user:

```bash
curl -fsSL https://raw.githubusercontent.com/MatakLA/Agent-forge/main/install.sh | bash
```

Flags go after `bash -s --`. For example, to put the example project's vision in place so the autopilot has something to build
right away:

```bash
curl -fsSL https://raw.githubusercontent.com/MatakLA/Agent-forge/main/install.sh | bash -s -- --demo
```

The bootstrap needs only `bash` and `curl`. It installs `git` and `python3` if they are missing, clones the repository to
`~/agent-forge` and runs `scripts/install.sh`. It refuses to run as root. Run again, it only fast-forwards an existing checkout.
Read it first if you like - it is one short file: [`install.sh`](../install.sh).

| variable | meaning |
|---|---|
| `FORGE_DIR` | where the code lives (default `~/agent-forge`) |
| `FORGE_REF` | a branch or tag to install instead of the default branch |
| `FORGE_REPO_URL` | clone from here instead of the public repository (your fork, a mirror) |

Prefer to look before you pipe? Clone and run it yourself:

```bash
git clone https://github.com/MatakLA/Agent-forge.git ~/agent-forge && cd ~/agent-forge
./forge install --check      # prints every planned step, changes nothing
./forge install              # add --demo for the example vision
```

The installer is safe to run again: every step skips what is already done. If it stops with an error, fix the cause and re-run
it. At the end it links the command into `~/.local/bin` as `agent-forge`, and as `forge` unless that name is already taken
(`--no-link` skips this). If `~/.local/bin` is not in your `PATH` yet, open a new shell or run `./forge` from the checkout.

When it asks for your `sudo` password, that is for installing Docker and the systemd services. Expect about 20 minutes, most of
it image downloads and builds.

## C. A fresh VM with cloud-init

[`deploy/cloud-init.yaml`](../deploy/cloud-init.yaml) is user data for a new Ubuntu 24.04+ VM. It creates the user `forge`, clones the
repository and runs the installer with `--demo` on the first boot. Before you use it:

1. Replace the SSH key under `ssh_authorized_keys` with yours (password logins are off).
2. Replace `REPOSITORY_URL` with `https://github.com/MatakLA/Agent-forge.git`.

Give the VM 4+ vCPU, 8+ GB RAM and 40+ GB disk. After the first boot, `~forge/agent-forge/install.log` shows how the install went.
Then connect with `ssh -L 8171:127.0.0.1:8171 forge@<vm>` ([remote access](#remote-access)).

## What the installer does

`scripts/install.sh`, in this order. Each step is skipped when it is already done. `--check` prints the plan and changes nothing.

1. **Preflight.** Refuses root; needs `apt`, `sudo`, Python 3.10+ and git 2.31+. Warns below 4 GB RAM or 2 CPUs and below 15 GB
   free where Docker keeps images; stops below 5 GB. Asks for your `sudo` password once and keeps it warm for the long steps.
2. **Docker.** If Docker is missing or your user may not use it, runs `scripts/vm-baseline-ubuntu.sh`: system update, base tools
   (`git`, `jq`, `tmux`, `make`, `ripgrep`, ...), unattended security updates (no automatic reboot), Docker Engine from Docker's signed
   repository (key checked by fingerprint), your user in group `docker`. If the group is new for this session, the installer
   continues inside it.
3. **Configuration.** Copies `etc/limits.example.conf`, `etc/foreman.example.conf`, `etc/forge.example.conf` and
   `etc/team-rules.example.md` to `etc/*.conf` / `etc/team-rules.md` where they do not exist (mode 0600; `etc/` is 0700). Creates the
   secrets directory `~/.config/sandbox/secrets` (0700). With `--demo`, puts `examples/demo/vision.md` in place as `docs/vision.md`.
4. **Egress proxy and internal network.** `bin/egress-up` starts the Squid proxy container (`egress-proxy`) and the internal Docker
   network `workers_internal`, which has no route to the internet. Workers can only reach the hosts on the allow-list.
5. **Internal Forgejo.** `scripts/forge-bootstrap` starts Forgejo, creates the bot accounts (`forge-admin`, `orchestrator`,
   `monitor`), the repository `sandbox/playground`, the team `workers`, and protects `main`: only the orchestrator merges.
   Passwords and tokens go to the secrets directory, never to the screen.
6. **Worker image.** Builds the Antigravity worker image (`sandbox-worker:1.2.11`) unless it exists. The image for API-key workers
   is built when you add the first such worker.
7. **Checks.** `bin/foreman --selftest` (spends nothing).
8. **Services.** `scripts/install-services` installs and starts two systemd services: `agent-forge-foreman` (the loop) and
   `agent-forge-web` (the dashboard). They start again after a reboot.
9. **Health check and shortcut.** `bin/doctor --quick`, then the `forge` shortcut. Run `forge doctor` later for the full check.

Outside the checkout it adds Docker and the base tools, the two systemd units, the secrets directory, and the containers, volumes
and network above. Nothing is exposed beyond `127.0.0.1`.

## First run in the dashboard

The dashboard is at <http://127.0.0.1:8171> on the machine ([from your laptop](#remote-access)). The Overview tab shows a **Get
started** card with four steps, checked off as you do them:

1. **Choose where the code lives.** A dialog offers *Internal Forgejo only*, *GitHub* or *GitLab*. With GitHub or GitLab you give a
   token for **that one repository**; the orchestrator opens and merges pull requests there, and workers never see the token.
   Later: Settings → Project. Details: [UPSTREAM.md](UPSTREAM.md).
2. **Add a worker.** Settings → Workers → **+ Add a worker**, pick a runtime. An API runtime (`claude`, `codex`, `gemini`) asks for
   the key once. `agy` (Antigravity) opens a terminal where you sign the worker's own Google account in. From a shell:
   `bin/worker-add 1 --runtime claude` (store the key first: `printf '%s' "$KEY" | bin/secret-set anthropic.key`).
   Which one to pick: [CHOOSING-AN-AI.md](CHOOSING-AN-AI.md) and [RUNTIMES.md](RUNTIMES.md).
3. **Get notified** (optional). Settings → Project → `NOTIFY_URL`: ntfy, Slack, Discord or any webhook.
4. **Give the team its first goal.** Overview → *What should the team do?* → **Start now**, or **Add to backlog**, or
   **Big project → roadmap**. From a shell: `bin/foreman goal "..."`.

A goal that is small and ends in something you can check works best: *"Add a `search` command that lists the bookmarks with a
tag, with a test that fails without it."* A step-by-step first run on a sample project: [QUICKSTART.md](QUICKSTART.md).

## Remote access

The dashboard and Forgejo listen on `127.0.0.1` of the install machine. From your laptop, use an SSH tunnel:

```bash
ssh -L 8171:127.0.0.1:8171 -L 3000:127.0.0.1:3000 <user>@<machine>
```

Then open <http://127.0.0.1:8171> (and <http://127.0.0.1:3000> for the internal Forgejo). `forge url` prints the address and the
exact tunnel command for this machine.

The dashboard can change settings, stop projects and create workers, so anyone who reaches it can do all of that. Do not put it on
a public address. Tailscale serve works if you prefer it (never `funnel`); the details, and the optional sign-in token, are in
[SECURITY.md](../SECURITY.md).

## Update

```bash
forge update --check    # shows what would change; changes nothing
forge update            # fast-forward, rebuild what changed, restart the services
```

`forge update` only fast-forwards the checkout; it never merges and never discards your work. It refuses to run when tracked files
are edited or the checkout has commits that are not upstream, and says what to do. Before anything changes, the new version must
pass its own self-test in a scratch worktree. Then it rebuilds only what the update touched (worker image, egress proxy,
Forgejo), re-installs and restarts the services, and runs `forge doctor --quick`. Running worker runs keep going. Your
configuration (`etc/*.conf`, `projects/`), the secrets directory, Forgejo's data and your workers are not touched. If `etc/*.example.*`
gained settings, they show up in the dashboard; compare with your `etc/*.conf` if you want the new defaults.
See the [CHANGELOG](../CHANGELOG.md) for what changed.

## Uninstall

```bash
forge uninstall --check    # shows the plan; changes nothing
forge uninstall            # removes the services, containers and shortcuts; KEEPS your data
forge uninstall --purge    # also deletes the data and images (type "purge" to confirm)
```

The default asks `y/N`. It removes the systemd services, the worker, egress-proxy and Forgejo containers, the `workers_internal`
network and the shortcuts. Forgejo's repositories, the workers' logins, `runs/`, `etc/`, `projects/` and the secrets stay, so a later
`forge install` picks up where you left off.

`--purge` deletes all of that for good, including the Docker images it built or pulled. Code you already pushed to GitHub or
GitLab stays there. Neither mode touches Docker itself, the apt packages or the checkout - delete the directory yourself when you are
done. `--yes` skips the questions (for scripts).

## macOS and Windows

- **Demo:** works with Docker Desktop (on Windows, with the WSL2 backend; run the command in the WSL2 terminal). Apple silicon is
  fine; the demo image is multi-arch.
- **Real install:** needs Linux with systemd. On a Mac or a Windows PC, run an Ubuntu VM (Multipass, UTM, VirtualBox, Hyper-V, ...)
  and follow B inside it. A VM is the recommended setup anyway. WSL1 and containers without systemd do not work.
- **arm64 (for example an Ubuntu VM on Apple silicon):** the install is tested on Ubuntu 24.04 arm64 in CI, with the API
  runtimes (`claude`, `codex`, `gemini`, `compat`, `aider`). The Antigravity runtime (`agy`) is x86-64 only, because the pinned
  Antigravity CLI is the `linux-x64` build; `bin/worker-add` says so and stops. On a Mac, install an arm64 Ubuntu VM and use API keys
  or your own model server.

## What can go wrong

The common ones: the `docker` group is not active in your shell yet, port 8171 or 3000 is taken, Forgejo is slow to start on a
small VM, the disk is full, an `apt` lock is held by another process. Each has a symptom and a fix in
[TROUBLESHOOTING.md](TROUBLESHOOTING.md). `forge doctor` checks the whole install and says how to fix what it finds.

## Next

[QUICKSTART.md](QUICKSTART.md) walks one goal from the dashboard to a merged pull request · [FAQ.md](FAQ.md) ·
[docs/README.md](README.md) is the index of everything else.
