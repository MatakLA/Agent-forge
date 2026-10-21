#!/usr/bin/env bash
# test_install_flow.sh - the real install path (not --check) and the one-line bootstrap, against stubs: no Docker, no sudo, no network.
# Real bash, git (a local "origin") and python3; docker / sudo / sg / systemctl and the repo's heavy helpers only log their arguments.
# Covers: Docker already usable, fresh machine (docker group -> `sg` re-exec -> resume), a group that never works (no loop), re-run,
#         file modes, the forge shortcut (and a foreign `forge` program), bootstrap clone / fast-forward / dirty checkout / foreign directory.
# Public interface: bash tests/test_install_flow.sh
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d "${TMPDIR:-/tmp}/af-install-flow.XXXXXX")"
SRV=""
trap '[ -z "$SRV" ] || kill "$SRV" 2>/dev/null || true; rm -rf "$T"' EXIT
fail() { echo "FAIL: $*" >&2; [ ! -f "$T/out.log" ] || { echo "--- output:"; cat "$T/out.log"; }; [ ! -f "$T/calls.log" ] || { echo "--- calls:"; cat "$T/calls.log"; }; exit 1; }
calls_has() { grep -qF -- "$1" "$T/calls.log" || fail "missing call: $1"; }
calls_lacks() { ! grep -qF -- "$1" "$T/calls.log" || fail "unexpected call: $1"; }
out_has() { grep -qF -- "$1" "$T/out.log" || fail "output lacks: $1"; }
out_lacks() { ! grep -qF -- "$1" "$T/out.log" || fail "output has: $1"; }

# ---------- stubs
mkdir "$T/stub"
cat > "$T/stub/docker" <<'EOF'
#!/usr/bin/env bash
echo "docker $* [WORKER_N=${WORKER_N:-}]" >> "$STUB_CALLS"
case "$1" in
  info) [ -e "$STUB_GROUP" ]; exit $? ;;
  version) echo 27.0.0 ;;
  inspect|image) exit 1 ;;
esac
exit 0
EOF
cat > "$T/stub/sudo" <<'EOF'
#!/usr/bin/env bash
echo "sudo $*" >> "$STUB_CALLS"
[ "$1" = -n ] && exit 0
[ "$1" = -v ] && exit 0
[ "$1" = scripts/install-services ] && echo "services installed" >> "$STUB_CALLS"
exit 0
EOF
cat > "$T/stub/sg" <<'EOF'
#!/usr/bin/env bash
echo "sg $*" >> "$STUB_CALLS"
[ "${STUB_GROUP_NEVER:-}" = 1 ] || touch "$STUB_GROUP"
exec bash -c "$3"
EOF
cat > "$T/stub/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >> "$STUB_CALLS"
case "$1" in is-active) exit 3 ;; esac
exit 0
EOF
# group membership follows the stubbed docker group (a CI runner's user is already in the real docker group)
cat > "$T/stub/id" <<'EOF'
#!/usr/bin/env bash
if [ "${1:-}" = -nG ]; then printf 'tester'; [ ! -e "$STUB_GROUP" ] || printf ' docker'; echo; exit 0; fi
exec /usr/bin/id "$@"
EOF
printf '#!/usr/bin/env bash\nexit 0\n' > "$T/stub/apt-get"
chmod +x "$T/stub/"*

# a repository copy whose heavy helpers are stubs
mkrepo() {
  local d="$1"
  mkdir -p "$d/scripts" "$d/bin" "$d/etc" "$d/examples/demo" "$d/worker" "$d/docs"
  cp "$ROOT/scripts/install.sh" "$d/scripts/install.sh"
  cp "$ROOT/etc/"*.example.* "$d/etc/"
  cp "$ROOT/examples/demo/vision.md" "$d/examples/demo/vision.md"
  cp "$ROOT/VERSION" "$d/VERSION"
  : > "$d/worker/docker-compose.yml"
  for h in bin/egress-up scripts/forge-bootstrap scripts/vm-baseline-ubuntu.sh scripts/install-services; do
    printf '#!/usr/bin/env bash\necho "%s $*" >> "$STUB_CALLS"\n' "$h" > "$d/$h"
  done
  printf 'print("doctor ok")\n' > "$d/bin/doctor"
  printf 'print("foreman selftest: ALL OK")\n' > "$d/bin/foreman"
  printf '#!/usr/bin/env bash\necho "Dashboard:  http://127.0.0.1:8171"\n' > "$d/forge"
  chmod +x "$d/scripts/"* "$d/bin/egress-up" "$d/forge"
}

