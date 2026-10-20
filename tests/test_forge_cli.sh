#!/usr/bin/env bash
# test_forge_cli.sh - the `forge` front door and its lifecycle scripts (update, uninstall) against stubs: no Docker, no sudo, no network.
# Real git (a local "origin"), real python3; docker / sudo / systemctl are stubs that only log their arguments.
# Public interface: bash tests/test_forge_cli.sh
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d "${TMPDIR:-/tmp}/af-forge-cli.XXXXXX")"
trap 'rm -rf "$T"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
calls_has() { grep -qF -- "$1" "$T/calls.log" || { echo "--- calls:"; cat "$T/calls.log"; fail "missing call: $1"; }; }
calls_lacks() { ! grep -qF -- "$1" "$T/calls.log" || { echo "--- calls:"; cat "$T/calls.log"; fail "unexpected call: $1"; }; }

# ---------- stubs
mkdir "$T/stub"
cat > "$T/stub/docker" <<'EOF'
#!/usr/bin/env bash
echo "docker $* [WORKER_N=${WORKER_N:-}]" >> "$STUB_CALLS"
case "$1" in
  info) exit 0 ;;
  ps) printf '%s\n' worker-1 worker-2 other-thing ;;
  volume) [ "$2" = ls ] && printf '%s\n' worker_worker-1-home forgejo_forgejo-data egress_logs unrelated_volume ;;
  image) [ "$2" = ls ] && printf '%s\n' sandbox-worker:1.2.11 sandbox-egress-proxy:7.2 codeberg.org/forgejo/forgejo:16.0.5-rootless nginx:latest ;;
  inspect) [ "$2" = sandbox-worker:1.2.11 ] && exit 1; exit 0 ;;
  network) exit 0 ;;
esac
exit 0
EOF
cat > "$T/stub/sudo" <<'EOF'
#!/usr/bin/env bash
echo "sudo $*" >> "$STUB_CALLS"
exit 0
EOF
cat > "$T/stub/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >> "$STUB_CALLS"
case "$1" in list-units) printf 'agent-forge-foreman.service loaded active running x\nagent-forge-web.service loaded active running y\n' ;; esac
exit 0
EOF
printf '#!/usr/bin/env bash\nexit 0\n' > "$T/stub/apt-get"
chmod +x "$T/stub/"*
export STUB_CALLS="$T/calls.log"; : > "$STUB_CALLS"
export HOME="$T/home"; mkdir -p "$HOME/.local/bin"
export PATH="$T/stub:$PATH"
unset BIND_PORT SANDBOX_SECRETS FORGE_DIR

# ---------- 1. forge itself
F="$ROOT/forge"
has() { grep -qE -- "$2" <<<"$1"; }
out="$("$F" help)"
has "$out" "forge install" || fail "help lacks 'forge install'"
has "$out" "forge uninstall" || fail "help lacks 'forge uninstall'"
has "$("$F" version)" "^Agent-Forge $(cat "$ROOT/VERSION")" || fail "version does not show VERSION"
has "$("$F" url)" "http://127.0.0.1:8171" || fail "url lacks the default dashboard address"
has "$(BIND_PORT=9123 "$F" url)" "http://127.0.0.1:9123" || fail "url ignores BIND_PORT"
if "$F" no-such-command >/dev/null 2>&1; then fail "unknown command must fail"; fi
if "$F" logs no-such-service >/dev/null 2>&1; then fail "logs of an unknown service must fail"; fi
has "$("$F" install --check)" "Plan complete - 0 side effects" || fail "forge install --check"
has "$("$F" update --help)" "forge update" || fail "update --help"
has "$("$F" uninstall --help)" "forge uninstall" || fail "uninstall --help"
echo "forge cli: ok"
: > "$T/calls.log"

# ---------- 1b. the curl bootstrap (install.sh in the repository root)
has "$(bash "$ROOT/install.sh" --check)" "Plan complete - 0 side effects" || fail "install.sh from a checkout does not hand over to scripts/install.sh"
# clone from a snapshot of the working tree, so the test does not depend on what is committed (or on .git being there at all)
snap="$T/snapshot"; mkdir "$snap"
if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  git -C "$ROOT" ls-files -co --exclude-standard -z | tar -C "$ROOT" --null -T - -cf - | tar -C "$snap" -xf -
else
  tar -C "$ROOT" -cf - . | tar -C "$snap" -xf -
