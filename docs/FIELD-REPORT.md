# Field report: Agent-Forge on real work

What one install of Agent-Forge did over a few weeks of real use, with the numbers as they came out. This is **not** a benchmark and
it is not independent: the people who wrote the tool ran it, on the tool itself and on one private application project. Data as of
2026-10-21. The aggregates are in [field-report-data.json](field-report-data.json);
[How to reproduce](#how-to-reproduce-on-your-own-install) shows how to compute the same figures on your install.

"Interval" below always means a 95 % confidence interval.

## The short version

- **Sample:** one self-hosted install, one operator, one worker model, two projects, five days of data for the main project and eleven for the
  second one's A/B test. Small N.
- **Agent-Forge's own development** (2026-10-11 to 10-16): 221 of 290 tasks merged, 69 cancelled. **58 %** of the merged tasks went
  through with no rework (129 of 221, interval 52-65 %). A merged task cost a median of 445 k tokens and, on average, 1.9 % of one
  account's weekly quota.
- **Context pack**, tested by assigning tasks at random (not before/after): in both samples tokens per merged task were 16-27 % lower and tool
  calls 14-16 % lower with the pack. The first-pass rate was 8 and 11 points higher; only the larger sample's interval excludes zero.
  A saving in quota could not be shown.
- **The 0.9.0 efficiency features as a whole** (before/after, second project): tokens per merged task fell by more than half, the first-pass
  rate did not move (67 % to 66 %), and runs became twice as long: 29 % now end at the 30-minute limit, up from 3 %.
- **What went wrong:** two loops wrote 1,276 zero-token records, 69 % of all run records of the Agent-Forge project. 24 % of its tasks were
  cancelled and took 23 % of its tokens.
- **Unmeasured:** the effect of everything shipped on or after 2026-10-20, including the scripted quality checks.

## Read this before the numbers

What the sample is:

- **One install**, self-hosted, one operator who is also the author of the tool. Nobody else's setup, tasks or habits are in here.
- **One worker model** (`gemini-3.8-flash-high` through the Antigravity runtime) in every run that has a model recorded. Planning and review were
  done by Claude Code (`claude-cli`) and, as a fallback, by Claude through Antigravity (`agy-claude`). Nothing here compares model families.
- **Two projects.** The *Agent-Forge project* is this repository's own development; most numbers come from it (290 tasks, 2026-10-11 to 10-16).
  The *private application project* is an unrelated codebase that cannot be described further; it is used only where it gives a before/after or a
  second randomised sample (562 merged tasks, 2026-10-02 to 10-20).
- **"Merged"** means the gate was green and an automated reviewer approved. No claim is made that a human read each diff, that the code is
  correct, or that it is still good a month later.
- Rates carry Wilson intervals, differences of rates Newcombe intervals, differences of means and medians bootstrap intervals, and
  p-values come from permutation tests. Tasks inside one goal are not independent, so the intervals are probably a bit too narrow. Many
  metrics were tested and nothing is corrected for that: treat a lone p below 0.05 as a hint.
- Bad numbers are left in. Where a number is distorted by an incident, the text says so.

What this does not show is listed in [section 7](#7-what-this-does-not-show).

## 1. Definitions

| Term | Meaning here |
|---|---|
| merged task | task whose pull request was merged into the project's main branch (status `done`) |
| first pass | merged with no `rework` entry in its history, i.e. neither the gate nor the reviewer sent it back |
| rework | one send-back of a task to its worker (gate failure, mutation check, merge conflict, or the reviewer's rejection) |
| executed run | a ledger record with tokens > 0, i.e. a worker really ran |
| tokens | the total the worker runtime reported for a run |
| quota per task | change of the account's "weekly" percentage from the provider's usage screen before and after each run, summed over the task's runs; whole percent points; a run whose quota reset in between is dropped; it also includes whatever else used that account at the same time |
| list price | the dollar figure recorded for an orchestrator call; the install runs on subscription accounts, so it is a measure of size, not a bill |

## 2. The Agent-Forge project: 221 merged tasks in five days

Window: first task created 2026-10-11 14:33 UTC, last merge 2026-10-16 14:43 UTC. Eight workers appear in the ledger records.

### 2.1 Outcome

| | N | |
|---|---|---|
| tasks created | 290 | all finished: 221 merged (76 %), 69 cancelled (24 %) |
| goals | 41 | 40 done, 1 cancelled |
| merged per day | 221 | 39, 53, 53, 39, 26, 11 (first and last day partial) |
| first pass | 129 of 221 | **58 %**, interval 52-65 % |
| reworks per merged task | 221 | 0: 129 tasks, 1: 52, 2: 15, 3: 11, 4 or more: 14 |
| reworks in total | 198 | 0.90 per merged task; another 78 on tasks that were cancelled later |
| tasks blocked at least once | 33 | 11 merged afterwards, 22 cancelled |
| size of a merged task | 221 | median 183 lines added + removed, 90th percentile 593; 57,827 lines in total |

### 2.2 Why tasks were sent back

The 198 reworks on the merged tasks, by cause. The cause is assigned by keyword matching on the feedback text, so the split is approximate.

| Cause | Reworks | Share |
|---|---|---|
| reviewer: acceptance criterion not met | 49 | 25 % |
| reviewer: repeats a test-counter warning the gate had already printed | 32 | 16 % |
| merge conflict with main | 32 | 16 % |
| reviewer: change outside the allowed files | 30 | 15 % |
| mutation check: tests did not catch deliberately planted bugs | 28 | 14 % |
| branch was not pushed | 9 | 5 % |
| gate: branch adds a secret-looking string | 7 | 4 % |
| reviewer: other | 6 | 3 % |
| tests fail in the clean container | 5 | 3 % |

Shares are rounded and add up to 101 %. On the merged tasks, `claude-cli` gave 224 verdicts and rejected 76 of them; `agy-claude` gave 112 and rejected 41. The two are
not comparable: `agy-claude` is the fallback for when the other one is unavailable, so it probably saw a different mix of tasks. The seven "secret" reworks were not examined for whether the
string was a real key.

### 2.3 What a merged task cost

| | N | Median | 90th pct | Mean |
|---|---|---|---|---|
| tokens per merged task, all its runs | 221 | 445 k | 1.43 M | 710 k |
| executed runs per merged task | 221 | 1 | 4 | 2.1 |
| quota per merged task, % of one account's week | 221 | 1 | - | 1.89 (interval 1.23-2.55) |
| tokens per executed run | 576 | 330 k | 580 k | 354 k |
| tool calls per executed run | 576 | 58 | 103 | 61 |
| minutes per executed run | 576 | 9.9 | 26.1 | 11.6 |

203.6 M tokens were spent in total: **77 % on tasks that were merged, 23 % on tasks that were cancelled**. Of the 576 executed runs, 471 ended
"ok" (82 %, interval 78-85 %), 36 hit the run time limit, 36 failed, 31 lost their supervisor, 1 was stopped by the kill switch and
1 by an exhausted quota. The quota figure is coarse (whole percent points, median 1) and its mean is pulled up by a few large readings.
The planner and reviewer calls cost $72.87 at list price over 752 calls, $0.33 per merged task; that is not a bill.

### 2.4 How long it took

| | N | Median | 90th pct |
|---|---|---|---|
| created to merged | 221 | 2.3 h | 11.7 h |
| last dispatch to merged | 221 | 12.5 min | 29.7 min |
| end of the worker's run to the verdict (gate + review) | 537 | 1.7 min | 5.7 min |

Most of the created-to-merged time passes before the last dispatch (queueing, dependencies, quota waits, earlier attempts); it was not broken down further.

### 2.5 Orchestrator calls

752 calls to a Claude model for planning, review, goal checks and "what next": 90 failed (12 %). By route: `claude-cli` 15 of 465 failed (3 %), `agy-claude`
75 of 287 (26 %). By kind: reviews 80 of 582, plans 9 of 74, checks 1 of 67.
Median duration: review 36 s, plan 124 s.

### 2.6 Self-check and mutation check

When a worker finishes, the supervisor runs the project's tests again; if they are red, the worker gets the output in the same conversation and fixes it.
Across both projects 1,019 runs have such a record: 1,007 were green at the first look, 5 were fixed in a second round, 7 were still red (the Agent-Forge project:
446, 1 and 2 of 449). So it almost never fires, and this data cannot say whether it prevents anything the worker would not have caught itself.
The mutation check did something visible: it sent 28 tasks of the Agent-Forge project back because their tests missed planted bugs.

## 3. What went wrong

**Two incident loops made most of the run records meaningless.** Of 1,852 ledger records for the Agent-Forge project, only 576 are runs in which a worker
used tokens. The other 1,276 (69 %) carry no tokens, and almost all of them (1,275) are labelled `orphaned` or `setup-failed`, the outcome of two loops:

| Incident | Records | Tasks | What happened |
|---|---|---|---|
| orphan re-queue loop | 957 | 9 | a run ended "without its supervisor"; the foreman re-queued the task with "no attempt counted", and at that time nothing limited how often this could repeat. One task was re-queued 717 times in 21 hours (2026-10-11 23:23 to 10-12 20:13 UTC); 888 records fell on 10-12. |
| dirty worker clone | 349 | 14 | "clone was not clean, stashed it, task re-queued" (a run that ended `setup-failed`); the re-queue adds no rework entry and has no streak limit. 339 records fell in 2 h 45 min on 2026-10-11, two tasks 175 and 117 times. The records show no cause and the burst ended by itself; later recurrences were single events (10 in total). |

A goal to detect such loops was queued on 2026-10-12 at 20:27 UTC and built by the team through its own pipeline; the core task merged at 21:07 UTC
(it warns at `ORPHAN_WARN`, default 3, and blocks the task at `ORPHAN_LIMIT`, default 5 consecutive recoveries without progress). 19 more records followed. That is about 22 hours
from the start of the worst task's loop to the fix. Few of these records used any tokens (31 of the 957 did), but a loop that writes a record every tick for a day is a bug whatever it costs.
The dirty-clone path has no such limit today; this report does not claim it is fixed.

**The dashboard's per-run columns count those records.** For this project it shows "25 % of runs ok" and a median of 0 tokens per run. The figures in this report use
executed runs (82 % ok, 330 k tokens). This distortion is still in the tool; the report does not fix it.

**Other things that went wrong**, as counts of event lines (a repeating event is counted every time it repeats, so these are not counts of distinct incidents):

| Event | Lines |
|---|---|
| a worker needed to log in to Antigravity again ("Authentication required"), first on 2026-10-14 23:07 UTC | 53 |
| dispatch refused because the worker was busy | 55 |
| "quota low", foreman waited | 56 |
| dispatch refused because `/usage` could not be read | 25 |
| worker "out of quota until ..." | 13 |
| account eligibility check failed | 9 |
| orchestrator call failed | 14 |
| HIGH egress alarm (4 alarms in 3 runs); dispatch refused until acknowledged | 4 |

None of the egress alarms was classified as real or false here. 69 of 290 tasks (24 %) were cancelled; why was not analysed beyond "22 of them had been blocked first".

## 4. Does the context pack help?

The foreman can give half of the tasks a *context pack* (`CONTEXT_PACK=ab`): the current content of the files the task owns, the interfaces of what they import, a
one-line map of the other modules. A stable hash of the task id decides which half, so this is a randomised comparison, not a before/after.
It ran on both projects; the private project's A/B starts on 2026-10-10. Both arms ran in the same worker pool at the same time and got every other 0.9.0 feature.
The pack tested is the one of that time (files, interfaces, module map); the line spans and callers block added on 2026-10-20 are mostly not in these data.
Task size is balanced between the arms (median 180 against 185 lines in the Agent-Forge project, 586 against 601 in the other).

| Per merged task | Agent-Forge project: pack 116, no pack 105 | Private project: pack 205, no pack 193 |
|---|---|---|
| first pass | 62 % against 54 %; difference +8 points (interval -5 to +20; p = 0.28) | 72 % against 61 %; +11 points (+2 to +20; p = 0.02) |
| reworks | 0.89 against 0.90 (difference -0.46 to +0.41) | 0.44 against 0.64 (-0.40 to -0.01; p = 0.04) |
| tokens, median | 406 k against 559 k (-27 %; difference of medians -293 k to -16 k; p = 0.016) | 435 k against 519 k (-16 %; -134 k to -8 k; p = 0.006) |
| tokens, mean | 650 k against 777 k (-304 k to +56 k; p = 0.17) | 545 k against 654 k (-198 k to -21 k; p = 0.013) |
| tool calls, median | 72 against 86 (-34 to +1.5; p = 0.054) | 83 against 96 (-28 to -4; p = 0.006) |
| executed runs | 1.95 against 2.01 (-0.56 to +0.43) | 1.45 against 1.69 (-0.45 to -0.03; p = 0.026) |
| quota, mean | 1.35 % against 2.48 % (-2.7 to +0.03; p = 0.08) | 1.29 % against 1.55 % (-0.49 to -0.03; p = 0.024) |
| quota, median | 1 against 1 | 1 against 1 |

How to read this:

- **The direction is the same in both samples for tokens, tool calls, first pass and reworks.** The pack arm needed 16 % and 27 % fewer tokens (median) and 14 % and 16 %
  fewer tool calls (median). That is what the pack is meant to do: fewer steps to find things.
- **Quality is less clear.** The first-pass gain is +8 and +11 points, but only the larger sample's interval stays above zero, and reworks per task were identical in the
  smaller one. A real quality gain is plausible; this report does not prove it.
- **The quota saving is not shown.** The Agent-Forge mean of 2.48 % in the no-pack arm comes from two tasks with readings of 62 and 37 points; readings of that size may
  include other use of the same account (the quota is shared), but that was not checked. Without readings above 10 the means are 1.26 % and 1.46 %. Medians are 1 in all four groups, because the reading is in whole
  percent points.

## 5. Before and after the 0.9.0 efficiency features

The efficiency features of 0.9.0 (see the [changelog](../CHANGELOG.md): context pack, self-check in the same conversation, push after every run, pre-split of tasks that own
many files, model routing, planning ahead while quota is out) were deployed to this install on 2026-10-07 at about 17:53 UTC. No task was merged between 2026-10-05 08:45 and
2026-10-10 14:54 UTC, so the cut is clean. The *before* is the first four days of the private project's merged tasks (164 tasks, 2026-10-02 to 10-05); the *after* is 398 merged tasks
over 2026-10-10 to 10-20. Nothing else is held constant: the task mix, the gate, the reviewers and the load changed too. A before/after cannot attribute anything to one
feature; its job here is to check whether the bundle moved the numbers it was meant to move.

| | Before | After |
|---|---|---|
| merged tasks | 164 | 398 |
| **first pass** | **67 %** (110 of 164; 60-74 %) | **66 %** (264 of 398; 62-71 %) |
| reworks per merged task | 0.62 | 0.54 |
| tokens per merged task, median | 1.08 M | 466 k (difference of medians -883 k to -477 k) |
| tokens per merged task, mean | 1.53 M | 598 k (-1.13 M to -728 k) |
| quota per merged task, mean | 1.77 % | 1.41 % (-0.63 to -0.09); median 1 and 1 |
| executed runs per merged task, mean | 1.68 | 1.57 (-0.32 to +0.09) |
| tool calls per merged task, median | 76.5 | 89 (-0.5 to +21) |
| executed runs | 383 | 935 |
| median tokens per executed run | 960 k | 346 k |
| **median minutes per executed run** | **12.2** | **25.8** |
| runs stopped by the token cap | 85 of 383 (22 %; 18-27 %) | 2 of 935 (0.2 %) |
| **runs stopped by the 30-minute limit** | **13 of 383 (3 %; 2-6 %)** | **275 of 935 (29 %; 27-32 %)** |
| runs ending "ok" | 252 of 383 (66 %) | 603 of 935 (65 %) |
| last dispatch to merged, median | 15.6 min | 35.1 min |

What it says:

- **Tokens per merged task fell by more than half**, and quota per merged task fell by about a fifth on average (not in the median, which is 1 on both sides).
  The token cap stopped being the limit that ends runs.
- **The first-pass rate did not change.** The rework causes moved in different directions, per merged task: merge conflict 0.20 to 0.03, mutation survived 0.08 to 0.03,
  branch not pushed 0.16 to 0.11, but tests failing in the clean container 0.07 to 0.20 and reviewer rejections 0.12 to 0.17.
- **Runs got about twice as long, and the time limit replaced the token cap as the usual way for a run to end** (3 % to 29 %). The limits did not change
  (30 minutes and 1.5 M tokens, unchanged since 2026-09-26), so this is not a settings change. The cause was not established. One candidate that was not tested:
  the run's time limit also covers every self-check round (see [EFFICIENCY.md](EFFICIENCY.md)), which includes running the project's test suite.
- **A single day can move the rate by about 30 points.** On 2026-10-20 the first-pass rate was 38 % (26 of 69; 27-49 %), and 35 of its reworks were failures in the clean container.

The windows are short, and the pack A/B in section 4 is the cleaner evidence for one feature; this table only says what the bundle did in sum.
The older table in [EFFICIENCY.md](EFFICIENCY.md) (about 2.6 % of a weekly quota per merged task, 22 % of runs at the token cap) comes from an earlier offline analysis
of about 400 runs. Its 22 % agrees with the 85 of 383 here; its quota figure was computed differently and is not comparable with the 1.77 % above.

## 6. Effect unmeasured

No before/after comparison is possible for these yet, because they are too recent or because nothing varied.:

| Feature | On this install since | Status |
|---|---|---|
| scripted quality checks (`bin/lib/quality.py`: find in scripts what the reviewer used to send back) | 2026-10-20 19:13 UTC | 2 tasks merged since: too few to say anything. For scale: 117 of the 198 reworks in the Agent-Forge project were reviewer rejections. |
| orientation changes: line spans, callers block, start state, branch diff on rework | from 2026-10-20 | only a benchmark of prompt contents exists (`bench/`); no field data |
| salvage commits whatever the worker left, after every run | from 2026-10-20 | no field data; the exit code recorded for salvage steps is non-zero for 248 of 249 tasks, so it was not used to count saved work |
| self-check in the same conversation | 0.9.0 | fired a fix round 5 times in 1,019 recorded runs; no with/without comparison |
| model routing | 0.9.0 | every run with a model recorded used the same one, so nothing to compare |
| `PROMPT_ORDER=cache` | off by default | not measured |

## 7. What this does not show

- That Agent-Forge works on **another install**, another model family, another language or another operator. There is one of each here.
- That it is **better than a person, a team, or a single agent without the loop**. There is no such comparison; the only controlled one is the pack A/B.
- That the **merged code is correct or maintainable**. A merge means a green gate and an automated reviewer's approval. Defects found later, review time and
  the operator's own corrections are not in these data.
- **Money.** Everything ran on subscriptions; quota is the only cost measure, and it is coarse. The dollar figure is the list price of orchestrator calls only.
- **Task difficulty.** Task size in lines is similar between the arms of the A/B, but nothing here rates difficulty, and the Agent-Forge tasks are a particular kind: a Python
  and JavaScript tool with a fast test suite.
- **The long run.** The Agent-Forge project's window is five days; the A/B on the second project covers eleven.

## How to reproduce on your own install

All of it is read-only. Run it from the install's root directory. `P` is the project name; leave it empty for the default project.

```bash
P=my-project

# totals, first pass, rework kinds, A/B groups (pack / no pack) with intervals, and the dashboard's verdicts
bin/foreman --project "$P" status --dashboard | python3 -c '
import json, sys
m = json.load(sys.stdin)["metrics"]
print(json.dumps(m["totals"], indent=1))
print(json.dumps(m["efficiency"]["pack"], indent=1))
print(json.dumps(m["efficiency"]["verdicts"], indent=1))'
```

The same figures are in the dashboard under **Insights → Experiments**. The dashboard's per-run columns count every ledger record, including zero-token re-queue
records (see section 3), so compare them with the executed-run figures from the ledger below.

First-pass rate and the pack split, straight from the task files:

```bash
P=my-project
P=$P python3 - <<'PY'
import glob, json, os
p = os.environ["P"]
base = f"runs/projects/{p}/foreman" if p else "runs/foreman"
tasks = [json.load(open(f)) for f in glob.glob(base + "/tasks/[0-9]*.json")]
done = [t for t in tasks if t["status"] == "done"]
first = sum(1 for t in done if not any(h.get("outcome") == "rework" for h in t.get("history", [])))
pack = [t for t in done if (t.get("variant") or {}).get("pack")]
print(first, "of", len(done), "merged tasks went through without rework")
print(len(pack), "merged tasks had the context pack,", len(done) - len(pack), "did not")
PY
```

Executed runs (those that used tokens) from the ledger, one JSON line per run:

```bash
P=my-project
P=$P python3 - <<'PY'
import collections, json, os
p = os.environ["P"]
runs = [json.loads(l) for l in open("runs/ledger.jsonl") if l.strip()]
runs = [r for r in runs if r.get("project", "") == p and not r.get("test") and (r.get("tokens") or 0) > 0]
print(len(runs), "executed runs", dict(collections.Counter(r["reason"] for r in runs)))
PY
```

Other sources, next to the tasks in the project's `foreman/` directory: `events.jsonl` (what the foreman did and why), `llm.jsonl` (planner and reviewer calls
with duration and exit code) and `run-stats.json` (per-run quota before and after, variant, self-check results). The intervals in this report are ordinary
bootstrap and permutation resampling on per-task numbers taken from these files; when you compare two arms, compare intervals, not just means.
