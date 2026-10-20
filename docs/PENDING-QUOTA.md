# Waiting for model quota

Things that need real worker turns (Gemini weekly quota) and must NOT be started while the quota is low: every one of them
spends the same quota the dev team needs for goals. Last updated 2026-10-20 (scripted quality checks added). Quota resets about 2026-10-23T23:47Z; check
`bin/foreman quota` first and start only when the 5 h and weekly percentages are above `QUOTA_MIN_*_PCT`.

## 1. Measure what is already deployed (3dd5f01 + the self-check / salvage fixes)

| What | How | Decides |
|------|-----|---------|
| Benchmark v4 vs v6 vs v7 vs v8 (see `bench/versions.json`) | `BENCH_MINUTES=15 BENCH_WORKER=<worker> python3 bench/run_bench.py run v4,v6,v7,v8 1`, then `python3 bench/run_bench.py chart` | Do line spans / callers / start state (v6) save tokens against v4? Is the batching rule alone (v8) enough? Is the cache-friendly prompt order (v7) worth it? |
| First real run with ORIENT on | `runs/<id>/setup.log` shows the branch checkout, `prompt.md` has a "Start state" block, `stream.jsonl` has fewer orientation git calls than the ~21 per run measured before | Whether `ORIENT` / `PACK_EXTRAS` stay `on` |
| Self-check fix | `meta.json` `self_checks[].mode` is `full` once, then `changed`; `duration_s` of ok runs drops by about 10 min; `runs/self-check-slow*.json` exists | Nothing to decide - confirm it works |
| Salvage fix | count of reworks with "branch was not pushed" per day (175 before) | Nothing to decide - confirm it dropped |
| Scripted quality checks (self-check problems, gate flags, fast pass) | `meta.json` `self_checks[].problems` of real runs; task `history[].kind/flags`; `team_metrics().totals.rework_kinds/rework_flags`; gate log shows "== whole suite NOT run (fast pass is red)" | Reworks per merged task (before: 67 % first pass, ≈12 % token waste, 41+40+9 of 120 review rejections were scriptable) must drop; compare a week before and after. Decides: which flag becomes a hard `block`, which rules are noise |
| Duplicate-definition check on real branches | `== FLAGS:` containing `duplicate`, and the reviewer's feedback on those tasks | False positives (same name, same body but legitimately separate, e.g. per-runtime stubs) - add to `DUP_SKIP` in `bin/lib/quality.py` |

## 2. Needs model turns to try

| Idea | Why it waits | First step |
|------|--------------|------------|
| agy hooks (PreToolUse) that refuse a duplicate grep/ls and answer from the index | cannot be tested without a model turn | write the hook, run one 15 min bench task |
| `symbol` tool (MCP/CLI): definition + callers + test in one call | needs the worker image rebuilt or an MCP server registered | measure first whether steps per run still hurt after v6 |
| Trim the agy tool/skill list in the prefix | unknown how many tokens the prefix weighs | measure the size of the prefix of one fresh run, then switch off unused tools |
| `PROMPT_ORDER=cache` as default | unmeasured (bench v7) | only after the benchmark |
| Worker affinity (a worker keeps its area) | the cache does not live between conversations (resumed context about 145k, TTL about 5 min); own history 38 % vs pool 41 % first pass | only inside the cache window, after v7 |
| Smaller review input (diff of relevant files only) | most expensive orchestration step (20.7k input) | change `review-branch` input, compare review cost per merge |
| Vector index | exact identifiers are not found reliably by vectors | only if `symbol` is not enough |
| Cleanup goals: de-duplicate the ≈280 duplicate definition pairs already in the measured project (135 in tests; `escapeHtml` ×25, `eu_offset_hours` ×17, `setup_db` ×16, `request_http` ×13, DOM stubs ×7-8 each) into shared modules; split the 100 of 191 source files over 400 lines; cut the 205 k test lines vs 94 k code lines | real worker turns; large diffs touch many files, so each goal must stay under `MAX_TASK_FILES` - one helper family per goal | backlog goals 25/30 on the devbox; start after the quota reset, one goal at a time, with the new duplicate check on |
| Lint / static analysis in the gate (pyflakes or ruff) | not in the worker image (`sandbox-worker:1.2.11` has Python 3.14.4 + pytest only); an image rebuild must not happen while a run is active; the stdlib-only unused-import check covers the main case meanwhile | build the image with pyflakes, add it to the gate behind `QUALITY_CHECK`, compare findings with the stdlib check on real branches |
| Code map richer than one line per module (signature + one-line purpose per public function) | the map is generated; the question is whether workers read less when it is richer - only a model run shows that | extend the generator, run the 15 min benchmark task |
| Faster gate through parallelism (pytest-xdist or more CPUs for the gate container) | the gate runs `--cpus 1` (6315 tests, 654 s); 6 CPUs on devbox, but parallel runs share ports/temp files/SQLite - flake risk needs real suite runs, not just a fake model | try on the measured project's suite once the team is idle: same suite twice, compare failures |

## 3. Needs data, not quota

- `QUALITY_CHECK=block`: count `== QUALITY:` lines in the gate output of a few days of runs under `warn`, then decide.
  (Today the reviewer already rejects on these warnings: 41 of 120 review rejections cite the quality gate / test ratio, 40 cite files outside the allowed list.)
  Since the scripted checks, count per flag (`rework_flags`) how often the gate still flags something the worker did not fix in its
  self-check: those are the candidates for `block`.
- Why tests fail in the clean container (212 "tests fail in clean container" reworks): the cause is now recorded as `kind`
  (`tests-own` / `tests-existing`) but not yet classified further (missing file in the branch, order dependence, timezone/clock,
  network, leftover state). After a week of data, read the `gate_tail` of those reworks and add the top cause as a self-check rule.
- Weakened-test and duplicate rules: tune the thresholds (`DUP_MIN_LINES` etc. in `bin/lib/quality.py`) on the real flags.

## Rules while waiting

- Do not start benchmark runs, A/B shares (`AB_SHARE`) or new goals "to see what happens".
- Deterministic work (gate, supervisor, foreman, docs, tests with a fake model) needs no quota and can go on.
