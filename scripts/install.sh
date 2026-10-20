#!/usr/bin/env bash
# install.sh - from a fresh Ubuntu machine (ideally a dedicated VM, 4+ vCPU, 8+ GB RAM) to a running Agent-Forge in one command.
# Run it as your normal user (it asks for sudo where it must). Safe to re-run: every step skips what is already done.
#
#   ./forge install [--demo] [--check]       from a checkout; the one-line bootstrap (install.sh in the repository root) clones and calls this
#
# Steps: preflight -> Docker (scripts/vm-baseline-ubuntu.sh) -> configuration from the examples -> egress proxy and internal
# network -> internal Forgejo with bot accounts and a protected main -> worker image -> selftest -> foreman + dashboard services
# -> health check -> `forge` shortcut in ~/.local/bin.
# --check dry-run mode: prints every planned step without modifying the system; 0 side effects.
# --demo  puts the example vision (examples/demo) in place, so the autopilot has something to build right away.
# Afterwards open the dashboard: choose where the code lives (internal / GitHub / GitLab), add workers, enter a goal.
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
DEMO=0
CHECK=0
LINK=1
for a in "$@"; do
  case "$a" in
    --demo) DEMO=1 ;;
    --check) CHECK=1 ;;
    --no-link) LINK=0 ;;
    -h|--help)
      cat <<'EOT'
Usage: scripts/install.sh [--demo] [--check] [--no-link]      (or: forge install ...)

From a fresh Ubuntu machine to a running Agent-Forge in one command.

Options:
  --check    Dry-run mode: prints every planned step without modifying the system
  --demo     Install example demo vision (examples/demo)
  --no-link  Do not put the `forge` shortcut in ~/.local/bin
  -h, --help Show this help message
EOT
      exit 0 ;;
    *) printf 'Unknown option: %s\n(see scripts/install.sh --help)\n' "$a" >&2; exit 2 ;;
  esac
done
TOTAL=9
N=0
step() { N=$((N + 1)); printf '\n[%d/%d] %s\n' "$N" "$TOTAL" "$*"; }
ok()   { printf '  ✔ %s\n' "$*"; }
warn() { printf '  ⚠ %s\n' "$*"; }
would() { printf '  Would: %s\n' "$*"; }
die()  { printf '\n✘ %s\n' "$1"; shift; for h in "$@"; do printf '    → %s\n' "$h"; done; exit 1; }
trap 'printf "\n✘ failed at line %s: %s\n  Fix the cause and run it again - every step skips what is already done. Common causes: docs/INSTALL.md (\"What can go wrong\")\n" "$LINENO" "$BASH_COMMAND"' ERR
T0="${FORGE_T0:-$(date +%s)}"; export FORGE_T0="$T0"

VERSION="$(cat VERSION 2>/dev/null || echo unknown)"
printf 'Agent-Forge %s - installing%s\n' "$VERSION" "$([ "$CHECK" = 1 ] && echo " (dry run: nothing is changed)")"

# --- resources: warnings, except for a disk that cannot hold the images
preflight_resources() {
  local mem cpus free
  mem="$(awk '/^MemTotal:/ {printf "%d", ($2 / 1048576) + 0.5}' /proc/meminfo 2>/dev/null || true)"
  cpus="$(nproc 2>/dev/null || echo 1)"
  free="$(df -BG --output=avail /var/lib 2>/dev/null | tail -1 | tr -dc '0-9')"
  if [ -n "$mem" ]; then
    if [ "$mem" -lt 4 ]; then warn "${mem} GB RAM: the dashboard works, but workers need about 2 GB each - 8 GB is comfortable."
    else ok "${mem} GB RAM"; fi
  fi
  if [ "$cpus" -lt 2 ]; then warn "${cpus} CPU: runs will be slow - 4 are comfortable."; else ok "${cpus} CPUs"; fi
  if [ -n "$free" ]; then
    if [ "$free" -lt 5 ]; then die "Only ${free} GB free where Docker keeps its images (/var/lib)." "The images need about 5 GB; free some space and run it again."
    elif [ "$free" -lt 15 ]; then warn "${free} GB free for Docker images - fine for a start, 15+ GB is comfortable."
    else ok "${free} GB free for Docker images"; fi
  fi
}

