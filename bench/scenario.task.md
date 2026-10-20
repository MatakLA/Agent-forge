# Task 0001-total - Add `tasklog total`

Role: backend-developer   Risk: routine
Files you own: `cli.py` (register the subcommand, add `cmd_total`), `tests/test_total.py` (new)

## The task
Add the subcommand `tasklog total [--since YYYY-MM-DD]`: print how many entries there are and their summed time.

### Behaviour
- `--since` takes a date exactly like `list --since` (use the existing `_date` argument type; an invalid date is a usage error, exit code 2).
- Output, to stdout, exit code 0: `<N> entries, <TIME>` where TIME is the summed minutes formatted with the existing `clock.format_minutes`
  (for example `5 entries, 5h45m`). Always the plural word `entries`.
- No entries (empty store, or `--since` newer than everything): print `No entries.` and exit 0.
- Reuse `_entries(store, since)` and `clock.format_minutes`; do not copy their logic.
- Add `tests/test_total.py` with a few tests (use `tests/helpers.py`).

## Acceptance criteria
- `python3 cli.py total` on the sample entries prints `5 entries, 5h45m` and exits 0.
- `--since 2024-03-07` on the same data prints `2 entries, 1h30m`.
- Empty result prints `No entries.` with exit 0; an invalid `--since` exits 2.
- `tests/test_total.py` exists and passes together with the whole existing suite.
- The change stays minimal: no changes to unrelated modules, no new dependencies.
