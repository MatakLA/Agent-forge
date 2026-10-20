# Efficiency: more merged work per subscription

With subscription accounts the limit is not time but quota. On the reference install (4 Antigravity accounts, ~400 runs)
the team used up a week's quota in about two and a half days, and then waited for the reset. So the measure that matters is
**quota per merged task**, and the dashboard shows it (Insights → Experiments).

What the data said before these features:

| | |
|---|---|
| one successful run | ~0.9 % of one account's weekly quota |
| one merged task | ~2.6 % (reworks, token-cap and failed runs included) |
| runs that hit the token cap | 22 % of runs, **38 % of the quota** |
| typical run | 64 tool calls, ~0.7 M input + ~3.9 M cached tokens, only ~40 k output |
| what predicts a token-cap hit | the number of files a task owns (1-2 files: 0.4 hits per task, 4: 0.8, 5: 1.0) - not its size in lines |
| most common rework causes | branch not pushed (58), merge conflict (43), tests fail in the clean container (21), mutation survived (17) |

Quota use correlated most with input tokens (0.86) and tool calls (0.82): an agent resends the whole conversation on every
turn, so **every exploration step makes all later steps more expensive**. Most of the features below cut turns.

## Context pack (`CONTEXT_PACK`)
The foreman appends a section to each task, built from main without any LLM (`bin/lib/codemap.py`):
- the current content of the files the task owns (only on a fresh attempt - a rework continues from its branch),
- the interfaces (signatures + first docstring line, no bodies) of the modules those files import, their tests and any file
  the spec names,
- a one-line map of every other module (path, size, purpose).

It is capped (`CONTEXT_PACK_CHARS`), because it is part of every later turn too. `ab` gives it to half the tasks (stable per
task) so the dashboard (Insights → Experiments) can compare quota per merged task and tool calls per run with and without it.

### Orientation without steps (`PACK_EXTRAS`, `ORIENT`)
What the transcripts show: one tool call per model step, so a run costs (number of steps) x (context resent). About a quarter of
the `run_command` calls are orientation (`grep`, `find`, `ls`, `git status/log/diff/branch/fetch`) and 92 % of file reads already use
line ranges, so the waste is not "reading whole files" but the steps that find out *where* something is. Re-reads of a region the
run already saw are rare (about 7 % fully covered), so a read cache would not pay. Three changes cut orientation steps instead:
- **Line spans** (`PACK_EXTRAS=on`, default): every definition in the code map ends in `[L12-40]`, so a worker reads just those
  lines (`sed -n '12,40p' file`) instead of grepping for the definition and reading around it.
- **Who uses what you change** (`PACK_EXTRAS`): one `git grep` on main for the public names of the task's own files; the pack lists
  `path:line` of the callers outside the task's files and names that nothing uses ("free to change"). It replaces the grep a
  worker would run for each name.
