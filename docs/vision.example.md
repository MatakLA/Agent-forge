# Vision (example) - copy to docs/vision.md and replace with yours

The planner, the autopilot and `bin/foreman project` read this file. Write what you want to exist, for whom, and what matters -
not how to build it. Keep it under ~10 pages; the first 30,000 characters are used.

## What
A small personal finance tool for one household: record expenses, see where the money goes, get a warning before a budget runs out.

## For whom
Two adults, on phones and one laptop. Not technical. Slovak and English.

## Must have
- Add an expense in under 10 seconds from a phone.
- Monthly overview by category; budgets per category with a warning at 80 %.
- Export to CSV.

## Constraints
- Runs offline on a home server; no third-party services, no tracking.
- Python standard library + SQLite on the server; plain HTML/CSS/JS in the browser.

## Not now
Bank synchronisation, investments, multi-household support.

## Open decisions
- Currency: EUR only, or several?
