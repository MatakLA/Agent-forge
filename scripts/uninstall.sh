#!/usr/bin/env bash
# uninstall.sh - remove Agent-Forge from this machine.
# Usage: forge uninstall [--purge] [--yes] [--check]
#   (default)  stops and removes the systemd services, the worker / egress-proxy / Forgejo containers, the workers' internal network and
#              the shortcuts in ~/.local/bin. KEEPS your data - Forgejo repositories, worker logins, runs/, etc/, projects/, secrets - so a
#              later forge install picks up where you left off.
#   --purge    ALSO deletes that data and the images. Irreversible. You type "purge" to confirm.
#   --yes      no questions (scripts and CI).
#   --check    print what would be done and touch nothing.
# Never touches: Docker itself, the apt packages, this checkout (delete the directory yourself), anything not named agent-forge-*,
# worker-N, egress-proxy, forgejo, sandbox-* or workers_internal.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
PURGE=0; YES=0; CHECK=0
for a in "$@"; do
  case "$a" in
    --purge) PURGE=1 ;;
    --yes|-y) YES=1 ;;
    --check) CHECK=1 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'Unknown option: %s\n(forge uninstall [--purge] [--yes] [--check])\n' "$a" >&2; exit 2 ;;
  esac
done
step() { printf '\n==> %s\n' "$*"; }
ok()   { printf '  ✔ %s\n' "$*"; }
warn() { printf '  ⚠ %s\n' "$*"; }
die()  { printf '\n✘ %s\n' "$1" >&2; shift; for h in "$@"; do printf '    → %s\n' "$h" >&2; done; exit 1; }
# do <description> <command...>: runs it, or only prints it with --check
do_() { local d="$1"; shift; if [ "$CHECK" = 1 ]; then printf '  Would: %s\n' "$d"; else "$@" && ok "$d" || warn "$d - failed (continuing)"; fi; }
trap 'printf "\n✘ failed at line %s: %s - run forge uninstall again, it continues where it stopped\n" "$LINENO" "$BASH_COMMAND" >&2' ERR

SECRETS="${SANDBOX_SECRETS:-$HOME/.config/sandbox/secrets}"
case "$SECRETS" in ""|/|"$HOME"|"$HOME"/|/etc|/home|/root) die "SANDBOX_SECRETS points at '$SECRETS' - refusing to treat that as a secrets directory." ;; esac
HAVE_DOCKER=0; command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 && HAVE_DOCKER=1
UNIT_DIR="${AF_UNIT_DIR:-/etc/systemd/system}"
HAVE_SYSTEMD=0; command -v systemctl >/dev/null 2>&1 && { [ -d /run/systemd/system ] || [ -n "${AF_UNIT_DIR:-}" ]; } && HAVE_SYSTEMD=1

units=""
if [ "$HAVE_SYSTEMD" = 1 ]; then
  for f in "$UNIT_DIR"/agent-forge-*.service; do [ -e "$f" ] && units="$units$(basename "$f") "; done
fi
workers=""; volumes=""; images=""
if [ "$HAVE_DOCKER" = 1 ]; then
  workers="$(docker ps -a --format '{{.Names}}' | grep -E '^worker-[0-9]+$' | tr '\n' ' ' || true)"
  volumes="$(docker volume ls --format '{{.Name}}' | grep -E '^(worker_worker-[0-9]+-home|egress_logs|forgejo_forgejo-data)$' | tr '\n' ' ' || true)"
  images="$(docker image ls --format '{{.Repository}}:{{.Tag}}' | grep -E '^(sandbox-[a-z-]+:|codeberg\.org/forgejo/forgejo:)' | tr '\n' ' ' || true)"
fi

if [ "$CHECK" = 1 ]; then echo "Dry run: nothing is changed."; fi
echo "This machine: ${units:+services: ${units}· }${workers:+workers: ${workers}· }${volumes:+data volumes: ${volumes}· }checkout: $ROOT"