export STUB_CALLS="$T/calls.log" STUB_GROUP="$T/group-active"
export HOME="$T/home"; mkdir -p "$HOME/.local/bin"
export PATH="$T/stub:$HOME/.local/bin:$PATH"
unset FORGE_DIR FORGE_REF FORGE_REPO_URL FORGE_RESUME FORGE_T0 SANDBOX_SECRETS
export SANDBOX_SECRETS="$T/secrets"

# something that answers /healthz, so the installer's final wait does not run into its timeout
PORT="$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
( cd "$T" && exec python3 -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1 ) & SRV=$!
export BIND_PORT="$PORT"
for _ in $(seq 40); do python3 -c "import socket; socket.create_connection(('127.0.0.1', $PORT), 0.2)" 2>/dev/null && break; sleep 0.1; done

run_install() { : > "$STUB_CALLS"; (cd "$1" && shift && bash scripts/install.sh "$@") > "$T/out.log" 2>&1; }

# ---------- 1. Docker already usable
touch "$STUB_GROUP"
R="$T/r1"; mkrepo "$R"
run_install "$R" --demo || fail "install with usable docker failed"
out_has "[1/9] Preflight"; out_has "[9/9]"; out_has "is installed"; out_lacks "[10/9]"
calls_lacks "vm-baseline-ubuntu.sh"
calls_has "bin/egress-up"; calls_has "scripts/forge-bootstrap"
calls_has "docker compose -f worker/docker-compose.yml build [WORKER_N=0]"
if [ -d /run/systemd/system ]; then calls_has "sudo scripts/install-services"; else out_has "no systemd on this machine"; calls_lacks "sudo scripts/install-services"; fi
[ "$(stat -c %a "$R/etc")" = 700 ] || fail "etc/ must be 700"
for f in limits foreman forge; do [ "$(stat -c %a "$R/etc/$f.conf")" = 600 ] || fail "etc/$f.conf must be 600"; done
[ "$(stat -c %a "$R/etc/team-rules.md")" = 600 ] || fail "etc/team-rules.md must be 600"
[ "$(stat -c %a "$T/secrets")" = 700 ] || fail "the secrets directory must be 700"
cmp -s "$R/docs/vision.md" "$R/examples/demo/vision.md" || fail "--demo must put the demo vision in place"
[ "$(readlink -f "$HOME/.local/bin/agent-forge")" = "$R/forge" ] || fail "agent-forge shortcut missing"
[ "$(readlink -f "$HOME/.local/bin/forge")" = "$R/forge" ] || fail "forge shortcut missing"
[ ! -d /run/systemd/system ] || out_has "dashboard answers on 127.0.0.1:$PORT"
echo "install (docker usable): ok"

# ---------- 2. re-run: nothing of yours is overwritten, the shortcuts stay
echo "my own settings" > "$R/etc/forge.conf"; echo "my vision" > "$R/docs/vision.md"
run_install "$R" --demo || fail "re-run failed"
[ "$(cat "$R/etc/forge.conf")" = "my own settings" ] || fail "re-run overwrote etc/forge.conf"
[ "$(cat "$R/docs/vision.md")" = "my vision" ] || fail "re-run overwrote docs/vision.md"
[ "$(readlink -f "$HOME/.local/bin/agent-forge")" = "$R/forge" ] || fail "re-run broke the shortcut"
echo "install (re-run): ok"