fi
git -C "$snap" init -q -b main
git -C "$snap" -c user.email=t@example.com -c user.name=t add -A
git -C "$snap" -c user.email=t@example.com -c user.name=t commit -q -m snapshot
export FORGE_REPO_URL="$snap" FORGE_DIR="$T/boot-clone"
# piped like curl does: the script arrives on stdin, the checkout does not exist yet
out="$(cat "$ROOT/install.sh" | bash -s -- --check 2>&1)" || { echo "$out"; fail "piped bootstrap failed"; }
has "$out" "Downloading Agent-Forge" || fail "bootstrap did not clone: $out"
has "$out" "Plan complete - 0 side effects" || fail "bootstrap did not reach scripts/install.sh: $out"
[ -x "$T/boot-clone/scripts/install.sh" ] || fail "no checkout after the bootstrap"
out="$(cat "$ROOT/install.sh" | bash -s -- --check 2>&1)" || { echo "$out"; fail "bootstrap re-run failed"; }
has "$out" "Updating the checkout" || fail "bootstrap re-run did not reuse the checkout: $out"
mkdir -p "$T/not-forge" && echo x > "$T/not-forge/file"
if cat "$ROOT/install.sh" | FORGE_DIR="$T/not-forge" bash -s -- --check >/dev/null 2>&1; then fail "bootstrap must not clone over a foreign directory"; fi
unset FORGE_REPO_URL FORGE_DIR
echo "bootstrap: ok"
: > "$T/calls.log"

# ---------- 2. update.sh against a local origin
git init -q --bare "$T/origin.git"
git -C "$T/origin.git" symbolic-ref HEAD refs/heads/main
seed="$T/seed"; mkdir -p "$seed/bin" "$seed/scripts" "$seed/worker"
git -C "$T" init -q -b main "$seed"
git -C "$seed" config user.email t@example.com; git -C "$seed" config user.name t
cp "$ROOT/scripts/update.sh" "$seed/scripts/update.sh"
printf '1.0.0\n' > "$seed/VERSION"
printf '#!/usr/bin/env python3\nimport sys\nprint("foreman selftest: ALL OK" if "--selftest" in sys.argv else "")\n' > "$seed/bin/foreman"
printf '#!/usr/bin/env python3\nprint("doctor: ok")\n' > "$seed/bin/doctor"
printf '#!/usr/bin/env bash\necho installed-services\n' > "$seed/scripts/install-services"
printf 'FROM scratch\n' > "$seed/worker/Dockerfile"
printf 'name: worker-${WORKER_N:?}\n' > "$seed/worker/docker-compose.yml"
chmod +x "$seed/bin/foreman" "$seed/bin/doctor" "$seed/scripts/install-services"
git -C "$seed" add -A; git -C "$seed" commit -q -m "v1.0.0"
git -C "$seed" remote add origin "$T/origin.git"; git -C "$seed" push -q -u origin main
git clone -q "$T/origin.git" "$T/inst"
git clone -q "$T/origin.git" "$T/dev"
git -C "$T/dev" config user.email t@example.com; git -C "$T/dev" config user.name t
U="$T/inst/scripts/update.sh"

out="$(bash "$U")" || fail "update with nothing new: $out"
grep -q "already up to date" <<<"$out" || fail "no 'already up to date': $out"

printf '1.1.0\n' > "$T/dev/VERSION"; printf 'FROM scratch\nRUN true\n' > "$T/dev/worker/Dockerfile"
git -C "$T/dev" commit -q -am "feat: something new"; git -C "$T/dev" push -q origin main
head_before="$(git -C "$T/inst" rev-parse HEAD)"

out="$(bash "$U" --check)" || fail "update --check: $out"
grep -q "1.0.0 -> 1.1.0" <<<"$out" || fail "--check lacks the version change: $out"
grep -q "Would: rebuild the worker images" <<<"$out" || fail "--check lacks the worker rebuild: $out"
grep -q "feat: something new" <<<"$out" || fail "--check lacks the commit list: $out"
[ "$(git -C "$T/inst" rev-parse HEAD)" = "$head_before" ] || fail "--check moved HEAD"
[ ! -s "$T/calls.log" ] || fail "--check ran something: $(cat "$T/calls.log")"

echo "# local edit" >> "$T/inst/bin/doctor"
if bash "$U" >/dev/null 2>&1; then fail "update must refuse a checkout with edited tracked files"; fi
[ "$(git -C "$T/inst" rev-parse HEAD)" = "$head_before" ] || fail "refused update moved HEAD"
git -C "$T/inst" checkout -q -- bin/doctor

bash "$U" >"$T/update.out" 2>&1 || { cat "$T/update.out"; fail "update failed"; }
[ "$(cat "$T/inst/VERSION")" = "1.1.0" ] || fail "VERSION not updated"
[ "$(git -C "$T/inst" rev-parse HEAD)" = "$(git -C "$T/origin.git" rev-parse main)" ] || fail "HEAD is not upstream"
calls_has "docker compose -f worker/docker-compose.yml build [WORKER_N=0]"
if [ -d /run/systemd/system ]; then calls_has "sudo scripts/install-services"; fi
git -C "$T/inst" worktree list | grep -vq "$T/inst " && fail "update left a worktree behind" || true

# a new version that fails its own selftest is not installed
: > "$T/calls.log"
printf '#!/usr/bin/env python3\nprint("foreman selftest: FAILED")\n' > "$T/dev/bin/foreman"
printf '1.2.0\n' > "$T/dev/VERSION"
git -C "$T/dev" commit -q -am "broken release"; git -C "$T/dev" push -q origin main
head_before="$(git -C "$T/inst" rev-parse HEAD)"
if bash "$U" >"$T/update.out" 2>&1; then fail "update must stop when the new selftest fails"; fi
grep -q "fails its own selftest" "$T/update.out" || fail "no selftest message: $(cat "$T/update.out")"
[ "$(git -C "$T/inst" rev-parse HEAD)" = "$head_before" ] || fail "failed selftest still moved HEAD"
calls_lacks "sudo scripts/install-services"

