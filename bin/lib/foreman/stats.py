"""Numbers learned from this project's history - finished runs and tasks - for the planner, the router and the dashboard:
run statistics (cached), efficiency by worker model / context pack with confidence intervals, what tasks of a given size and role
really cost here, an estimate for a task before it is dispatched, and model routing (per role, the cheapest worker model that has
proven itself). Everything here is pure arithmetic over files; no LLM, no network."""
import math, os

import sbx

from . import ctx
from .util import slugify
from .store import jload, jsave


def run_stats():
    """Per finished run of this project: model, variant, reason, tokens, tool calls, self-checks and the share of the account's
    WEEKLY worker-model quota the run used (agy /usage before/after). Finished runs never change, so they are cached."""
    p = os.path.join(ctx.FD, "run-stats.json")
    cache = jload(p, {})
    try:
        fam = str(sbx.load_limits().get("QUOTA_MODEL_FILTER", "gemini")).lower()
    except SystemExit:                          # no etc/limits.conf yet (fresh checkout, selftest)
        fam = "gemini"

    def week(q):
        for r in q if isinstance(q, list) else []:
            if fam in str(r.get("model", "")).lower() and "week" in str(r.get("label", "")).lower():
                return r.get("pct")
    changed = False
    for d in sbx.run_dirs():
        rid = os.path.basename(d)
        if rid in cache or not os.path.exists(os.path.join(d, "finished")):
            continue
        m = sbx.meta(d)
        if m.get("test") or m.get("project", "") != sbx.PROJECT:
            cache[rid] = 0; changed = True; continue
        wb, wa = week(m.get("quota_before")), week(m.get("quota_after"))
        rt = m.get("runtime") or "agy"
        cache[rid] = dict(task=str(m.get("task", "")).removesuffix(".md"), runtime=rt, cost=m.get("cost_usd"),
                          model=(m.get("model") or "?") if rt == "agy" else f"{rt}:{m.get('model') or 'default'}", variant=m.get("variant") or "",
                          reason=m.get("reason"), tokens=m.get("tokens") or 0, tools=m.get("tool_calls") or 0,
                          week=round(wb - wa, 2) if wb is not None and wa is not None and wb >= wa else None,
                          checks=[c.get("ok") for c in m.get("self_checks") or []], worker=m.get("worker"),
                          duration=m.get("duration_s"), finished=m.get("finished"))
        changed = True
    if changed:
        jsave(p, cache)
    return {k: v for k, v in cache.items() if v}


# ---------------------------------------------------------------- confidence intervals (pure, selftested)
def wilson(k, n, z=1.96):
    """95 % Wilson interval of a rate k/n, in percent: (low, high); (None, None) without data."""
    if not n:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(100 * max(0.0, c - h)), round(100 * min(1.0, c + h))


def mean_ci(vals, z=1.96):
    """(mean, low, high) of a list of numbers with a normal-approximation 95 % interval; None for fewer than 2 values."""
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return None
    m = sum(vals) / len(vals)
    sd = math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))
    h = z * sd / math.sqrt(len(vals))
    return round(m, 4), round(m - h, 4), round(m + h, 4)


def first_pass(t):
    return not any(h.get("outcome") == "rework" for h in t.get("history", []))


def per_task(ts, rs):
    """Task id -> what all of its runs cost together: tokens, runs, USD, % of one account's weekly quota, its models."""
    out = {}
    for r in rs.values():
        e = out.setdefault(r["task"], dict(tokens=0, runs=0, cost=0.0, week=0.0, week_n=0, caps=0, models=set()))
        e["tokens"] += r.get("tokens") or 0
        e["runs"] += 1
        e["cost"] += r.get("cost") or 0
        e["caps"] += r.get("reason") in ("token-cap", "timeout")
        if r.get("week") is not None:
            e["week"] += r["week"]; e["week_n"] += 1
        e["models"].add(r.get("model") or "?")
    return out


