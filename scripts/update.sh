#!/usr/bin/env bash
# update.sh - bring this checkout to the newest version of its upstream branch (main) and restart what runs from it.
# Usage: forge update [--check]        (--check: show what would change, touch nothing)
# Fast-forwards the checkout to its upstream branch (never merges, never discards local work: a checkout with edited tracked files
# is refused). The new version must pass its own selftest in a scratch worktree BEFORE anything changes. Then, only for what the
# update touched: worker image, egress proxy, Forgejo; services are re-installed and restarted. Worker runs keep going (they are
# separate processes; the services use KillMode=process). Configuration in etc/ and your data are never touched.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
CHECK=0
for a in "$@"; do
  case "$a" in
    --check) CHECK=1 ;;
    -h|--help) sed -n '2,3p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'Unknown option: %s\n(forge update [--check])\n' "$a" >&2; exit 2 ;;
  esac
done
step() { printf '\n==> %s\n' "$*"; }
ok()   { printf '  ✔ %s\n' "$*"; }
warn() { printf '  ⚠ %s\n' "$*"; }
would() { printf '  Would: %s\n' "$*"; }
die()  { printf '\n✘ %s\n' "$1" >&2; shift; for h in "$@"; do printf '    → %s\n' "$h" >&2; done; exit 1; }
trap 'printf "\n✘ failed at line %s: %s - nothing is half-way: fix the cause and run forge update again\n" "$LINENO" "$BASH_COMMAND" >&2' ERR
WT=""
trap '[ -z "$WT" ] || { git worktree remove --force "$WT" >/dev/null 2>&1 || true; git worktree prune >/dev/null 2>&1 || true; }' EXIT

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "This is not a git checkout, so it cannot update itself." "Clone the repository again, or download the new release and run its forge install."
BRANCH="$(git symbolic-ref --short -q HEAD || true)"
[ -n "$BRANCH" ] || die "The checkout is on a detached HEAD." "git switch main"
git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1 || die "Branch $BRANCH has no upstream to update from." "git branch --set-upstream-to=origin/main $BRANCH"
if ! git diff --quiet HEAD --; then
  die "Tracked files are edited in this checkout, so an update could clash with them:" \
      "$(git diff --name-only HEAD -- | head -5 | tr '\n' ' ')" "git stash   (then forge update, then git stash pop)"
fi

step "Looking for a new version"
git fetch --quiet --tags || die "git fetch failed - no network, or the remote is gone."
OLD="$(git rev-parse HEAD)"; NEW="$(git rev-parse '@{u}')"
OLDV="$(cat VERSION 2>/dev/null || echo unknown)"
if [ "$OLD" = "$NEW" ]; then ok "already up to date (version $OLDV, $(git rev-parse --short HEAD))"; exit 0; fi
git merge-base --is-ancestor "$OLD" "$NEW" || die "This checkout has commits that are not upstream, so it cannot fast-forward." "git log @{u}..HEAD    (push them, or move them to another branch)"
NEWV="$(git show "$NEW:VERSION" 2>/dev/null || echo unknown)"
ok "$OLDV -> $NEWV ($(git rev-list --count "$OLD..$NEW") commits)"
git --no-pager log --no-merges --pretty='    %h %s' "$OLD..$NEW" | head -15
CHANGED="$(git diff --name-only "$OLD" "$NEW")"
touched() { printf '%s\n' "$CHANGED" | grep -qE "$1"; }

step "What the update touches"
REBUILD_WORKER=0; REBUILD_EGRESS=0; REBOOT_FORGEJO=0
touched '^worker/' && REBUILD_WORKER=1
touched '^egress/' && REBUILD_EGRESS=1
touched '^forgejo/' && REBOOT_FORGEJO=1
[ "$REBUILD_WORKER" = 0 ] || { [ "$CHECK" = 1 ] && would "rebuild the worker images (worker/ changed)" || echo "  • worker images are rebuilt (worker/ changed)"; }
[ "$REBUILD_EGRESS" = 0 ] || { [ "$CHECK" = 1 ] && would "recreate the egress proxy (egress/ changed; allow-list edits you made in the dashboard are kept)" || echo "  • egress proxy is recreated (egress/ changed)"; }
[ "$REBOOT_FORGEJO" = 0 ] || { [ "$CHECK" = 1 ] && would "re-run scripts/forge-bootstrap (forgejo/ changed)" || echo "  • Forgejo is brought to the pinned version (forgejo/ changed)"; }
touched '^etc/.*example' && warn "etc/*.example.* changed: new settings appear in the dashboard; compare with your etc/*.conf if you want the new defaults"
touched '^scripts/install\.sh$' && echo "  • install.sh changed (only matters for a fresh install)"
if [ "$CHECK" = 1 ]; then
  would "run the new version's selftest in a scratch worktree first"
  would "fast-forward $BRANCH to $(git rev-parse --short "$NEW")"
  would "re-install and restart the foreman and dashboard services"
  printf '\nPlan complete - 0 side effects\n'
  exit 0
fi

step "Selftest of the new version (scratch worktree, nothing changed yet)"
WT="$(mktemp -d "${TMPDIR:-/tmp}/af-update.XXXXXX")"
git worktree add --quiet --detach "$WT" "$NEW"
python3 "$WT/bin/foreman" --selftest | tail -1 | grep -q "ALL OK" || die "The new version fails its own selftest, so it was NOT installed." "Report it: https://github.com/MatakLA/Agent-forge/issues"
ok "ALL OK"
git worktree remove --force "$WT" >/dev/null 2>&1 || true; git worktree prune; WT=""

step "Updating the checkout"
git merge --ff-only --quiet "$NEW"
ok "now at $NEWV ($(git rev-parse --short HEAD))"

if [ "$REBUILD_EGRESS" = 1 ] && command -v docker >/dev/null 2>&1 && docker inspect egress-proxy >/dev/null 2>&1; then
  step "Egress proxy"
  mode="$(docker inspect egress-proxy --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | sed -n 's/^EGRESS_MODE=//p' | head -1)"
  bin/egress-up "${mode:-enforce}"
fi
if [ "$REBOOT_FORGEJO" = 1 ] && command -v docker >/dev/null 2>&1 && docker inspect forgejo >/dev/null 2>&1; then
  step "Forgejo"
  scripts/forge-bootstrap
fi
if [ "$REBUILD_WORKER" = 1 ] && command -v docker >/dev/null 2>&1; then
  step "Worker images"
  WORKER_N=0 docker compose -f worker/docker-compose.yml build
  warn "running workers keep the old image until they are recreated: bin/worker-up N (when no run is in progress)"
fi

if [ -d /run/systemd/system ] && systemctl cat agent-forge-foreman >/dev/null 2>&1; then
  step "Services"
  sudo scripts/install-services
  units="$(systemctl list-units 'agent-forge-*.service' --plain --no-legend --state=active 2>/dev/null | awk '{print $1}' | tr '\n' ' ')"
  [ -z "$units" ] || { sudo systemctl restart $units; ok "restarted: $units"; }
else
  warn "no agent-forge systemd services found - restart bin/foreman run and web/server.py yourself if they run"
fi

step "Health"
python3 bin/doctor --quick || true
printf '\n✔ Agent-Forge %s (was %s).  Changelog: CHANGELOG.md\n' "$NEWV" "$OLDV"
