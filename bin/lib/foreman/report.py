"""Read-only views for the dashboard and the CLI: timeline, run statistics, efficiency, team metrics, diffs, status."""
import datetime as dt, json, os, re, time

import sbx, runtimes

from . import ctx
from .util import fmt_ts, sh, slugify
from .store import goals, gstate, jload, jsave, tasks
from .pool import WORKER_RT, workers


# ---------------------------------------------------------------- CLI
def timeline(hours=24):
    """Worker runs that overlap the last `hours` hours: one bar per run for the dashboard's swimlanes."""
    now, since = time.time(), time.time() - hours * 3600
    out = []
    for e in sbx.read_ledger():
        if e.get("test") or e.get("project", "") != sbx.PROJECT:      # this project's runs only
            continue
        try:
            end = dt.datetime.fromisoformat(e["at"].replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        start = end - (e.get("duration_s") or 0)
        if end >= since:
            out.append(dict(worker=e.get("worker"), start=start, end=end, reason=e.get("reason"), tokens=e.get("tokens", 0),
                            task=str(e.get("task", "")).removesuffix(".md")))
    for d in sbx.run_dirs():
        if sbx.is_running(d):
            m = sbx.meta(d)
            if m.get("test") or m.get("project", "") != sbx.PROJECT:
                continue
            try:
                start = dt.datetime.fromisoformat(sbx.read(d + "/started").replace("Z", "+00:00")).timestamp()
            except Exception:
                start = now
            out.append(dict(worker=m.get("worker"), start=start, end=None, reason="running", tokens=sbx.stream_tokens(d + "/stream.jsonl"),
                            task=str(m.get("task", "")).removesuffix(".md")))
    return out


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
                          checks=[c.get("ok") for c in m.get("self_checks") or []])
        changed = True
    if changed:
        jsave(p, cache)
    return {k: v for k, v in cache.items() if v}


