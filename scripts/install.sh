#!/usr/bin/env bash
# install.sh - from a fresh Ubuntu machine (ideally a dedicated VM, 4+ vCPU, 8+ GB RAM) to a running Agent-Forge in one command.
# Run it as your normal user (it asks for sudo where it must). Safe to re-run: every step skips what is already done.
#
#   git clone <agent-forge repository> agent-forge && cd agent-forge && scripts/install.sh [--demo] [--check]
#
# Steps: Docker (scripts/vm-baseline-ubuntu.sh) -> configuration from the examples -> egress proxy and internal network ->
# internal Forgejo with bot accounts and a protected main -> worker image -> foreman + dashboard services -> bin/doctor.
# --check dry-run mode: prints every planned step without modifying the system; 0 side effects.
# --demo  puts the example vision (examples/demo) in place, so the autopilot has something to build right away.
# Afterwards open the dashboard: choose where the code lives (internal / GitHub / GitLab), add workers, enter a goal.
set -Eeuo pipefail
umask 077
ROOT="${FORGE_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"; cd "$ROOT"
DEMO=0
CHECK=0
for a in "$@"; do
  [ "$a" = "--demo" ] && DEMO=1
  [ "$a" = "--check" ] && CHECK=1
  if [ "$a" = "-h" ] || [ "$a" = "--help" ]; then
    cat <<'EOF'
Usage: scripts/install.sh [--demo] [--check]

From a fresh Ubuntu machine to a running Agent-Forge in one command.

Options:
  --check    Dry-run mode: prints every planned step without modifying the system
  --demo     Install example demo vision (examples/demo)
  -h, --help Show this help message
EOF
    exit 0
  fi
done
step() { printf '\n==> %s\n' "$*"; }
ok()   { printf '  ✔ %s\n' "$*"; }
die()  { printf '\n✘ %s\n' "$1"; shift; for h in "$@"; do printf '    → %s\n' "$h"; done; exit 1; }
trap 'printf "\n✘ failed at line %s: %s - fix the cause and run scripts/install.sh again (it is safe)\n" "$LINENO" "$BASH_COMMAND"' ERR

if [ "$CHECK" = 1 ]; then
  would() { printf '  Would: %s\n' "$*"; }

  # Prerequisite-detection logic
  command -v apt-get >/dev/null 2>&1 && ok "Ubuntu / Debian (apt-get available)" || die "This installer supports Ubuntu / Debian (apt). On other systems follow docs/SETUP.md."
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
  avail_gb="$(df -BG "$ROOT" 2>/dev/null | awk 'NR==2 {sub("G","",$4); print $4}')"
  if [ -n "$avail_gb" ]; then
    ok "disk space: ${avail_gb} GB available"
  fi

  step "Docker"
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    ok "docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || true) usable without sudo"
  else
    if ! command -v docker >/dev/null 2>&1 || ! id -nG 2>/dev/null | grep -qw docker; then
      would "install Docker and base tools via sudo bash scripts/vm-baseline-ubuntu.sh"
      would "add user $(id -un) to docker group"
    else
      would "start Docker service or continue session in docker group via sg"
    fi
  fi
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die "Python 3.10+ is needed."
  ok "python $(python3 -c 'import sys; print(sys.version.split()[0])') (3.10+)"
  git --version | awk '{split($3, v, "."); exit !(v[1] > 2 || (v[1] == 2 && v[2] >= 31))}' || die "git 2.31+ is needed."
  ok "git $(git --version | awk '{print $3}') (2.31+)"

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
    would "build sandbox-worker:1.2.11 via docker compose -f worker/docker-compose.yml build"
  fi

  step "Checks"
  python3 bin/foreman --selftest | tail -1

  step "Services: foreman + dashboard (systemd)"
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet agent-forge-foreman 2>/dev/null; then
    ok "already installed (scripts/install-services re-installs)"
  else
    would "install and start systemd services (foreman + dashboard) via sudo scripts/install-services"
  fi

  step "Health"
  would "verify system health via python3 bin/doctor --quick"

  printf '\nPlan complete - 0 side effects\n'
  exit 0
fi

[ "$(id -u)" != 0 ] || die "Run it as your normal user, not root (it uses sudo where needed)."
command -v apt-get >/dev/null || die "This installer supports Ubuntu / Debian (apt). On other systems follow docs/SETUP.md."
command -v sudo >/dev/null || die "sudo is needed."

step "Docker"
if ! docker info >/dev/null 2>&1; then
  sudo systemctl start docker >/dev/null 2>&1 || true
fi
if ! docker info >/dev/null 2>&1; then
  if ! command -v docker >/dev/null || ! id -nG | grep -qw docker; then
    sudo bash scripts/vm-baseline-ubuntu.sh
  fi
  if ! docker info >/dev/null 2>&1; then
    # the docker group is new for this login session: continue the installation inside it
    exec sg docker -c "$(printf '%q ' "$0" "$@")"
  fi
fi
ok "docker $(docker version --format '{{.Server.Version}}') usable without sudo"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die "Python 3.10+ is needed."
git --version | awk '{split($3, v, "."); exit !(v[1] > 2 || (v[1] == 2 && v[2] >= 31))}' || die "git 2.31+ is needed."

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
docker image inspect sandbox-worker:1.2.11 >/dev/null 2>&1 && ok "sandbox-worker:1.2.11 exists" || docker compose -f worker/docker-compose.yml build

step "Checks"
python3 bin/foreman --selftest | tail -1

step "Services: foreman + dashboard (systemd)"
if systemctl is-active --quiet agent-forge-foreman 2>/dev/null; then ok "already installed (scripts/install-services re-installs)"; else sudo scripts/install-services; fi

step "Health"
python3 bin/doctor --quick || true

cat <<EOF

✔ Agent-Forge is installed.

Next, in the dashboard - http://127.0.0.1:8171 on this machine; from your laptop:  ssh -L 8171:127.0.0.1:8171 <this machine>
  1. Choose where the code lives: internal only, or your GitHub / GitLab repository (a token for just that repository).
  2. Settings → "+ Add worker": an Antigravity account (signed in from the browser) or an API key (Claude Code, Codex, Gemini CLI).
  3. Overview → "New goal" - or let the autopilot work from your vision (docs/vision.md$([ "$DEMO" = 1 ] && echo ", the demo is in place")).
Optional: set NOTIFY_URL (ntfy / Slack / Discord) in Settings → Project to be told when goals are done.
Read SECURITY.md before you publish the dashboard anywhere.
EOF