preflight_tools() {
  command -v python3 >/dev/null 2>&1 || die "python3 is needed (3.10+)." "sudo apt-get install -y python3"
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die "Python 3.10+ is needed (found $(python3 -c 'import sys; print(sys.version.split()[0])'))." "Ubuntu 22.04 or newer has it."
  ok "python $(python3 -c 'import sys; print(sys.version.split()[0])') (3.10+)"
  command -v git >/dev/null 2>&1 || die "git is needed (2.31+)." "sudo apt-get install -y git"
  git --version | awk '{split($3, v, "."); exit !(v[1] > 2 || (v[1] == 2 && v[2] >= 31))}' || die "git 2.31+ is needed (found $(git --version | awk '{print $3}'))."
  ok "git $(git --version | awk '{print $3}') (2.31+)"
}

# --- the `forge` shortcut: ~/.local/bin/agent-forge always, ~/.local/bin/forge unless that name is already some other program
link_cli() {
  local bindir="$HOME/.local/bin" name dest taken shown=""
  mkdir -p "$bindir"
  for name in agent-forge forge; do
    dest="$bindir/$name"
    if [ -L "$dest" ] && [ "$(readlink -f "$dest")" = "$ROOT/forge" ]; then shown="$shown $name"; continue; fi
    taken="$(command -v "$name" 2>/dev/null || true)"
    if [ -e "$dest" ] || [ -L "$dest" ] || { [ "$name" = forge ] && [ -n "$taken" ]; }; then
      warn "'$name' already exists (${taken:-$dest}) - left alone; use $ROOT/forge instead"
      continue
    fi
    ln -s "$ROOT/forge" "$dest" && shown="$shown $name"
  done
  [ -z "$shown" ] || ok "shortcut:${shown} -> $ROOT/forge"
  case ":$PATH:" in *":$bindir:"*) ;; *) [ -z "$shown" ] || warn "$bindir is not in your PATH yet - open a new shell (or log in again), or run ./forge from $ROOT" ;; esac
}

# --- the dashboard needs a moment after its service starts
wait_dashboard() {
  [ -d /run/systemd/system ] || return 0
  local port
  port="${BIND_PORT:-$(sed -n 's/.*BIND_PORT=\([0-9]\+\).*/\1/p' /etc/systemd/system/agent-forge-web.service 2>/dev/null | head -1)}"; port="${port:-8171}"
  if python3 -c 'import sys,time,urllib.request as u,urllib.error as e
for _ in range(20):
    try: u.urlopen("http://127.0.0.1:%s/healthz" % sys.argv[1], timeout=2).read(); sys.exit(0)
    except e.HTTPError: sys.exit(0)
    except OSError: time.sleep(1)
sys.exit(1)' "$port"; then ok "dashboard answers on 127.0.0.1:$port"
  else warn "the dashboard does not answer yet - look at: forge logs"; fi
}

