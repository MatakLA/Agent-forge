# Changelog

## 0.9.0 - 2026-10-07
First versioned release. Highlights since the extraction from a private setup:

**Efficiency** (data from ~400 runs: token-cap runs used 38 % of the quota) - context pack per task from a deterministic code
map, self-check with fixes in the same agent conversation, push after every run, pre-split of tasks owning many files, model
routing and A/B tests with quota-per-merged-task metrics, planning ahead and LLM-free maintenance while quota is out.

**Owner controls** - goals, backlog order and answers to the planner's questions from the dashboard (answers are kept in
`decisions.md` and bind later plans); per-project kill switch and `--all`; settings, workers (with browser login) and new
projects from the dashboard; notifications via ntfy / Slack / Discord / webhook; `bin/doctor`.

**Any language** - `TEST_CMD` and `GATE_IMAGE` per project (also editable in the dashboard's Settings → Project).

**English and Slovak dashboard** - English by default, Slovak for Slovak browsers, switchable (EN/SK button); content such as
goals, specs and transcripts is never translated.

**Security** - dashboard Host check (DNS rebinding), CSRF header, no framing, CSP, optional `DASHBOARD_TOKEN`; Forgejo tokens
no longer on git command lines; nothing is pushed from a run with a HIGH egress alarm.

**Fixes**
- Dashboard froze on stale data after ~14 h: tail processes leaked pipes until the open-file limit; finished runs' panes were
  re-created every ~95 s.
- Parallel gate steps shared one rebase clone: interleaved rebases could push one branch's result to another branch.
- Freshly opened PRs looked conflicted (Forgejo's mergeability check still running) and were sent back to the worker.
- The kill switch re-tripped after "Continue": stops by the kill switch, token caps and quota stops counted as failures.
- A run whose supervisor died (reboot, OOM) left its task "running" forever; after a reboot a reused pid could make a worker look
  busy forever.
- A fresh checkout crashed when planning (missing `tasks/`); malformed planner JSON made the loop re-plan (and re-pay) every
  tick; two goals created in the same second overwrote each other; concurrent backlog/state writes lost updates.
- Claude Code failures that were not usage limits were retried every tick; LLM call kinds were mislabelled under parallel calls.
- Maintenance reported "main is red" for a project without tests or when Docker failed.
- Timeline mixed the runs of all projects; the remote dashboard assumed the user `dev`.
- A worker out of weekly quota logged the same event every 3 minutes for days and re-read its quota each time (now once per
  change, re-checked every 30 min when the reset is far; ISO reset times were never parsed).