def efficiency(ts, rs):
    """Quota per merged task by worker model and by context pack on/off (A/B), plus what the self-check fixed."""
    med = lambda v: sorted(v)[len(v) // 2] if v else None
    groups = {}
    tmodel = {t["id"]: (t.get("variant") or {}) for t in ts}
    for r in rs.values():
        v = tmodel.get(r["task"], {})
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
            fp = sum(1 for t in done if not any(h.get("outcome") == "rework" for h in t.get("history", [])))
            out[dim][key] = dict(runs=g["runs"], ok_pct=round(100 * g["ok"] / g["runs"]), cap_pct=round(100 * g["caps"] / g["runs"]),
                                 week_per_run=round(g["week"] / g["week_n"], 2) if g["week_n"] else None,
                                 week_per_done=round(g["week"] * g["runs"] / max(g["week_n"], 1) / len(done), 2) if done and g["week_n"] else None,
                                 tokens_median=med(g["tok"]), tools_median=med(g["tools"]), done=len(done),
                                 cost_usd=round(g["cost"], 2) if g["cost"] else None,
                                 cost_per_done=round(g["cost"] / len(done), 2) if g["cost"] and done else None,
                                 first_pass_pct=round(100 * fp / len(done)) if done else None)
    chk = [r["checks"] for r in rs.values() if r["checks"]]
    out["self_check"] = dict(runs=len(chk), green_first=sum(1 for c in chk if c[0]),
                             fixed=sum(1 for c in chk if not c[0] and c[-1]), still_red=sum(1 for c in chk if not c[-1]))
    return out


def team_metrics(ts=None, days=7):
    """What works: per role first-pass approvals, reworks, blocks, tokens per merged task; per day merged tasks; per provider calls."""
    ts = ts if ts is not None else tasks()
    tok = {}
    for e in sbx.read_ledger():
        if not e.get("test"):
            k = str(e.get("task", "")).removesuffix(".md")
            tok[k] = tok.get(k, 0) + (e.get("tokens") or 0)
    roles = {}
    for t in ts:
        if t["status"] == "cancelled":
            continue
        r = roles.setdefault(slugify(t.get("role", "?")), dict(tasks=0, done=0, first_pass=0, reworks=0, blocked=0, tokens_done=0, lines_done=0))
        r["tasks"] += 1
        reworks = sum(1 for h in t.get("history", []) if h.get("outcome") == "rework")
        r["reworks"] += reworks
        if t["status"] == "blocked":
            r["blocked"] += 1
        if t["status"] == "done":
            r["done"] += 1
            r["first_pass"] += reworks == 0
            r["tokens_done"] += tok.get(t["id"], 0)
            r["lines_done"] += sum((t.get("diff") or {}).get(k, 0) for k in ("added", "removed"))
    for r in roles.values():
        r["first_pass_pct"] = round(100 * r["first_pass"] / r["done"]) if r["done"] else None
        r["tokens_per_done"] = round(r["tokens_done"] / r["done"]) if r["done"] else None
    per_day = {}
    for t in ts:
        if t["status"] == "done" and t.get("finished"):
            per_day[t["finished"][:10]] = per_day.get(t["finished"][:10], 0) + 1
    last = sorted(per_day.items())[-days:]
    prov, cutoff = {}, (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        for l in open(os.path.join(ctx.FD, "llm.jsonl")):
            e = json.loads(l)
            if e.get("at", "") >= cutoff:
                who = e.get("who", "?").split("@")[0]
                p = prov.setdefault(who, dict(calls=0, failed=0, secs=0.0))
                p["calls"] += 1; p["failed"] += e.get("rc", 0) != 0; p["secs"] += e.get("secs") or 0
    except FileNotFoundError:
        pass
    done = [t for t in ts if t["status"] == "done"]
    total_reworks = sum(r["reworks"] for r in roles.values())
    try:
        eff = efficiency(ts, run_stats())
    except Exception as e:
        eff = dict(error=str(e)[:200])
    return dict(roles=roles, merged_per_day=last, providers=prov, efficiency=eff,
                totals=dict(done=len(done), blocked=sum(1 for t in ts if t["status"] == "blocked"), reworks=total_reworks,
                            first_pass_pct=round(100 * sum(r["first_pass"] for r in roles.values()) / len(done)) if done else None,
                            tokens_per_done=round(sum(tok.get(t["id"], 0) for t in done) / len(done)) if done else None))


def task_diff(task_id):
    if not re.fullmatch(r"\d{4}-[\w-]+", task_id or ""):
        return "(invalid task id)"
    p = os.path.join(ctx.FD, "diffs", task_id + ".diff")
    if os.path.exists(p):
        return open(p).read()
    t = jload(os.path.join(ctx.FD, "tasks", task_id + ".json"))
    if t and t.get("pr"):
        rc, out, _ = sh([os.path.join(ctx.BIN, "forgejo-api"), "GET", f"/repos/{sbx.FORGE_REPO}/pulls/{int(t['pr'])}.diff"], timeout=30)
        if rc == 0 and out.strip():
            return out[:400000]
    return "(no diff recorded for this task)"


def list_projects():
    """Every project on this install: the default one (etc/, runs/foreman/) and projects/<name>/. Read-only summary."""
    out = []
    names = ([""] if os.path.exists(os.path.join(ctx.ROOT, "etc", "foreman.conf")) or os.path.isdir(os.path.join(sbx.RUNS, "foreman")) else [])
    pd = os.path.join(ctx.ROOT, "projects")
    names += sorted(n for n in (os.listdir(pd) if os.path.isdir(pd) else []) if os.path.exists(os.path.join(pd, n, "project.conf")))
    for n in names:
        conf = sbx._kv(os.path.join(pd, n, "project.conf")) if n else {}
        fd = os.path.join(sbx.RUNS, "projects", n, "foreman") if n else os.path.join(sbx.RUNS, "foreman")
        live = jload(os.path.join(fd, "live.json"), {})
        st = {}
        tdir = os.path.join(fd, "tasks")
        for f in (os.listdir(tdir) if os.path.isdir(tdir) else []):
            if re.fullmatch(r"\d{4}-[\w-]+\.json", f):
                x = (jload(os.path.join(tdir, f)) or {}).get("status", "?")
                st[x] = st.get(x, 0) + 1
        out.append(dict(name=n or "default", repo=conf.get("FORGE_REPO", sbx._FORGE.get("FORGE_REPO", "sandbox/playground")) if n else sbx.FORGE_REPO,
                        workers=conf.get("WORKERS", "") or ("" if n else ctx.C["WORKERS"]), alive=live.get("at", 0) > time.time() - 120,
                        paused=os.path.exists(os.path.join(fd, "PAUSED")), tasks=st))
    return out


def dashboard_json():
    """Everything the host dashboard shows, in one read-only call (`bin/foreman status --dashboard`)."""
    pool = workers()                                   # first: it also learns each worker's runtime (WORKER_RT)
    st, ts, now = gstate(), tasks(), time.time()
    live = jload(os.path.join(ctx.FD, "live.json"), {})
    alive = live.get("at", 0) > now - 3 * ctx.C["LOOP_SEC"] - 30
    evs, llm_today = [], dict(calls=0, cost_usd=0.0, by={})
    try:
        evs = [json.loads(l) for l in open(os.path.join(ctx.FD, "events.jsonl")).read().splitlines()[-60:]]
    except Exception:
        pass
    try:
        for l in open(os.path.join(ctx.FD, "llm.jsonl")):
            e = json.loads(l)
            if e["at"][:10] == sbx.today():
                who = "claude" if e["who"] == "claude" else "antigravity-claude"
                llm_today["calls"] += 1
                llm_today["cost_usd"] += e.get("cost_usd") or 0
                llm_today["by"][who] = llm_today["by"].get(who, 0) + 1
    except Exception:
        pass
    led = [e for e in sbx.read_ledger() if e.get("date") == sbx.today() and not e.get("test")]
    lim = sbx.load_limits()
    task_out = []
    for t in ts:
        task_out.append(dict(id=t["id"], goal=t["goal"], title=t["title"], role=t["role"], risk=t.get("risk"), status=t["status"],
                             attempts=t["attempts"], max_attempts=ctx.C["MAX_REWORK"] + 1, worker=t.get("worker"), pr=t.get("pr"),
                             depends_on=t.get("depends_on", []), files=t.get("files", []), spec=t.get("spec", "")[:2500],
                             acceptance=t.get("acceptance", []), blocked_why=t.get("blocked_why", ""), feedback=t.get("feedback", "")[:1500],
                             history=t.get("history", [])[-6:], dispatched=t.get("dispatched"), finished=t.get("finished"),
                             updated=t.get("updated"), run_tokens=t.get("run_tokens"), diff=t.get("diff"), total_lines=t.get("total_lines"),
                             mutations=t.get("mutations", []), pr_url=t.get("pr_url") or (t.get("upstream_pr") or {}).get("url"),
                             runtime=WORKER_RT.get(t.get("worker") or "", "")))
    return dict(project=ctx.PROJECT or "default", repo=sbx.FORGE_REPO, pool=pool, now=now, alive=alive, paused=os.path.exists(os.path.join(ctx.FD, "PAUSED")), killswitch=sbx.killswitch_active(),
                autopilot=bool(ctx.C["AUTOPILOT"]) and not os.path.exists(os.path.join(ctx.FD, "AUTOPILOT_OFF")),
                claude_until=st.get("claude_until") if st.get("claude_until", 0) > now else None,
                agy_cooldown={w: u for w, u in st.get("agy_cooldown", {}).items() if u > now},
                live=live, backlog=jload(os.path.join(ctx.FD, "backlog.json"), []),
                owner_questions=jload(os.path.join(ctx.FD, "owner_questions.json"), []), roadmap_request=st.get("roadmap_request"),
                goals=[dict(id=g["id"], text=g["text"], status=g["status"], source=g.get("source", "owner"), summary=g.get("summary", ""),
                            notes=g.get("notes", ""), created=g.get("created"), finished=g.get("finished"), rounds=g.get("rounds", 0),
                            acceptance=g.get("acceptance", []), criteria=g.get("criteria", []), criteria_at=g.get("criteria_at"))
                       for g in goals()],
                forge=dict(kind=ctx.C["FORGE_KIND"] or "", repo=ctx.C["UPSTREAM_REPO"], url=ctx.C["UPSTREAM_URL"],
                           setup_needed=not ctx.C["FORGE_KIND"] and not goals()),
                maintenance=st.get("maint"), api=dict(spent_today=sbx.api_spent_today(),
                                                      budget=float(lim.get("API_DAILY_BUDGET_USD", 0)),
                                                      workers=sorted(w for w, r in WORKER_RT.items() if runtimes.is_api(r))),
                tasks=task_out, events=evs, llm_today=llm_today, timeline=timeline(), metrics=team_metrics(ts),
                today=dict(runs=len(led), runs_max=lim["MAX_RUNS_PER_DAY"], tokens=sum(e.get("tokens", 0) for e in led),
                           tokens_max=lim["DAILY_TOKEN_BUDGET"], ok=sum(1 for e in led if e.get("reason") == "ok"),
                           merged=sum(1 for t in ts if t["status"] == "done" and (t.get("finished") or "")[:10] == sbx.today())))


def status(as_json):
    st, ts = gstate(), tasks()
    evs = []
    try:
        evs = [json.loads(l) for l in open(os.path.join(ctx.FD, "events.jsonl")).read().splitlines()[-25:]]
    except Exception:
        pass
    out = dict(paused=os.path.exists(os.path.join(ctx.FD, "PAUSED")), killswitch=sbx.killswitch_active(),
               claude_until=fmt_ts(st["claude_until"]) if st.get("claude_until", 0) > time.time() else None,

               goals=[dict(id=g["id"], text=g["text"], status=g["status"], summary=g.get("summary", ""),
                           tasks=[dict(id=t["id"], title=t["title"], role=t["role"], status=t["status"], attempts=t["attempts"],
                                       worker=t.get("worker"), pr=t.get("pr"), blocked_why=t.get("blocked_why", ""))
                                  for t in ts if t["goal"] == g["id"]]) for g in goals()],
               events=evs)
    if as_json:
        print(json.dumps(out, ensure_ascii=False)); return
    print(f"foreman: {'PAUSED' if out['paused'] else 'kill switch ON' if out['killswitch'] else 'active'}"
          f"   orchestrator: {'Claude Code' if not out['claude_until'] else 'Antigravity-Claude (Claude Code back ' + out['claude_until'] + ')'}")
    for g in out["goals"]:
        print(f"\n[{g['status']}] {g['id']}: {g['text'][:100]}" + (f"\n    => {g['summary']}" if g["summary"] else ""))
        for t in g["tasks"]:
            print(f"    {t['status']:8} {t['id']}  ({t['role']}, attempt {t['attempts'] + 1}{', ' + t['worker'] if t['worker'] else ''})"
                  + (f"  PR #{t['pr']}" if t["pr"] else "") + (f"\n             ! {t['blocked_why'][:160]}" if t["blocked_why"] else ""))
    print("\nrecent:")
    for e in evs[-10:]:
        print(f"  {e['at']}  {e['msg'][:160]}")
