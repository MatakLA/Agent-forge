# Does it pay off? Results, with the limits

Short answer: **yes for tokens and for throughput, no for speed or reliability per run, and not proven for the newest changes.**
Everything below comes from the ledger of one real install (one operator, one project mix, one model account), not from a lab.
The numbers are committed ([`field-report-data.json`](field-report-data.json), [`parallel-data.json`](parallel-data.json),
[`bench/results/runs.jsonl`](../bench/results/runs.jsonl)) and the charts are drawn from them by
[`tools/results_charts.py`](../tools/results_charts.py). Method and wording of the statistics: [FIELD-REPORT.md](FIELD-REPORT.md).

## 1. Did the efficiency upgrades pay off?

![Before and after the 0.9.0 efficiency features](img/results-upgrades.svg)

- **Tokens per merged task halved** (median 1.08M to 466k); the interval of the difference excludes zero. Runs killed by the token cap went from 22 % to 0.2 %.
- **Quality did not change**: first-pass rate stayed about 66 %. The upgrades save money, they do not make the model smarter.
- **Speed and reliability got worse**: a run takes 25.8 min instead of 12.2 and 29 % end at the time limit instead of 3 %. Part of this is the change itself
  (longer, more careful runs), part is the load from running many workers at once (section 3). The data cannot tell the two apart.
- This is a before/after on one project, not an experiment. Other things changed in the same weeks.

![Context pack, randomised](img/results-pack.svg)

The context pack is the one change tested with randomly assigned tasks: median tokens per merged task **-27 %** (p = 0.016). Tool calls are borderline,
first pass and reworks show no difference. One effect is established, the others are not.

## 2. The newest changes are not proven

![Benchmark, one run per prompt version](img/results-bench.svg)

Four prompt versions, one run each, same small task. All passed their checks; token counts differ by up to 26 %, which at n = 1 per version is noise.
In particular, **this benchmark does not show that the orientation features of v6 beat the plain v4 prompt**, and the lean v8 was as good as the full v6.
Each run costs about 7 % of one account's weekly quota, so a proper repeat is a decision about quota, not a coding task. Nothing from 2026-10-20 on is in the
before/after numbers either. We do not claim it works until it is measured.

## 3. Eight workers at once or one after another?

![Throughput by concurrency](img/results-parallel.svg)

Measured from the ledger: for every executed run, how many runs were in progress when it started.

| Workers running | Runs | Done ok per hour (team) | vs one worker | Mean run | Time-limit stops |
|---|---|---|---|---|---|
| 1 | 404 | 3.3 | 1.0x | 14 min | 10 % |
| 2 | 263 | 5.3 | 1.6x | 16 min | 12 % |
| 3-4 | 766 | 7.6 | 2.3x | 20 min | 18 % |
| 5-8 (avg 6.2) | 494 | 10.5 | 3.1x | 24 min | 24 % |

- **Parallel pays off, but about half of the ideal.** Six workers deliver 3.1x the finished runs of one, not 6x. Runs get slower and fail more because they share
  one machine and one model quota.
- **One after another would have taken 2.0-2.8x more calendar time** for the same 1,927 runs and the same tokens: 441-612 h of worker time against 220 h
  in which anything was running. The range is the two ways to assume how long a run lasts when it is alone.
- **Tokens are not saved by running in parallel.** The cost is the same either way; only the waiting changes. The binding limit is the model quota, not the
  number of workers, so more than about 6-8 workers buys little.
- Caveat: concurrency was not assigned at random. Busy periods also had different tasks, so read the trend rather than the exact factor.

## 4. Is the project worth having then?

What is supported: a foreman that keeps a queue of tasks moving through isolated workers with a merge gate, at roughly half the tokens per merged task
and about three times the throughput of a single worker, on a real project (hundreds of merged tasks).

What is not: that it writes better code than a person or than a single careful session (first pass about 66 %, and the gate is the safety net, not proof of
quality); that the results transfer to other setups (n = 1 operator); that the newest prompt changes help. Code quality of the merged output was not assessed here.

Reproduce: `python3 tools/ledger_stats.py runs/ledger.jsonl > docs/parallel-data.json && python3 tools/results_charts.py`.
