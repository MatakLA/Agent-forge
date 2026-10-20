# Waiting for model quota

Things that need real worker turns (Gemini weekly quota) and must NOT be started while the quota is low: every one of them
spends the same quota the dev team needs for goals. Last updated 2026-10-20. Quota resets about 2026-10-23T23:47Z; check
`bin/foreman quota` first and start only when the 5 h and weekly percentages are above `QUOTA_MIN_*_PCT`.

## 1. Measure what is already deployed (3dd5f01 + the self-check / salvage fixes)

| What | How | Decides |
|------|-----|---------|
| Benchmark v4 vs v6 vs v7 vs v8 (see `bench/versions.json`) | `BENCH_MINUTES=15 BENCH_WORKER=<worker> python3 bench/run_bench.py run v4,v6,v7,v8 1`, then `python3 bench/run_bench.py chart` | Do line spans / callers / start state (v6) save tokens against v4? Is the batching rule alone (v8) enough? Is the cache-friendly prompt order (v7) worth it? |
| First real run with ORIENT on | `runs/<id>/setup.log` shows the branch checkout, `prompt.md` has a "Start state" block, `stream.jsonl` has fewer orientation git calls than the ~21 per run measured before | Whether `ORIENT` / `PACK_EXTRAS` stay `on` |
| Self-check fix | `meta.json` `self_checks[].mode` is `full` once, then `changed`; `duration_s` of ok runs drops by about 10 min; `runs/self-check-slow*.json` exists | Nothing to decide - confirm it works |
| Salvage fix | count of reworks with "branch was not pushed" per day (175 before) | Nothing to decide - confirm it dropped |

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

## 3. Needs data, not quota

- `QUALITY_CHECK=block`: count `== QUALITY:` lines in the gate output of a few days of runs under `warn`, then decide.
  (Today the reviewer already rejects on these warnings: 10 of the last rejections cite the 1.5x test ratio.)

## Rules while waiting

- Do not start benchmark runs, A/B shares (`AB_SHARE`) or new goals "to see what happens".
- Deterministic work (gate, supervisor, foreman, docs, tests with a fake model) needs no quota and can go on.
