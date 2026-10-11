# Vision: `tasks` - a small command-line task manager (demo project)

A demo for trying Agent-Forge: small enough to finish in an afternoon of worker runs, big enough to see the team plan in
parallel, test, review and merge.

## What it is
A Python 3 command-line tool `tasks` that keeps a personal task list in a JSON file (default `~/.tasks.json`, overridable with
`TASKS_FILE`). Standard library only, no network.

## Features, in order of value
1. `tasks add "<title>" [--due YYYY-MM-DD] [--tag TAG ...]`, `tasks list [--tag TAG] [--all]`, `tasks done <id>`, `tasks rm <id>`.
   Ids are short, stable integers. Done tasks are hidden from `list` unless `--all`.
2. Due dates: `tasks list` sorts by due date, marks overdue tasks, `tasks today` shows what is due today or overdue.
3. Search: `tasks find <text>` (case-insensitive, title and tags).
4. Export / import: `tasks export --format csv|json`, `tasks import <file>` (duplicates by title + due date are skipped).
5. A short user guide `docs/USAGE.md` with an example session.

## Quality bar
- Every module starts with a docstring; every feature has pytest tests, including edge cases (empty file, broken JSON file -
  a clear error, not a traceback; dates in the past; unicode titles).
- The JSON file is written atomically (temp file + rename), so a crash never loses the list.
- `python3 -m pytest -q` passes from the repository root.

## Out of scope
Sync, servers, a GUI, recurring tasks.
