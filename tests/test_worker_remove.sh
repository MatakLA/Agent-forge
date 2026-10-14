#!/usr/bin/env bash
# bin/worker-remove against a fake docker and a scratch tree: refuses to leave a project without workers, removes everything of
# one worker (pools, container, volume, Forgejo user, secrets, workers.conf line) and nothing of the others, and can be repeated.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
fail=0
check() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }

A="$T/af"; mkdir -p "$A/bin" "$A/etc" "$A/projects/p1" "$T/secrets" "$T/runs" "$T/bin"
cp "$ROOT/bin/worker-remove" "$A/bin/"; cp -r "$ROOT/bin/lib" "$A/bin/lib"
printf 'WORKERS=worker-1,worker-2\n' > "$A/etc/foreman.conf"
printf 'WORKERS=worker-2\n' > "$A/projects/p1/project.conf"
printf 'worker-1=a\nworker-2=b\n' > "$A/etc/workers.conf"
for w in worker-1 worker-2; do echo x > "$T/secrets/$w.token"; echo x > "$T/secrets/$w.password"; echo x > "$T/secrets/$w-keyring"; done
printf 'ID Username Email\n1 Matak m@x\n3 worker-1 a@x\n5 worker-2 b@x\n' > "$T/users"
cat > "$T/bin/docker" <<'DOCKER'
#!/bin/bash
echo "$*" >> "$FAKE_LOG"
case "$*" in
  "exec forgejo forgejo admin user list") cat "$FAKE_USERS" ;;
  "exec forgejo forgejo admin user delete --username "*)
    [ -n "${FAKE_DELETE_FAIL:-}" ] && exit 1
    sed -i "/ ${8} /d" "$FAKE_USERS" ;;
esac
exit 0
DOCKER
chmod +x "$T/bin/docker"
export PATH="$T/bin:$PATH" FAKE_LOG="$T/docker.log" FAKE_USERS="$T/users" SANDBOX_SECRETS="$T/secrets" SANDBOX_TEST=1 SANDBOX_RUNS_DIR="$T/runs" PYTHONDONTWRITEBYTECODE=1
: > "$FAKE_LOG"

bash "$A/bin/worker-remove" abc >/dev/null 2>&1; check "a worker number that is not a number is refused" "[ $? -eq 2 ]"

out=$(bash "$A/bin/worker-remove" 2 2>&1); rc=$?
check "the only worker of a project is not removed" "[ $rc -ne 0 ] && echo \"\$out\" | grep -q 'only worker of project p1'"
check "...and nothing was touched" "[ ! -s $FAKE_LOG ] && [ -f $T/secrets/worker-2.token ] && grep -q worker-2 $A/etc/workers.conf"

printf 'WORKERS=worker-1,worker-2\n' > "$A/projects/p1/project.conf"
out=$(bash "$A/bin/worker-remove" 2 2>&1); rc=$?
check "removal succeeds once every project has another worker" "[ $rc -eq 0 ] && echo \"\$out\" | grep -q 'worker-2 removed'"
check "it leaves the pool of every project" "grep -qx 'WORKERS=worker-1' $A/etc/foreman.conf && grep -qx 'WORKERS=worker-1' $A/projects/p1/project.conf"
check "the container and its home volume are removed" "grep -qx 'rm -f worker-2' $FAKE_LOG && grep -qx 'volume rm -f worker_worker-2-home' $FAKE_LOG"
check "the Forgejo user is deleted by name, and without --purge (its pull requests stay)" "grep -qx 'exec forgejo forgejo admin user delete --username worker-2' $FAKE_LOG"
check "its token, password and keyring are gone" "[ ! -e $T/secrets/worker-2.token ] && [ ! -e $T/secrets/worker-2.password ] && [ ! -e $T/secrets/worker-2-keyring ]"
check "its workers.conf line is gone" "! grep -q '^worker-2=' $A/etc/workers.conf"
check "the other worker keeps everything" "[ -f $T/secrets/worker-1.token ] && [ -f $T/secrets/worker-1-keyring ] && grep -q '^worker-1=' $A/etc/workers.conf && grep -q worker-1 $T/users && ! grep -q 'rm -f worker-1' $FAKE_LOG"

: > "$FAKE_LOG"
out=$(bash "$A/bin/worker-remove" 2 2>&1); rc=$?; check "it can be repeated (nothing left to delete, still ok)" "[ $rc -eq 0 ] && ! grep -q 'user delete' $FAKE_LOG"

printf 'ID Username Email\n3 worker-1 a@x\n5 worker-3 c@x\n' > "$T/users"; echo x > "$T/secrets/worker-3.token"
out=$(FAKE_DELETE_FAIL=1 bash "$A/bin/worker-remove" 3 2>&1); rc=$?
check "when Forgejo will not delete the user it says so and keeps the secrets so it can be repeated" "[ $rc -ne 0 ] && echo \"\$out\" | grep -q 'could not delete' && [ -f $T/secrets/worker-3.token ]"

[ $fail -eq 0 ] && echo "worker-remove tests: ALL OK" || { echo "worker-remove tests: FAILED"; exit 1; }
