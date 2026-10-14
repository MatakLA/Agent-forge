#!/bin/bash
# Worker container entrypoint: validates env vars, drops root, secures repo perms, and execs agent process.
# Invariants:
# - Refuses to run as root; drops privileges to unprivileged worker user (uid 1000).
# - Restrictive umask (077) ensures repository files and credentials are never group/world-readable.
# - FORGEJO_USER and FORGE_REPO are strictly validated against allow-patterns before passing to git commands.
# - All shell variables are quoted to prevent command or argument injection.
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
  TARGET_USER="${WORKER_USER:-worker}"
  if id "$TARGET_USER" >/dev/null 2>&1; then
    TARGET_HOME="$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6)"
    [ -n "$TARGET_HOME" ] && [ -d "$TARGET_HOME" ] && chown "$TARGET_USER:$TARGET_USER" "$TARGET_HOME" 2>/dev/null || true
    if command -v runuser >/dev/null 2>&1; then
      exec runuser -m -u "$TARGET_USER" -- "$0" "$@"
    elif command -v su >/dev/null 2>&1; then
      exec su -m "$TARGET_USER" -c 'exec "$@"' -- "$0" "$@"
    fi
  fi
  echo "worker entrypoint: refuse to run as root and cannot switch to non-root user ($TARGET_USER)" >&2
  exit 1
fi

umask 077

FORGEJO_USER="${FORGEJO_USER:-worker-1}"
[[ "$FORGEJO_USER" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$ ]] || { echo "invalid FORGEJO_USER: $FORGEJO_USER" >&2; exit 1; }

FORGE_REPO="${FORGE_REPO:-sandbox/playground}"
if [[ ! "$FORGE_REPO" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}/[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$ ]] || [[ "$FORGE_REPO" == *".."* ]]; then
  echo "invalid FORGE_REPO: $FORGE_REPO" >&2
  exit 1
fi

export GIT_AUTHOR_NAME="$FORGEJO_USER" GIT_COMMITTER_NAME="$FORGEJO_USER"
export GIT_AUTHOR_EMAIL="${FORGEJO_USER}@sandbox.local" GIT_COMMITTER_EMAIL="${FORGEJO_USER}@sandbox.local"

HOME="${HOME:-/home/worker}"
if [ ! -d "$HOME/playground/.git" ]; then
  git clone "http://forgejo:3000/${FORGE_REPO}.git" "$HOME/playground"
  git -C "$HOME/playground" config user.name "$FORGEJO_USER"
  git -C "$HOME/playground" config user.email "${FORGEJO_USER}@sandbox.local"
fi

chmod 700 "$HOME/playground" 2>/dev/null || true
chmod -R go-rwx "$HOME/playground/.git" 2>/dev/null || true

exec "$@"
