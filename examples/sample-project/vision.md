# Vision: bookmarks - a stdlib-only Python CLI bookmark manager

The planner, the autopilot and `bin/foreman project` read this file. Write what you want to exist, for whom, and what matters -
not how to build it. Keep it under ~10 pages; the first 30,000 characters are used.

## What
A small, fictional example project: `bookmarks`, a standard-library-only Python 3 CLI bookmark manager (script at `seed-repo/bin/bookmarks`). The seed repository already supports two subcommands: `add <url> [--tag TAG ...]` (append a bookmark with zero or more tags to a JSON file) and `list` (print every bookmark with its tags). It deliberately has no way to filter by tag yet.

Goal narrative (what a new owner would type as `bin/foreman goal "..."`):
"Add a `bookmarks search <tag>` command that lists only the bookmarks having that tag, with a test that fails without it."

## For whom
Developers and command-line users who want a quick, transparent, offline way to organize bookmarks from the terminal, and new Agent-Forge owners evaluating a realistic end-to-end goal on a sample project.

## Must have (Priorities)
1. Correctness of the new `search` subcommand and its test: add a `bookmarks search <tag>` command that lists only the bookmarks having that tag, with a test that fails without it.
2. Keep it dependency-free: Python 3 standard library only, no pip installs, matching the main repo's own rule.

## Constraints (Rules)
- Apache-2.0 license note: this is example content only, not a real product.
- No telemetry and no tracking.
- No secrets: no API keys, credentials, or private data.
- Python 3 standard library only for the CLI tool.

## Not now
Browser extension integration, full-text page content indexing, remote server sync, multi-user support.

## Open decisions
- Output ordering: match the existing output ordering and format of `bookmarks list`.

This file is a template. Copy examples/sample-project/ to try Agent-Forge end to end; see examples/sample-project/README.md.