if [ "$CHECK" = 0 ] && [ "$YES" = 0 ]; then
  if [ "$PURGE" = 1 ]; then
    cat <<EOT

--purge DELETES, for good:
  • the Forgejo data (every repository and pull request inside the internal Forgejo)
  • every worker's home volume (their Antigravity logins and clones)
  • runs/ (the history and audit logs), projects/, etc/*.conf and etc/team-rules.md of $ROOT
  • the secrets directory $SECRETS (bot tokens, API keys)
  • the Docker images built or pulled for Agent-Forge
Code that was already pushed to GitHub / GitLab stays there.
EOT
    printf 'Type "purge" to continue: '; read -r ans || ans=""; [ "$ans" = purge ] || die "Not confirmed - nothing was changed."
  else
    printf '\nRemove the services and containers (your data stays)? [y/N] '; read -r ans || ans=""
    case "$ans" in y|Y|yes|YES) ;; *) die "Not confirmed - nothing was changed." ;; esac
  fi
fi

if [ -n "$units" ]; then
  step "Services"
  for u in $units; do do_ "stop and disable $u" sudo systemctl disable --now "$u"; done
  files=(); for u in $units; do files+=("$UNIT_DIR/$u"); done
  do_ "delete the unit files" sudo rm -f "${files[@]}"
  do_ "reload systemd" sudo systemctl daemon-reload
fi

if [ "$HAVE_DOCKER" = 1 ]; then
  step "Containers and network"
  for w in $workers; do do_ "remove $w" docker rm -f "$w"; done
  if docker inspect forgejo >/dev/null 2>&1; then
    do_ "remove Forgejo" env COMPOSE_PROJECT_NAME=forgejo docker compose -p forgejo -f forgejo/docker-compose.yml down
  fi
  if docker inspect egress-proxy >/dev/null 2>&1; then
    do_ "remove the egress proxy" env EGRESS_MODE=enforce docker compose -f egress/docker-compose.yml down
  fi
  if docker network inspect workers_internal >/dev/null 2>&1; then
    do_ "remove the network workers_internal" docker network rm workers_internal
  fi
  if [ "$PURGE" = 1 ]; then
    step "Docker data and images (purge)"
    for v in $volumes; do do_ "delete volume $v" docker volume rm "$v"; done
    for i in $images; do do_ "delete image $i" docker image rm "$i"; done
    if docker network inspect egress_out >/dev/null 2>&1; then do_ "remove the network egress_out" docker network rm egress_out; fi
  fi
else
  warn "Docker is not usable here, so containers, volumes and images were not looked at."
fi

step "Shortcuts"
for n in forge agent-forge; do
  l="$HOME/.local/bin/$n"
  if [ -L "$l" ] && [ "$(readlink -f "$l")" = "$ROOT/forge" ]; then do_ "remove $l" rm -f "$l"; fi
done

if [ "$PURGE" = 1 ]; then
  step "Local data (purge)"
  for p in runs projects etc/foreman.conf etc/limits.conf etc/forge.conf etc/team-rules.md etc/lessons.md etc/workers.conf egress/allowlist.local.txt egress/local-endpoint.conf docs/vision.md; do
    [ -e "$p" ] && do_ "delete $p" rm -rf -- "$p" || true
  done
  [ -d "$SECRETS" ] && do_ "delete $SECRETS" rm -rf -- "$SECRETS" || true
fi

if [ "$CHECK" = 1 ]; then printf '\nPlan complete - 0 side effects\n'; exit 0; fi
printf '\n✔ Agent-Forge is removed%s.\n' "$([ "$PURGE" = 1 ] && echo ' together with its data' || echo ' (data kept - forge install brings it back; forge uninstall --purge deletes it)')"
printf 'This checkout (%s) is still here: delete the directory yourself if you are done with it.\n' "$ROOT"
