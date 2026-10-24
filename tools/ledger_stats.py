#!/usr/bin/env python3
"""Parallelism figures from a run ledger (runs/ledger.jsonl): how many workers ran at once, how much work got done per hour at each
level, and what running the same runs one after another would have taken. Aggregates only: no task names, no paths.
Usage: python3 tools/ledger_stats.py runs/ledger.jsonl > docs/parallel-data.json"""
import datetime as dt, heapq, json, statistics, sys

MAX_RUN_S = 4 * 3600          # longer records are crashes or clock jumps, not runs
GROUPS = (("1", 1, 1), ("2", 2, 2), ("3-4", 3, 4), ("5-8", 5, 99))


def _ts(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()


def load(path):
    """Executed runs (tokens > 0, not a test or benchmark) with start/end epoch seconds."""
    runs = []
    for line in open(path, encoding="utf-8"):
        try:
            r = json.loads(line)
            if r.get("test") or r.get("project") == "bench" or not r.get("tokens"):
                continue
            s = dt.datetime.strptime(r["run_id"][:15], "%Y%m%dT%H%M%S").replace(tzinfo=dt.timezone.utc).timestamp()
            e = _ts(r["at"])
        except (ValueError, KeyError, TypeError):
            continue
        if 0 < e - s <= MAX_RUN_S:
            runs.append(dict(s=s, e=e, ok=r.get("reason") == "ok", timeout=r.get("reason") == "timeout", tokens=r["tokens"]))
    return sorted(runs, key=lambda r: r["s"])


def concurrency_at_start(runs):
    """Number of runs in progress when each run started, itself included."""
    ends, out = [], []
    for r in runs:
        while ends and ends[0] <= r["s"]:
            heapq.heappop(ends)
        heapq.heappush(ends, r["e"])
        out.append(len(ends))
    return out


def active_seconds(runs):
    """Time with at least one run in progress."""
    total, cur_s, cur_e = 0.0, None, None
    for r in runs:                      # sorted by start
        if cur_e is None or r["s"] > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = r["s"], r["e"]
        else:
            cur_e = max(cur_e, r["e"])
    return total + (cur_e - cur_s if cur_e is not None else 0)


def stats(runs):
    if not runs:
        return {}
    k = concurrency_at_start(runs)
    groups = []
    for name, lo, hi in GROUPS:
        rs = [r for r, kk in zip(runs, k) if lo <= kk <= hi]
        if not rs:
            continue
        kbar = statistics.mean(kk for kk in k if lo <= kk <= hi)
        mean_h = statistics.mean(r["e"] - r["s"] for r in rs) / 3600
        ok = sum(r["ok"] for r in rs)
        per_worker = ok / len(rs) / mean_h
        groups.append(dict(group=name, runs=len(rs), workers_avg=round(kbar, 1), ok_pct=round(100 * ok / len(rs)),
                           timeout_pct=round(100 * sum(r["timeout"] for r in rs) / len(rs)), mean_run_min=round(mean_h * 60, 1),
                           ok_runs_per_worker_hour=round(per_worker, 2), ok_runs_per_hour_team=round(kbar * per_worker, 2),
                           tokens_per_ok_run=round(sum(r["tokens"] for r in rs) / ok) if ok else None))
    for g in groups:
        g["speedup_vs_one"] = round(g["ok_runs_per_hour_team"] / groups[0]["ok_runs_per_hour_team"], 2)
    busy_h = sum(r["e"] - r["s"] for r in runs) / 3600
    active_h = active_seconds(runs) / 3600
    alone = [r["e"] - r["s"] for r, kk in zip(runs, k) if kk == 1]
    alone_h = len(runs) * statistics.mean(alone) / 3600 if alone else None
    return dict(runs=len(runs), tokens=sum(r["tokens"] for r in runs), peak_workers=max(k),
                first_run=dt.datetime.fromtimestamp(runs[0]["s"], dt.timezone.utc).strftime("%Y-%m-%d"),
                last_run=dt.datetime.fromtimestamp(runs[-1]["e"], dt.timezone.utc).strftime("%Y-%m-%d"),
                worker_hours_busy=round(busy_h, 1), hours_with_work_running=round(active_h, 1),
                avg_workers_while_active=round(busy_h / active_h, 2),
                one_after_another_hours_same_run_length=round(busy_h, 1),
                one_after_another_hours_run_length_when_alone=round(alone_h, 1) if alone_h else None,
                calendar_speedup_range=[round(alone_h / active_h, 1), round(busy_h / active_h, 1)] if alone_h else None,
                by_workers_running=groups)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(json.dumps(stats(load(sys.argv[1])), indent=1))