if [ "$CHECK" = 1 ]; then
  step "Preflight"
  command -v apt-get >/dev/null 2>&1 && ok "Ubuntu / Debian (apt-get available)" || die "This installer supports Ubuntu / Debian (apt)." "On other systems follow docs/SETUP.md (every step by hand)."
  if [ "$(id -u)" != 0 ]; then
    ok "running as normal user ($(id -un))"
  else
    would "run as normal user (not root)"
  fi
  if command -v sudo >/dev/null 2>&1; then
    ok "sudo is available"
  else
    would "require sudo for installation"
  fi
  preflight_resources
  preflight_tools

  step "Docker"
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    ok "docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || true) usable without sudo"
  else
    if ! command -v docker >/dev/null 2>&1 || ! id -nG 2>/dev/null | grep -qw docker; then
      would "install Docker and base tools via sudo bash scripts/vm-baseline-ubuntu.sh (no full system upgrade)"
      would "add user $(id -un) to docker group"
    else
      would "start Docker service or continue session in docker group via sg"
    fi
  fi

  step "Configuration (examples copied only where nothing exists yet)"
  if [ -d etc ]; then
    ok "etc/ directory exists"
  else
    would "create etc/ directory (mode 700)"
  fi
  for f in limits foreman forge; do
    if [ -f "etc/$f.conf" ]; then
      ok "etc/$f.conf exists"
    else
      would "copy etc/$f.example.conf to etc/$f.conf (mode 600)"
    fi
  done
  if [ -f etc/team-rules.md ]; then
    ok "etc/team-rules.md exists"
  else
    would "copy etc/team-rules.example.md to etc/team-rules.md (mode 600)"
  fi
  if [ "$DEMO" = 1 ]; then
    if [ -f docs/vision.md ]; then
      ok "demo vision in docs/vision.md exists"
    else
      would "copy examples/demo/vision.md to docs/vision.md"
    fi
  fi
  SECRETS_DIR="${SANDBOX_SECRETS:-$HOME/.config/sandbox/secrets}"
  if [ -d "$SECRETS_DIR" ]; then
    ok "secrets directory exists"
  else
    would "ensure secrets directory exists with mode 700"
  fi

  step "Egress proxy and the workers' internal network"
  if command -v docker >/dev/null 2>&1 && docker inspect egress-proxy >/dev/null 2>&1; then
    ok "egress-proxy exists"
  else
    would "start egress proxy and internal network via bin/egress-up"
  fi

  step "Internal Forgejo, bot accounts, repository, protected main"
  if command -v docker >/dev/null 2>&1 && docker inspect forgejo >/dev/null 2>&1; then
    ok "forgejo container exists"
    would "verify bot accounts and protected main via scripts/forge-bootstrap"
  else
    would "bootstrap Forgejo container, bot accounts, repository, and branch protection via scripts/forge-bootstrap"
  fi

  step "Worker image (Antigravity CLI; the API-key image is built when the first such worker is added)"
  if command -v docker >/dev/null 2>&1 && docker image inspect sandbox-worker:1.2.11 >/dev/null 2>&1; then
    ok "sandbox-worker:1.2.11 exists"
  else
    would "build sandbox-worker:1.2.11 via WORKER_N=0 docker compose -f worker/docker-compose.yml build"
  fi

  step "Checks"
  python3 bin/foreman --selftest | tail -1

  step "Services: foreman + dashboard (systemd)"
  if [ ! -d /run/systemd/system ]; then
    warn "no systemd on this machine: the services cannot be installed (run bin/foreman run and python3 web/server.py yourself)"
  elif command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet agent-forge-foreman 2>/dev/null; then
    ok "already installed (scripts/install-services re-installs)"
  else
    would "install and start systemd services (foreman + dashboard) via sudo scripts/install-services"
  fi

  step "Health and the forge shortcut"
  would "verify system health via python3 bin/doctor --quick"
  [ "$LINK" = 0 ] || would "link ./forge as ~/.local/bin/agent-forge (and ~/.local/bin/forge when that name is free)"

  printf '\nPlan complete - 0 side effects\n'
  exit 0
fi

[ "$(id -u)" != 0 ] || die "Run it as your normal user, not root (it uses sudo where needed)."
if [ -n "${FORGE_RESUME:-}" ]; then
  N=1
  printf '\n(continuing inside the docker group)\n'
else
  step "Preflight"
  command -v apt-get >/dev/null || die "This installer supports Ubuntu / Debian (apt)." "On other systems follow docs/SETUP.md (every step by hand)."
  command -v sudo >/dev/null || die "sudo is needed." "su -c 'apt-get install -y sudo'"
  ok "$(. /etc/os-release 2>/dev/null && printf '%s' "${PRETTY_NAME:-Linux}" || printf Linux) ($(uname -m))"
  preflight_resources
  preflight_tools
fi
# One password prompt up front, kept warm for the long steps (a worker image build can outlast sudo's 15 minutes).
if ! sudo -n true 2>/dev/null; then
  echo "  sudo needs your password once:"
  sudo -v
  ( while kill -0 "$$" 2>/dev/null; do sudo -n true 2>/dev/null || exit 0; sleep 50; done ) &
fi
[ -n "${FORGE_RESUME:-}" ] || ok "sudo works"