def efficiency(ts, rs):
    """Quota / money per merged task by worker model and by context pack on/off (A/B) - with 95 % intervals for the first-pass
    rate and for the cost per merged task - plus what the self-check fixed."""
    med = lambda v: sorted(v)[len(v) // 2] if v else None
    groups = {}
    pt = per_task(ts, rs)
    for r in rs.values():
        pack = "pack" if r["variant"].startswith("pack") else "no pack" if r["variant"].startswith("nopack") else "not measured"
        for dim, key in (("model", r["model"]), ("pack", pack)):
            g = groups.setdefault(dim, {}).setdefault(key, dict(runs=0, ok=0, caps=0, week=0.0, week_n=0, tok=[], tools=[], tasks=set(), cost=0.0))
            g["runs"] += 1; g["ok"] += r["reason"] == "ok"; g["caps"] += r["reason"] in ("token-cap", "timeout")
            g["cost"] += r.get("cost") or 0
            if r["week"] is not None:
                g["week"] += r["week"]; g["week_n"] += 1
            g["tok"].append(r["tokens"]); g["tools"].append(r["tools"]); g["tasks"].add(r["task"])
    out = {}
    status = {t["id"]: t for t in ts}
    for dim, gs in groups.items():
        out[dim] = {}
        for key, g in gs.items():
            done = [status[x] for x in g["tasks"] if x in status and status[x]["status"] == "done"]
            fp = sum(1 for t in done if first_pass(t))
            weeks = [pt[t["id"]]["week"] for t in done if pt.get(t["id"], {}).get("week_n")]
            costs = [pt[t["id"]]["cost"] for t in done if pt.get(t["id"], {}).get("cost")]
            out[dim][key] = dict(runs=g["runs"], ok_pct=round(100 * g["ok"] / g["runs"]), cap_pct=round(100 * g["caps"] / g["runs"]),
                                 week_per_run=round(g["week"] / g["week_n"], 2) if g["week_n"] else None,
                                 week_per_done=round(g["week"] * g["runs"] / max(g["week_n"], 1) / len(done), 2) if done and g["week_n"] else None,
                                 tokens_median=med(g["tok"]), tools_median=med(g["tools"]), done=len(done),
                                 cost_usd=round(g["cost"], 2) if g["cost"] else None,
                                 cost_per_done=round(g["cost"] / len(done), 2) if g["cost"] and done else None,
                                 first_pass_pct=round(100 * fp / len(done)) if done else None,
                                 first_pass_ci=list(wilson(fp, len(done))), week_done_ci=mean_ci(weeks) if g["week_n"] else None,
                                 cost_done_ci=mean_ci(costs) if g["cost"] else None)
    chk = [r["checks"] for r in rs.values() if r["checks"]]
    out["self_check"] = dict(runs=len(chk), green_first=sum(1 for c in chk if c[0]),
                             fixed=sum(1 for c in chk if not c[0] and c[-1]), still_red=sum(1 for c in chk if not c[-1]))
    return out


def ab_verdict(a, b, a_name, b_name):
    """Compare two efficiency groups (dicts from efficiency()): which one is cheaper per merged task without approving less work
    first time - only when the 95 % intervals say so. Returns dict(winner|None, text, need_more|None)."""
    if not a or not b:
        return dict(winner=None, text="no data for one of the two yet", need_more=None)
    use = "cost_done_ci" if (a.get("cost_done_ci") and b.get("cost_done_ci")) else "week_done_ci"
    unit = "$" if use == "cost_done_ci" else "% of a week's quota"
    ca, cb = a.get(use), b.get(use)
    if not ca or not cb:
        return dict(winner=None, text=f"too few merged tasks to compare ({a.get('done', 0)} vs {b.get('done', 0)})",
                    need_more=max(0, 5 - min(a.get("done", 0), b.get("done", 0))))
    fa, fb = a.get("first_pass_ci") or [None, None], b.get("first_pass_ci") or [None, None]
    worse_quality = lambda x, y: x[1] is not None and y[0] is not None and x[1] < y[0]        # x's best < y's worst
    if cb[2] < ca[1] and not worse_quality(fb, fa):
        return dict(winner=b_name, text=f"{b_name} is cheaper per merged task ({cb[0]:g} vs {ca[0]:g} {unit}) and approves as much first time",
                    need_more=None)
    if ca[2] < cb[1] and not worse_quality(fa, fb):
        return dict(winner=a_name, text=f"{a_name} is cheaper per merged task ({ca[0]:g} vs {cb[0]:g} {unit}) and approves as much first time",
                    need_more=None)
    gap = abs(ca[0] - cb[0]) or 1e-9
    half = max(ca[2] - ca[1], cb[2] - cb[1]) / 2
    n = max(a.get("done", 0), b.get("done", 0))
    need = max(0, math.ceil(n * (half / gap) ** 2) - n) if half > 0 else None
    return dict(winner=None, text=f"no clear difference yet ({ca[0]:g} vs {cb[0]:g} {unit} per merged task)",
                need_more=min(need, 999) if need is not None else None)


# ---------------------------------------------------------------- task sizes, estimates, routing
def files_bucket(n):
    return "4+" if n >= 4 else str(max(1, n))


def sizes(ts, rs, own_files=None):
    """What merged tasks cost here, by the number of files a task owns and by role: median tokens over all of a task's runs,
    and how often a run hit the token cap. own_files(t) -> list (default: t["files"])."""
    own_files = own_files or (lambda t: t.get("files", []))
    pt = per_task(ts, rs)
    by_files, by_role, allv = {}, {}, []
    for t in ts:
        e = pt.get(t["id"])
        if t["status"] != "done" or not e:
            continue
        allv.append(e["tokens"])
        b = by_files.setdefault(files_bucket(len(own_files(t))), dict(tokens=[], runs=0, caps=0))
        b["tokens"].append(e["tokens"]); b["runs"] += e["runs"]; b["caps"] += e["caps"]
        by_role.setdefault(slugify(t.get("role", "?")), []).append(e["tokens"])
    med = lambda v: sorted(v)[len(v) // 2] if v else None
    return dict(n=len(allv), median=med(allv),
                by_files={k: dict(n=len(v["tokens"]), median=med(v["tokens"]), cap_pct=round(100 * v["caps"] / v["runs"]) if v["runs"] else None)
                          for k, v in sorted(by_files.items())},
                by_role={k: dict(n=len(v), median=med(v)) for k, v in sorted(by_role.items())})


def size_hint(sz, budget):
    """A few lines for the planning prompt: what tasks really cost in this project (needs >= 5 merged tasks)."""
    if not sz or sz.get("n", 0) < 5:
        return ""
    lines = [f"## What tasks cost in this project (from {sz['n']} merged tasks; all runs of a task, in tokens)"]
    for k, v in sz["by_files"].items():
        if v["n"]:
            lines.append(f"- owning {k} file(s): median {v['median']:,} ({v['n']} tasks)" +
                         (f", {v['cap_pct']} % of runs hit the token cap" if v["cap_pct"] else ""))
    roles = [(k, v) for k, v in sz["by_role"].items() if v["n"] >= 2]
    if roles:
        lines.append("- by role: " + ", ".join(f"{k} {v['median']:,}" for k, v in sorted(roles, key=lambda kv: -kv[1]["median"])[:8]))
    lines.append(f"Size tasks so that each needs about {int(budget):,} tokens; split those that would need more.")
    return "\n".join(lines) + "\n"


def estimate(t, sz, own_files=None):
    """Expected tokens for task `t` before it runs: the median of merged tasks of the same role and size here, else of the same
    size, else of all; None without history. Returns dict(tokens, basis) or None."""
    if not sz or not sz.get("n"):
        return None
    own_files = own_files or (lambda x: x.get("files", []))
    b = files_bucket(len(own_files(t)))
    role = sz["by_role"].get(slugify(t.get("role", "?")))
    if role and role["n"] >= 3:
        fb = sz["by_files"].get(b)
        tok = role["median"] if not fb or not fb["n"] else int((role["median"] + fb["median"]) / 2)
        return dict(tokens=tok, basis=f"{role['n']} merged {slugify(t.get('role', '?'))} tasks")
    fb = sz["by_files"].get(b)
    if fb and fb["n"] >= 3:
        return dict(tokens=fb["median"], basis=f"{fb['n']} merged tasks owning {b} file(s)")
    return dict(tokens=sz["median"], basis=f"all {sz['n']} merged tasks")


def usd_per_token(rs):
    """Observed USD per token by runtime (API runs with a cost), e.g. {'claude': 3.1e-06}."""
    acc = {}
    for r in rs.values():
        if r.get("cost") and r.get("tokens"):
            a = acc.setdefault(r.get("runtime") or "agy", [0.0, 0])
            a[0] += r["cost"]; a[1] += r["tokens"]
    return {k: v[0] / v[1] for k, v in acc.items() if v[1]}


def routing_table(ts, rs):
    """role -> worker model -> dict(done, first_pass_pct, week_per_done, cost_per_done) from this project's merged tasks
    (a task counts for the model of its first run)."""
    first = {}
    for rid in sorted(rs):
        r = rs[rid]
        first.setdefault(r["task"], r.get("model") or "?")
    pt = per_task(ts, rs)
    out = {}
    for t in ts:
        if t["status"] != "done" or t["id"] not in first:
            continue
        e = out.setdefault(slugify(t.get("role", "?")), {}).setdefault(first[t["id"]], dict(done=0, fp=0, week=[], cost=[]))
        e["done"] += 1; e["fp"] += first_pass(t)
        p = pt.get(t["id"], {})
        if p.get("week_n"):
            e["week"].append(p["week"])
        if p.get("cost"):
            e["cost"].append(p["cost"])
    for role in out.values():
        for m, e in role.items():
            role[m] = dict(done=e["done"], first_pass_pct=round(100 * e["fp"] / e["done"]),
                           week_per_done=round(sum(e["week"]) / len(e["week"]), 3) if e["week"] else None,
                           cost_per_done=round(sum(e["cost"]) / len(e["cost"]), 4) if e["cost"] else None)
    return out


def pick_model(role, table, candidates, min_tasks, min_first_pass):
    """The cheapest of `candidates` that has proven itself for `role` here (>= min_tasks merged, >= min_first_pass % approved first
    time), by quota per merged task (or USD for API models); None when no candidate qualifies."""
    rows = table.get(slugify(role or "?"), {})
    ok = [(m, rows[m]) for m in candidates if m and m in rows and rows[m]["done"] >= min_tasks and rows[m]["first_pass_pct"] >= min_first_pass]
    cost = lambda e: e["week_per_done"] if e["week_per_done"] is not None else (e["cost_per_done"] if e["cost_per_done"] is not None else 1e9)
    return min(ok, key=lambda me: (cost(me[1]), -me[1]["first_pass_pct"]))[0] if ok else None
