#!/bin/bash
set -euo pipefail
export GIT_AUTHOR_NAME="${FORGEJO_USER:-worker-1}" GIT_COMMITTER_NAME="${FORGEJO_USER:-worker-1}"
export GIT_AUTHOR_EMAIL="${FORGEJO_USER:-worker-1}@sandbox.local" GIT_COMMITTER_EMAIL="${FORGEJO_USER:-worker-1}@sandbox.local"
if [ ! -d "$HOME/playground/.git" ]; then
  git clone "http://forgejo:3000/sandbox/playground.git" "$HOME/playground"
  git -C "$HOME/playground" config user.name "${FORGEJO_USER:-worker-1}"
  git -C "$HOME/playground" config user.email "${FORGEJO_USER:-worker-1}@sandbox.local"
fi
exec "$@"
