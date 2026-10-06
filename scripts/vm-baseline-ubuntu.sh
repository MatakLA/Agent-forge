#!/usr/bin/env bash
# vm-baseline-ubuntu.sh — prepares the machine (ideally a dedicated VM) that runs Agent-Forge: updates, base tools,
# Docker Engine from Docker's signed repository (key checked by fingerprint), log rotation, your user in group docker.
#
# Run on that machine (Ubuntu 22.04+):
#   sudo bash scripts/vm-baseline-ubuntu.sh 2>&1 | tee vm-baseline.log
# Safe to re-run. Takes 3–10 minutes.
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l NEEDRESTART_SUSPEND=1 LC_ALL=C.UTF-8
trap 'printf "\n✘ Failed at line %s: %s (exit %s). Fix the cause and re-run — it is safe.\n" "$LINENO" "$BASH_COMMAND" "$?"' ERR

DOCKER_KEY_FPR="${DOCKER_KEY_FPR:-9DC858229FC7DD38854AE2D88D81803C0EBFCD88}"
USER_NAME="${SUDO_USER:-dev}"

step() { printf '\n==> %s\n' "$*"; }
ok()   { printf '  ✔ %s\n' "$*"; }
warn() { printf '  ⚠ %s\n' "$*"; }
die()  { printf '\n✘ ERROR: %s\n' "$1"; shift; for h in "$@"; do printf '    → %s\n' "$h"; done; exit 1; }
aptg() { apt-get -o DPkg::Lock::Timeout=600 -o Dpkg::Use-Pty=0 "$@"; }

[[ $EUID -eq 0 ]] || die "Run it with sudo:  sudo bash scripts/vm-baseline-ubuntu.sh"

step "System update"
aptg update -q
aptg -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold full-upgrade -y -q
ok "Up to date"

step "Base tools"
aptg install -y -q git jq tmux make unzip ripgrep python3-venv ca-certificates curl gnupg
ok "git, jq, tmux, make, unzip, ripgrep, python3-venv"

step "Automatic security updates (no automatic reboot)"
aptg install -y -q unattended-upgrades
systemctl enable --now unattended-upgrades.service >/dev/null 2>&1 || true
reboot_auto="$(apt-config dump Unattended-Upgrade::Automatic-Reboot 2>/dev/null | awk -F'"' '{print $2}')"
ok "unattended-upgrades on · automatic reboot: ${reboot_auto:-false}"

step "Docker Engine (official repository)"
for p in docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc; do
  if dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'install ok installed'; then
    aptg remove -y -q "$p"; warn "Removed conflicting package $p"
  fi
done
mkdir -p /etc/apt/keyrings; chmod 0755 /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc.tmp \
  || die "Could not download Docker's signing key." "curl -I https://download.docker.com"
fpr="$(gpg --show-keys --with-colons /etc/apt/keyrings/docker.asc.tmp 2>/dev/null | awk -F: '$1=="fpr" {print $10; exit}')"
if [[ $fpr != "$DOCKER_KEY_FPR" ]]; then
  rm -f /etc/apt/keyrings/docker.asc.tmp
  die "Docker key fingerprint is '${fpr:-none}', expected $DOCKER_KEY_FPR." \
    "Check https://docs.docker.com/engine/install/ubuntu/ — if Docker rotated its key, re-run:" \
    "sudo env DOCKER_KEY_FPR=<new-fingerprint> bash scripts/vm-baseline-ubuntu.sh"
fi
mv -f /etc/apt/keyrings/docker.asc.tmp /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
ok "Docker key fingerprint verified: $fpr"
codename="$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")"
cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $codename
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
aptg update -q
aptg install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
ok "$(docker --version) · $(docker compose version)"

step "Docker log rotation"
mkdir -p /etc/docker
want='{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}'
if [[ -f /etc/docker/daemon.json ]] && ! cmp -s <(printf '%s\n' "$want") /etc/docker/daemon.json; then
  cp -p /etc/docker/daemon.json "/etc/docker/daemon.json.bak.$(date +%s)"
  warn "Replaced an existing /etc/docker/daemon.json (backup kept next to it)"
fi
printf '%s\n' "$want" >/etc/docker/daemon.json
systemctl restart docker
ok "json-file logs, max 3 × 10 MB per container"

step "User $USER_NAME in group docker"
usermod -aG docker "$USER_NAME"
ok "Added (takes effect at your next login)"

step "Test"
out="$(docker run --rm hello-world 2>&1 || true)"
if grep -q 'Hello from Docker' <<<"$out"; then ok "docker run hello-world works"; else printf '%s\n' "$out"; die "hello-world failed" "journalctl -u docker -n 50"; fi
docker image rm hello-world >/dev/null 2>&1 || true

step "Done"
if [[ -f /var/run/reboot-required ]]; then
  warn "A reboot is recommended (kernel update): sudo reboot   — then reconnect with ssh devbox"
fi
cat <<'EOF'
  Next:
    1. Log out and back in (exit, then ssh devbox) so the docker group applies:  docker ps  must work without sudo.
    2. Ask the owner for a snapshot on the host:  ./run vm-snapshot create baseline "after V1"
    3. Continue with docs/SETUP.md (step 2: Forgejo).
EOF
