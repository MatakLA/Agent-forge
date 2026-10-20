# Task 0001-stats-by-tag - Add `tasklog stats --by-tag`

Role: backend-developer   Risk: routine
Files you own: `stats.py` (new), `tests/test_stats.py` (new), `cli.py` (wire the new subcommand in)

## The task
Add the subcommand `tasklog stats --by-tag [--since YYYY-MM-DD]`: for every tag, the number of entries that carry it and the total
minutes of those entries, as a plain-text table.

### Behaviour
- `--by-tag` is required (omitting it is a usage error, exit code 2). `--since` takes a date `YYYY-MM-DD` exactly like `list --since`
  (an invalid date is a usage error, exit code 2); only entries whose day is on or after it count.
- An entry with several tags counts once for each of its tags (its full minutes in each). An entry without tags counts under the
  tag `(untagged)`; use the existing tag helpers for that, do not invent your own placeholder.
- Rows are sorted by total minutes descending, then by tag name ascending.
- Output, to stdout, exit code 0. Columns `TAG`, `ENTRIES`, `MINUTES` (minutes as a plain integer, not `1h30m`). Every column is as wide as
  its longest cell or its header; `TAG` is left-aligned, `ENTRIES` and `MINUTES` right-aligned; columns are separated by two spaces; no trailing
  spaces. Example for the entries of `tests/helpers.sample_entries()`:

```
TAG         ENTRIES  MINUTES
backend           2      210
api               1       90
frontend          1       60
review            1       45
(untagged)        1       30
```
- No entries after filtering (empty store, or `--since` newer than everything): print `No entries.` and exit 0.
- Reuse the existing storage (`Store.all()` / `Store.since()`), tag (`tags.entry_tags`) and date (`clock.parse_date` via the CLI's existing
  `_date` argument type) helpers. Do not copy their logic.

### Structure
- New module `stats.py` with the logic (a function that turns a list of entries into rows and a function that renders the table) and a
  `cmd_stats(args, store)` function in the same style as the other `cmd_*` functions in `cli.py`; register the `stats` subcommand and its
  arguments in `cli.py`.
- New `tests/test_stats.py` that covers the behaviour above (use `tests/helpers.py`).

## Acceptance criteria
- `python3 cli.py stats --by-tag` prints exactly the table above for the sample entries and exits 0.
- `--since 2024-03-07` on the same data prints only `frontend` (1 entry, 60) and `(untagged)` (1 entry, 30), in that order.
- Empty result prints `No entries.` with exit 0; missing `--by-tag` or an invalid `--since` exits 2.
- `stats.py` is imported by `cli.py`; `tests/test_stats.py` exists and passes together with the whole existing suite.
- The change stays minimal: no changes to unrelated modules, no new dependencies.
