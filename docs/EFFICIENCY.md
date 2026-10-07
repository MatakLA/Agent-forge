# Efficiency: more merged work per subscription

With subscription accounts the limit is not time but quota. On the reference install (4 Antigravity accounts, ~400 runs)
the team used up a week's quota in about two and a half days, and then waited for the reset. So the measure that matters is
**quota per merged task**, and the dashboard's Metrics tab shows it.

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
task) so the Metrics tab can compare quota per merged task and tool calls per run with and without it.

## Code documentation that cannot go stale
The code map is generated from the code on every main commit, so it is always current - unlike hand-written docs, which drift
and then mislead the agents reading them. Its quality comes from module docstrings: workers are told to start every module with
what it is for, its public interface and its invariants, the gate warns about new modules without one, and maintenance queues a
"module docstrings" goal for existing ones. The planner gets the full map instead of the first 60 lines of every module (fewer
orchestrator tokens, better overview).

## Self-check in the same run (`SELF_CHECK_ROUNDS`)
When a worker finishes, the supervisor runs the full test suite and the gate's skip/xfail scan in the worker's clone. If it is
red, the worker gets the output in the **same agy conversation** (it still has the context) and fixes it - instead of the gate
sending the task back to a new run that has to rebuild that context. The run's token cap and timeout cover all rounds.

## Nothing is lost at the end of a run
After every run (not only after a token cap) the supervisor commits and pushes whatever the worker left in its clone to the
task branch, so "branch not pushed" no longer costs a rework. A run that changed nothing pushes nothing.

## Smaller tasks before work starts (`MAX_TASK_FILES`)
The planner is told to keep tasks to at most `MAX_TASK_FILES` files; a task that still owns more is split by the planner
before anyone works on it, rather than after it hit the token cap twice.

## Model routing and A/B tests (`WORKER_MODEL_LIGHT`, `AB_MODEL`, `AB_SHARE`)
Light roles can use a cheaper model; `AB_SHARE` % of routine tasks can run on another model. The Metrics tab compares models by
quota per run, quota per merged task, first-pass approvals and token-cap rate. Whether a lower-thinking model is really cheaper
per *merged* task is exactly what this measures - a cheap run that needs a rework is not cheap.

## Days without quota (`PLAN_AHEAD`, `MAINTENANCE`)
When every worker of a project is below its quota floor, the foreman plans backlog goals ahead (orchestrator quota only), up to
about two pending tasks per worker, so work starts the moment quotas reset. And while the team is idle it checks main without
any LLM, once per new main commit and at most every 6 hours: the full suite three times (a red main becomes a goal; tests
that pass and fail on the same commit become a "flaky tests" backlog entry) and the code map (modules without a docstring).

Using a second subscription (e.g. Claude Code) as a fallback worker is deliberately not built in: it would draw from the same
limit the orchestrator needs, and a busy team can empty it within hours.
