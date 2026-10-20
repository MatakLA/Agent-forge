#!/usr/bin/env bash
# Agent-Forge bootstrap: gets the code onto this machine and hands over to scripts/install.sh.
#
#   curl -fsSL https://raw.githubusercontent.com/MatakLA/Agent-forge/main/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/MatakLA/Agent-forge/main/install.sh | bash -s -- --demo
#   ./install.sh [--demo] [--check]            (from a checkout: no download, just the install)
#
# Everything after the script name goes to scripts/install.sh (--demo, --check, --help).
# Environment:  FORGE_DIR       where the code lives (default ~/agent-forge)
#               FORGE_REF       branch or tag to install (default: the repository's default branch)
#               FORGE_REPO_URL  where to clone from (default: the public repository)
# Prefer to read before you run? Download the file, read it (about 60 lines), then run it.
# Needs Ubuntu / Debian, a normal user with sudo and internet access. Safe to re-run: an existing checkout is only fast-forwarded.
main() {
  set -Eeuo pipefail
  local url="${FORGE_REPO_URL:-https://github.com/MatakLA/Agent-forge.git}" ref="${FORGE_REF:-}" dir here=""
  dir="${FORGE_DIR:-$HOME/agent-forge}"
  say() { printf '==> %s\n' "$*"; }
  die() { printf '\n✘ %s\n' "$1" >&2; shift; for h in "$@"; do printf '    → %s\n' "$h" >&2; done; exit 1; }

  [ "$(id -u)" != 0 ] || die "Run it as your normal user, not root (it uses sudo where needed)."
  command -v apt-get >/dev/null 2>&1 || die "This installer supports Ubuntu / Debian (apt)." "On other systems follow docs/INSTALL.md."
  command -v sudo >/dev/null 2>&1 || die "sudo is needed." "As root: apt-get install sudo && adduser $(id -un) sudo"

  # Started from a checkout (./install.sh): use it as it is.
  # (piped into bash, BASH_SOURCE[0] is just "bash", not a file - then the current directory must not count as a checkout)
  if [ -f "${BASH_SOURCE[0]:-}" ] && [ -f "$(dirname "${BASH_SOURCE[0]}")/scripts/install.sh" ]; then
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  fi

  if [ -z "$here" ]; then
    if ! command -v git >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
      say "Installing git and python3 (sudo asks for your password)"
      sudo apt-get update -q
      sudo apt-get install -y -q git python3 ca-certificates
    fi
    if [ -d "$dir/.git" ]; then
      say "Updating the checkout in $dir"
      if [ -n "$(git -C "$dir" status --porcelain --untracked-files=no)" ]; then
        printf '  ⚠ %s has local changes - leaving the code as it is\n' "$dir"
      else
        git -C "$dir" fetch --quiet --tags origin
        if [ -n "$ref" ]; then git -C "$dir" checkout --quiet "$ref"; fi
        if git -C "$dir" symbolic-ref -q HEAD >/dev/null; then git -C "$dir" merge --quiet --ff-only '@{u}' || printf '  ⚠ could not fast-forward - leaving the code as it is\n'; fi
      fi
    elif [ -e "$dir" ] && [ -n "$(ls -A "$dir" 2>/dev/null)" ]; then
      die "$dir exists and is not an Agent-Forge checkout." "Move it away or choose another place: FORGE_DIR=/path/to/agent-forge"
    else
      say "Downloading Agent-Forge to $dir"
      git clone --quiet "$url" "$dir" || die "Could not clone $url" "Check the internet connection, or set FORGE_REPO_URL."
      if [ -n "$ref" ]; then git -C "$dir" checkout --quiet "$ref" || die "Unknown branch or tag: $ref"; fi
    fi
    here="$dir"
  fi

  # When piped from curl, stdin is this script: keep installer children from reading it (sudo asks on the terminal itself).
  [ -t 0 ] || exec </dev/null
  exec env -u FORGE_DIR "$here/scripts/install.sh" "$@"
}
main "$@"
exit