# local commits that are not upstream cannot be fast-forwarded over
git -C "$T/inst" config user.email t@example.com; git -C "$T/inst" config user.name t
git -C "$T/dev" reset -q --hard HEAD~1; git -C "$T/dev" push -q -f origin main
git -C "$T/inst" fetch -q; echo x > "$T/inst/local.txt"; git -C "$T/inst" add local.txt; git -C "$T/inst" commit -q -m "local only"
git -C "$T/dev" commit -q --allow-empty -m "other work"; git -C "$T/dev" push -q origin main
if bash "$U" >"$T/update.out" 2>&1; then fail "update must refuse diverged history"; fi
grep -q "not upstream" "$T/update.out" || fail "no diverged message: $(cat "$T/update.out")"
echo "forge update: ok"

# ---------- 3. uninstall.sh
mk_install() {
  rm -rf "$T/un"; mkdir -p "$T/un/scripts" "$T/un/forgejo" "$T/un/egress" "$T/un/runs/foreman" "$T/un/projects/p" "$T/un/etc" "$T/units" "$T/sec" "$T/un/docs"
  cp "$ROOT/scripts/uninstall.sh" "$T/un/scripts/"; cp "$ROOT/forge" "$T/un/forge"
  : > "$T/un/forgejo/docker-compose.yml"; : > "$T/un/egress/docker-compose.yml"
  echo a > "$T/un/etc/foreman.conf"; echo b > "$T/un/etc/team-rules.md"; echo c > "$T/un/runs/foreman/live.json"; echo d > "$T/sec/worker-1.token"
  : > "$T/units/agent-forge-foreman.service"; : > "$T/units/agent-forge-web.service"; : > "$T/units/unrelated.service"
  ln -sf "$T/un/forge" "$HOME/.local/bin/forge"; ln -sf "$T/un/forge" "$HOME/.local/bin/agent-forge"
  : > "$T/calls.log"
}
export AF_UNIT_DIR="$T/units" SANDBOX_SECRETS="$T/sec"
D="$T/un/scripts/uninstall.sh"

mk_install
out="$(bash "$D" --check --purge)" || fail "uninstall --check: $out"
grep -q "Would:" <<<"$out" || fail "--check prints no plan"
grep -q "Plan complete - 0 side effects" <<<"$out" || fail "--check lacks the closing line"
for what in "docker rm" "docker volume rm" "docker image rm" "sudo systemctl" "sudo rm" " down"; do calls_lacks "$what"; done
[ -e "$T/un/runs/foreman/live.json" ] && [ -e "$T/sec/worker-1.token" ] && [ -L "$HOME/.local/bin/forge" ] || fail "--check changed something"

mk_install
bash "$D" --yes >"$T/un.out" 2>&1 || { cat "$T/un.out"; fail "uninstall --yes"; }
calls_has "sudo systemctl disable --now agent-forge-foreman.service"
calls_has "sudo systemctl disable --now agent-forge-web.service"
calls_has "docker rm -f worker-1"; calls_has "docker rm -f worker-2"; calls_lacks "other-thing"
calls_has "docker compose -p forgejo -f forgejo/docker-compose.yml down"
calls_has "docker compose -f egress/docker-compose.yml down"
calls_has "docker network rm workers_internal"
calls_lacks "unrelated"; calls_lacks " -v"; calls_lacks "docker volume rm"; calls_lacks "docker image rm"
[ -e "$T/un/runs/foreman/live.json" ] && [ -e "$T/un/etc/foreman.conf" ] && [ -e "$T/sec/worker-1.token" ] || fail "uninstall without --purge deleted data"
[ ! -e "$HOME/.local/bin/forge" ] && [ ! -e "$HOME/.local/bin/agent-forge" ] || fail "the shortcuts are still there"

mk_install
if echo no | bash "$D" --purge >/dev/null 2>&1; then fail "--purge must need the typed word"; fi
[ -e "$T/un/runs/foreman/live.json" ] || fail "an unconfirmed purge deleted data"
echo purge | bash "$D" --purge >"$T/un.out" 2>&1 || { cat "$T/un.out"; fail "uninstall --purge with confirmation"; }
calls_has "docker volume rm worker_worker-1-home"; calls_has "docker volume rm forgejo_forgejo-data"; calls_has "docker volume rm egress_logs"
calls_lacks "unrelated_volume"; calls_has "docker image rm sandbox-worker:1.2.11"; calls_lacks "nginx"
[ ! -e "$T/un/runs" ] && [ ! -e "$T/un/projects" ] && [ ! -e "$T/un/etc/foreman.conf" ] && [ ! -e "$T/sec" ] || fail "purge left data behind"
[ -f "$T/un/scripts/uninstall.sh" ] && [ -f "$T/un/forge" ] || fail "purge deleted the checkout itself"
echo "forge uninstall: ok"
echo "forge cli tests: ALL OK"
