# Why use Agent-Forge? The numbers, and where they stop

![Agent-Forge in four numbers](img/results-headline.svg)

This page makes the case for Agent-Forge with figures from a real install and says plainly where the case ends. Every number links to its source:
the [field report](FIELD-REPORT.md) (what one install did), the [results page](RESULTS.md) (charts for the upgrades and for parallel workers) and
the data files they are computed from, which are committed in this repository.

**The case in one paragraph.** If you have a backlog of small, testable tasks, Agent-Forge worked through hundreds of them without supervision:
783 tasks merged in about three weeks on two projects. A gate and an AI reviewer sent back 34-42 % of the worker's first attempts before they reached
`main`. A merged task cost 1-2 % of one weekly model quota. With about six workers at once it finished three times as many runs per hour as with one.
What it did not do: make the model right on the first try, avoid waste (about a quarter of the tasks were cancelled), or prove that the merged code is
good. And all of this comes from one install run by the author; there are no numbers from anyone else's install in this repository yet.

## 1. It keeps working while you are away

| Measured | Number | Source |
|---|---|---|
| Tasks merged on Agent-Forge's own development | 221 in 5 days, up to 53 a day | [field report 2](FIELD-REPORT.md#2-the-agent-forge-project-221-merged-tasks-in-five-days) |
| Tasks merged on a private application project | 562 in 19 days (2026-10-02 to 10-20), about 30 a day | [field report 5](FIELD-REPORT.md#5-before-and-after-the-090-efficiency-features) |
| Time from the last dispatch to the merge (median) | 12.5 min (p90 30 min) on Agent-Forge; 16 min then 35 min on the private project | field report 2, 5 |
| Time from the end of a worker run to the verdict (median) | 1.7 min | field report 2 |
| Size of a merged task (median) | 183 changed lines (p90 593) | field report 2 |

The tasks are small on purpose. This is a queue of many small changes, not one large feature written in a single sitting.

## 2. Parallel workers: about three times the throughput

![Throughput by concurrency](img/results-parallel.svg)

Measured from the run ledger, for every executed run: how many runs were in progress when it started.

- With 5-8 workers running, the team finished **10.5 runs per hour, 3.1 times one worker's 3.3**. That is about half of perfect scaling, because the workers share one machine and one model quota.
- Running the same 1,927 runs **one after another would have taken 2.0-2.8 times more calendar time**, for the same tokens.
- The price: runs take longer (14 to 24 min) and more of them hit the time limit (10 % to 24 %). The limit that matters is the model quota, not the number of workers.

## 3. Nothing reaches main unchecked, and workers are often wrong

![What the gate and the reviewer sent back](img/results-gate.svg)

- On Agent-Forge's own 221 merged tasks, **92 (42 %) were sent back at least once**, 198 times in total. On the private project the first-pass rate was 66 %, so 34 % were sent back.
- The mutation check found 28 times that a worker's tests would not have noticed a deliberately planted bug. The gate flagged a secret-looking string in a branch 7 times.
- The reviewers are not rubber stamps: they rejected 117 of 336 verdicts (35 %).
- Without the loop these branches would have gone to `main` as the worker left them. That experiment was not run, and the reviewer is a model too, so some of the send-backs may have been needless.

## 4. It spends fewer tokens than it used to

![Before and after the 0.9.0 efficiency features](img/results-upgrades.svg)

- After the 0.9.0 features, the median merged task cost **57 % fewer tokens** (1.08M to 466k); the interval of the difference excludes zero. Runs killed by the token cap fell from 22 % to 0.2 %.
- The **context pack**, tested with tasks assigned at random in two independent samples, cut median tokens per merged task by 27 % (p = 0.016) and 16 % (p = 0.006). In the larger sample the first-pass rate was also higher, 72 % against 61 % (difference 2-20 points, p = 0.02).
- A merged task used 1.4-1.9 % of one weekly account quota on average, so one account-week buys roughly 40-80 merged tasks. The quota figure includes whatever else used the account at the same time. The orchestrator's calls had a list price of $0.33 per merged task; the install runs on subscriptions, so that is a size, not a bill.
- The same upgrades made runs twice as long and many more end at the time limit (3 % to 29 %). Both effects are real, and the data cannot separate the change itself from the load of running many workers at once.

## 5. What it costs you

| Cost | Number |
|---|---|
| Tasks that were cancelled | 69 of 290 (24 %), and they took 23 % of the tokens |
| Tasks blocked at least once | 33 of 290 (11 %); 11 merged afterwards, 22 were cancelled |
| Bugs in the first week | two loops wrote 1,276 zero-token records (69 % of the run records). The team built a detector for the first one through its own pipeline, about 22 hours after the worst loop began. The field report does not claim the second (a dirty worker clone) is fixed, and the dashboard's per-run columns still count those records |
| Slower runs under load | 14 min with one worker, 24 min with 5-8 |
| Egress alarms | 4 in the project's window; dispatching was refused 4 times until an alarm was acknowledged; none was classified as real or false |
| Setup | an Ubuntu machine or VM with Docker, accounts for a worker runtime and an orchestrator, about 20 minutes to install |
| Maturity | alpha, see [SECURITY.md](../SECURITY.md) before pointing it at anything that matters |

## 6. Compared with what?

| Alternative | What the data says |
|---|---|
| **One worker, no parallelism** | Measured: 3.1x the finished runs per hour with about six workers, 2.0-2.8x less calendar time, the same tokens (section 2). |
| **The same workers without the gate and the reviewer** | Not run as an experiment. Indirect evidence: 34-42 % of first attempts were sent back (section 3). |
| **You, or a team, doing the work by hand** | No data. Nothing here says Agent-Forge is faster, cheaper or better than a person. |
| **A single interactive AI session, or another agent framework** | No data. Not claimed. |

## 7. When it is not worth it

This list is our judgement, not a measurement.

- **You have no tests.** The gate and the reviewer judge a branch against your test suite and the task's criteria. With nothing to run, you get the workers' output with much weaker protection.
- **You have one change to make.** Installing and configuring the system takes longer than doing it in a single session.
- **The code is critical and nobody will read the diff.** Merged means the gate was green and a model approved. It does not mean correct.
- **Your tasks are exploratory.** The loop works best on tasks with a checkable result. About a quarter of the tasks here still ended cancelled.
- **You expect more than your model quota allows.** More workers do not buy more work once the quota is the limit.

## 8. Check it on your own install

The claims above are cheap to falsify. Run it for a week on your project, then compute the same figures from your ledger and compare:

```
python3 tools/ledger_stats.py runs/ledger.jsonl     # throughput by workers running, tokens, time-limit stops
```

[FIELD-REPORT.md](FIELD-REPORT.md#how-to-reproduce-on-your-own-install) shows how to compute the first-pass rate, the send-back causes and the rest. If your
numbers are worse than ours, that is a result worth having: open an issue with the aggregates (no task names, no code) and it goes into the next version of this page.

## What this page does not show

- That it works on another install, another model family, another language or another operator.
- A comparison with a human team, a single agent without the loop, or another tool.
- That merged changes are correct or maintainable.
- Money cost: the install runs on subscriptions.
- Anything about changes made on or after 2026-10-20, such as the scripted quality checks: their effect is [unmeasured](FIELD-REPORT.md#6-effect-unmeasured).