# ---------- 3. fresh machine: Docker gets installed, the new group needs the `sg` re-exec, the install resumes
rm -f "$STUB_GROUP"; rm -rf "$HOME/.local/bin"/*; mkdir -p "$HOME/.local/bin"
R="$T/r2"; mkrepo "$R"
run_install "$R" || fail "fresh-machine install failed"
calls_has "sudo env SYSTEM_UPGRADE=0 bash scripts/vm-baseline-ubuntu.sh"
[ "$(grep -c '^sg docker' "$STUB_CALLS")" = 1 ] || fail "exactly one sg re-exec expected"
calls_has "env FORGE_RESUME=1 $R/scripts/install.sh"
out_has "(continuing inside the docker group)"
[ "$(grep -c 'Preflight' "$T/out.log")" = 1 ] || fail "preflight must run once, not again after the re-exec"
out_has "[9/9]"; out_lacks "[10/9]"; out_has "is installed"
out_has "m0"   # the elapsed time is shown over the re-exec as well
echo "install (fresh machine, sg resume): ok"

# ---------- 4. the group never works: stop with an explanation, no loop
rm -f "$STUB_GROUP"; R="$T/r3"; mkrepo "$R"
if STUB_GROUP_NEVER=1 run_install "$R"; then fail "must fail when docker stays unusable"; fi
[ "$(grep -c '^sg docker' "$STUB_CALLS")" = 1 ] || fail "must re-exec only once"
out_has "cannot use it"
calls_lacks "bin/egress-up"
echo "install (docker never usable): ok"

# ---------- 5. a foreign program called `forge` (Foundry) is left alone, agent-forge is linked
touch "$STUB_GROUP"; rm -rf "$HOME/.local/bin"/*; mkdir -p "$HOME/.local/bin" "$T/foundry"
printf '#!/bin/sh\necho foundry\n' > "$T/foundry/forge"; chmod +x "$T/foundry/forge"
R="$T/r4"; mkrepo "$R"
PATH="$T/foundry:$PATH" run_install "$R" || fail "install next to a foreign forge failed"
[ ! -e "$HOME/.local/bin/forge" ] || fail "a foreign forge program must not be shadowed"
[ "$(readlink -f "$HOME/.local/bin/agent-forge")" = "$R/forge" ] || fail "agent-forge must still be linked"
out_has "left alone"
run_install "$T/r4" --no-link || fail "--no-link failed"
echo "install (forge name collision, --no-link): ok"

# ---------- 6. the one-line bootstrap against a local origin
git -C "$T" init -q --bare origin.git
mkrepo "$T/seed"; cp "$ROOT/install.sh" "$T/seed/install.sh"
git -C "$T/seed" init -q -b main
git -C "$T/seed" -c user.name=t -c user.email=t@example.invalid add -A
git -C "$T/seed" -c user.name=t -c user.email=t@example.invalid commit -q -m seed
git -C "$T/seed" remote add origin "$T/origin.git"; git -C "$T/seed" push -q origin main
git -C "$T/origin.git" symbolic-ref HEAD refs/heads/main
export FORGE_REPO_URL="$T/origin.git" FORGE_DIR="$T/checkout"
# like `curl ... | bash -s -- ARGS`: the script arrives on stdin, so it cannot know where it lives
boot() { : > "$STUB_CALLS"; bash -s -- "$@" < "$ROOT/install.sh" > "$T/out.log" 2>&1; }

boot --check || fail "bootstrap clone + --check failed"
[ -f "$T/checkout/scripts/install.sh" ] || fail "bootstrap did not clone"
out_has "Downloading Agent-Forge"; out_has "Plan complete - 0 side effects"

git -C "$T/seed" -c user.name=t -c user.email=t@example.invalid commit -q --allow-empty -m "second"
git -C "$T/seed" push -q origin main
before="$(git -C "$T/checkout" rev-parse HEAD)"
boot --check || fail "bootstrap update failed"
[ "$(git -C "$T/checkout" rev-parse HEAD)" = "$(git -C "$T/seed" rev-parse HEAD)" ] && [ "$before" != "$(git -C "$T/checkout" rev-parse HEAD)" ] || fail "bootstrap did not fast-forward"
out_has "Updating the checkout"

git -C "$T/seed" -c user.name=t -c user.email=t@example.invalid commit -q --allow-empty -m "third"; git -C "$T/seed" push -q origin main
echo "local edit" >> "$T/checkout/VERSION"
before="$(git -C "$T/checkout" rev-parse HEAD)"
boot --check || fail "bootstrap with a dirty checkout failed"
[ "$(git -C "$T/checkout" rev-parse HEAD)" = "$before" ] || fail "a checkout with local changes must not be moved"
grep -q "local edit" "$T/checkout/VERSION" || fail "local changes must survive"
out_has "local changes"

mkdir "$T/foreign"; echo x > "$T/foreign/file"
if FORGE_DIR="$T/foreign" boot --check; then fail "a foreign directory must be refused"; fi
out_has "is not an Agent-Forge checkout"; [ "$(cat "$T/foreign/file")" = x ] || fail "foreign directory touched"

if boot --no-such-option; then fail "unknown option must fail"; fi

# started from a checkout: used as it is, nothing downloaded
: > "$STUB_CALLS"; FORGE_DIR="$T/elsewhere" bash "$T/checkout/install.sh" --check > "$T/out.log" 2>&1 < /dev/null || fail "./install.sh from a checkout failed"
out_lacks "Downloading"; [ ! -e "$T/elsewhere" ] || fail "a checkout must not trigger a second clone"
echo "bootstrap: ok"

echo "install flow test: ALL OK"