- **Start state** (`ORIENT=on`, default): the supervisor puts the worker's clone on the task branch (earlier attempts' work, or a
  new branch from main) and writes where it stands into the prompt (behind/ahead of main, the branch's commits). The rules no
  longer ask for `git fetch` / `checkout` / `status` / `log`. A rework also gets its branch's diff against main in the pack
  (`PACK_EXTRAS`) instead of having to run `git diff`.

The rules also say that fewer steps beat shorter ones: independent commands go into one `run_command`, all edits of one file into
one call. `PROMPT_ORDER=cache` (off by default, not yet measured) puts the text that is the same for every task - rules and the
one-line file list, without sizes or spans so it only changes when a file is added - in front of the task, for the provider's prompt cache.
Both toggles exist so the benchmark (`bench/`) can compare on and off; turn `ORIENT=off` / `PACK_EXTRAS=off` to get the old prompt.

## Code documentation that cannot go stale
The code map is generated from the code on every main commit, so it is always current - unlike hand-written docs, which drift
and then mislead the agents reading them. Its quality comes from module docstrings: workers are told to start every module with
what it is for, its public interface and its invariants, the gate warns about new modules without one, and maintenance queues a
"module docstrings" goal for existing ones. The planner gets the full map instead of the first 60 lines of every module (fewer
orchestrator tokens, better overview).

## Self-check in the same run (`SELF_CHECK_ROUNDS`)
When a worker finishes, the supervisor runs the test suite and the gate's skip/xfail scan in the worker's clone. If it is
red, the worker gets the output in the **same agy conversation** (it still has the context) and fixes it - instead of the gate
sending the task back to a new run that has to rebuild that context. The run's token cap and timeout cover all rounds.

A suite that does not finish within 10 minutes (measured on a large Python project: 6315 tests, 654 s on the gate's 1 CPU) used to cost
every run that full timeout and still check nothing. Now the first such run records "slow" in `runs/self-check-slow[.project].json`
(valid 7 days, tied to the project's `TEST_CMD`); until it expires the self-check runs only the pytest files the branch added or
changed plus `test_<module>.py` next to a changed module, which takes seconds. The full suite stays the gate's job.

## What the reviewer would send back, found by scripts (`bin/lib/quality.py`)
Of 120 review rejections measured on a large Python project, 41 cited the quality gate / test ratio, 40 files outside the allowed list
and 9 tests that lost assertions - all things a script can see. Each of these cost a new run (≈650 k tokens per merged task) and
a ≈11 min gate. Now the supervisor's self-check, the gate and the foreman use the same module, and the worker hears about them in
the **same conversation**, before it stops:

| Finding | Self-check | Gate (`review-branch`) |
|---|---|---|
| a file outside the task's file list (tests of a listed module and `SHARED_FILES` are fine) | problem | `scope:` warning, flag `scope` |
| an existing test lost assertions or test functions | problem | `weakened:`, flag `weakened` |
| a definition copied from another file (same name, same body; JS and Python) | problem (source files) | `duplicate:`, flag `duplicate` |
| far more test lines than code lines (`TEST_RATIO_MAX`) | problem | `== QUALITY` |
| an unused import the change introduced | hint only | `unused-import:` |
| a red test of the changed or importing files | problem, found by a **fast pass** (≤ 30 test files, `--maxfail=5`) in seconds | the fast pass runs first; red → the whole suite is not run (saves ≈11 min) |

The task's file list travels in `meta.json` (`files`, from `DISPATCH_FILES`) and to the gate in `GATE_ALLOWED_FILES`; a task without a
list is unrestricted. All checks follow `QUALITY_CHECK` (`off` switches them off); the fast pass only runs for a plain
`pytest ...` `TEST_CMD` (`GATE_FAST=0` disables it in the gate). Editing an existing file no longer produces a warning by itself
(it used to, on every modified file, and discouraged reuse); only deleted/moved files do.

Every rework now stores **why** it happened (`kind`: conflict, secret, quality, tests-own, tests-existing, not-pushed, review,
mutation) and the gate's `== FLAGS:` at that moment in the task's `history`; `team_metrics().totals` counts them
(`rework_kinds`, `rework_flags`) - so the next rule is built from numbers, not impressions.

## Nothing is lost at the end of a run
After every run (not only after a token cap) the supervisor commits and pushes whatever the worker left in its clone to the
task branch, so "branch not pushed" no longer costs a rework. A run that changed nothing pushes nothing. (The commit used to be
chained to `git add`'s exit code, which is 1 whenever the repository's `.gitignore` lists `.pytest_cache` and the pathspec excludes
it - so exactly the runs that had run pytest by hand were never saved. Tested in `tests/test_supervisor.sh`.)

## Smaller tasks before work starts (`MAX_TASK_FILES`)
The planner is told to keep tasks to at most `MAX_TASK_FILES` files; a task that still owns more is split by the planner
before anyone works on it, rather than after it hit the token cap twice.

## Model routing and A/B tests (`WORKER_MODEL_LIGHT`, `AB_MODEL`, `AB_SHARE`)
Light roles can use a cheaper model; `AB_SHARE` % of routine tasks can run on another model. Insights → Experiments compares models by
quota per run, quota per merged task, first-pass approvals and token-cap rate. Whether a lower-thinking model is really cheaper
per *merged* task is exactly what this measures - a cheap run that needs a rework is not cheap.

## Days without quota (`PLAN_AHEAD`, `MAINTENANCE`)
When every worker of a project is below its quota floor, the foreman plans backlog goals ahead (orchestrator quota only), up to
about two pending tasks per worker, so work starts the moment quotas reset. And while the team is idle it checks main without
any LLM, once per new main commit and at most every 6 hours: the full suite three times (a red main becomes a goal; tests
that pass and fail on the same commit become a "flaky tests" backlog entry) and the code map (modules without a docstring).

Using a second subscription (e.g. Claude Code) as a fallback worker is deliberately not built in: it would draw from the same
limit the orchestrator needs, and a busy team can empty it within hours.