step "Docker"
if ! docker info >/dev/null 2>&1; then
  sudo systemctl start docker >/dev/null 2>&1 || true
fi
if ! docker info >/dev/null 2>&1; then
  if ! command -v docker >/dev/null || ! id -nG | grep -qw docker; then
    sudo env SYSTEM_UPGRADE=0 bash scripts/vm-baseline-ubuntu.sh
  fi
  if ! docker info >/dev/null 2>&1; then
    [ -z "${FORGE_RESUME:-}" ] || die "Docker is installed but this user cannot use it." "Log out and in again (or run: newgrp docker), then run the installer again." "Check: docker info"
    # the docker group is new for this login session: continue the installation inside it
    exec sg docker -c "$(printf '%q ' env FORGE_RESUME=1 "$ROOT/scripts/install.sh" "$@")"
  fi
fi
ok "docker $(docker version --format '{{.Server.Version}}') usable without sudo"

step "Configuration (examples copied only where nothing exists yet)"
mkdir -p etc
chmod 700 etc
for f in limits foreman forge; do
  [ -f "etc/$f.conf" ] || { cp "etc/$f.example.conf" "etc/$f.conf"; ok "etc/$f.conf"; }
  chmod 600 "etc/$f.conf"
done
[ -f etc/team-rules.md ] || { cp etc/team-rules.example.md etc/team-rules.md; ok "etc/team-rules.md (edit: your stack and conventions)"; }
chmod 600 etc/team-rules.md
if [ "$DEMO" = 1 ] && [ ! -f docs/vision.md ]; then cp examples/demo/vision.md docs/vision.md; ok "demo vision in docs/vision.md"; fi
SECRETS="${SANDBOX_SECRETS:-$HOME/.config/sandbox/secrets}"
mkdir -p "$SECRETS"
chmod 700 "$SECRETS"
ok "configuration in etc/"

step "Egress proxy and the workers' internal network"
docker inspect egress-proxy >/dev/null 2>&1 && ok "egress-proxy exists" || bin/egress-up

step "Internal Forgejo, bot accounts, repository, protected main"
scripts/forge-bootstrap

step "Worker image (Antigravity CLI; the API-key image is built when the first such worker is added)"
docker image inspect sandbox-worker:1.2.11 >/dev/null 2>&1 && ok "sandbox-worker:1.2.11 exists" || WORKER_N=0 docker compose -f worker/docker-compose.yml build

step "Checks"
python3 bin/foreman --selftest | tail -1

step "Services: foreman + dashboard (systemd)"
if [ ! -d /run/systemd/system ]; then
  warn "no systemd on this machine, so the services are not installed. Start them yourself:"
  warn "  python3 bin/foreman run &      and      python3 web/server.py &"
elif systemctl is-active --quiet agent-forge-foreman 2>/dev/null; then
  ok "already installed (scripts/install-services re-installs)"
else
  sudo scripts/install-services
fi

step "Health and the forge shortcut"
python3 bin/doctor --quick || true
wait_dashboard
[ "$LINK" = 0 ] || link_cli

secs=$(( $(date +%s) - T0 ))
printf '\n✔ Agent-Forge %s is installed (%dm%02ds).\n\n' "$VERSION" "$((secs / 60))" "$((secs % 60))"
[ -x ./forge ] && ./forge url || echo "Dashboard: http://127.0.0.1:8171"
cat <<EOF

Next, in the dashboard:
  1. Choose where the code lives: internal only, or your GitHub / GitLab repository (a token for just that repository).
  2. Settings → "+ Add worker": an Antigravity account (signed in from the browser) or an API key (Claude Code, Codex, Gemini CLI).
  3. Overview → "New goal" - or let the autopilot work from your vision (docs/vision.md$([ "$DEMO" = 1 ] && echo ", the demo is in place")).
Optional: set NOTIFY_URL (ntfy / Slack / Discord) in Settings → Project to be told when goals are done.

Day to day:  forge status · forge goal "..." · forge doctor · forge logs -f · forge update · forge uninstall
Read SECURITY.md before you publish the dashboard anywhere.
EOF
