"""Read-only views for the dashboard and the CLI: timeline, run statistics, efficiency, team metrics, diffs, status."""
import datetime as dt, json, os, re, time

import sbx, runtimes

from . import ctx
from .util import approvals, fmt_ts, sh, slugify
from .store import goals, gstate, jload, jsave, tasks
from .pool import WORKER_RT, workers
from .stats import ab_verdict, efficiency, estimate, per_task, run_stats, sizes
from .repo import own_files
from .llm import orchestrator_label, providers_for


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
                            task=str(e.get("task", "")).removesuffix(".md"), run_id=e.get("run_id")))
    for d in sbx.run_dirs():
        if sbx.is_running(d):
            m = sbx.meta(d)
            if m.get("test") or m.get("project", "") != sbx.PROJECT:
                continue
            try:
                start = dt.datetime.fromisoformat(sbx.read(d + "/started").replace("Z", "+00:00")).timestamp()
            except Exception:
                start = now
            out.append(dict(worker=m.get("worker"), start=start, end=None, reason="running", tokens=runtimes.scan_file(m.get("runtime"), d + "/stream.jsonl")["tokens"],
                            task=str(m.get("task", "")).removesuffix(".md"), run_id=os.path.basename(d)))
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
    kinds, flags = {}, {}          # why work came back (merge.rework kind) and which measured gate flags were set then; old entries: "unknown"
    for t in ts:
        if t["status"] == "cancelled":
            continue
        for h in t.get("history", []):
            if h.get("outcome") == "rework":
                kinds[h.get("kind") or "unknown"] = kinds.get(h.get("kind") or "unknown", 0) + 1
                for f in h.get("flags") or []:
                    flags[f] = flags.get(f, 0) + 1
    try:
        rs = run_stats()
        eff = efficiency(ts, rs)
        eff["verdicts"] = ab_verdicts(eff)
        weekly = trends(ts, rs, tok)
    except Exception as e:
        eff, weekly = dict(error=str(e)[:200]), []
    return dict(roles=roles, merged_per_day=last, providers=prov, efficiency=eff, weekly=weekly,
                totals=dict(done=len(done), blocked=sum(1 for t in ts if t["status"] == "blocked"), reworks=total_reworks,
                            rework_kinds=kinds, rework_flags=flags,
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


def is_orphan_warning(t, orphan_warn=None, orphan_limit=None):
    """True when a task has orphan_streak between ORPHAN_WARN and ORPHAN_LIMIT and is not yet blocked."""
    if orphan_warn is None:
        orphan_warn = int(ctx.C.get("ORPHAN_WARN", 3))
    if orphan_limit is None:
        orphan_limit = int(ctx.C.get("ORPHAN_LIMIT", 5))
    streak = int((t or {}).get("orphan_streak") or 0)
    return bool((t or {}).get("status") != "blocked" and 0 < streak < orphan_limit and streak >= orphan_warn)


def dashboard_json():
    """Everything the host dashboard shows, in one read-only call (`bin/foreman status --dashboard`)."""
    pool = workers()                                   # first: it also learns each worker's runtime (WORKER_RT)
    st, ts, now = gstate(), tasks(), time.time()
    live = jload(os.path.join(ctx.FD, "live.json"), {})
    try:
        beat = os.path.getmtime(os.path.join(ctx.FD, "heartbeat"))
    except OSError:
        beat = 0
    alive = max(live.get("at", 0), beat) > now - 3 * ctx.C["LOOP_SEC"] - 30
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
    orphan_warn = int(ctx.C.get("ORPHAN_WARN", 3))
    orphan_limit = int(ctx.C.get("ORPHAN_LIMIT", 5))
    task_out = []
    try:
        sz = sizes(ts, run_stats(), own_files)
    except Exception:
        sz = None
    for t in ts:
        open_ = t["status"] not in ("done", "cancelled")
        streak = int(t.get("orphan_streak") or 0)
        warn = is_orphan_warning(t, orphan_warn, orphan_limit)
        x = dict(id=t["id"], goal=t["goal"], title=t["title"], role=t["role"], risk=t.get("risk"), status=t["status"],
                 attempts=t["attempts"], max_attempts=ctx.C["MAX_REWORK"] + 1, worker=t.get("worker"), pr=t.get("pr"),
                 depends_on=t.get("depends_on", []), files=t.get("files", []), dispatched=t.get("dispatched"), finished=t.get("finished"),
                 updated=t.get("updated"), run_tokens=t.get("run_tokens"), diff=t.get("diff"), total_lines=t.get("total_lines"),
                 pr_url=t.get("pr_url") or (t.get("upstream_pr") or {}).get("url"), runtime=WORKER_RT.get(t.get("worker") or "", ""),
                 awaiting=t.get("awaiting"), pin_worker=t.get("pin_worker"), model_override=t.get("model_override"),
                 cancel_requested=t.get("cancel_requested"), created=t.get("created"),
                 orphan_streak=streak, orphan_warning=warn, orphan_limit=orphan_limit)
        if open_:                                     # the full spec and history of closed tasks: GET /api/task (task_detail)
            x.update(spec=t.get("spec", "")[:2500], acceptance=t.get("acceptance", []), blocked_why=t.get("blocked_why", ""),
                     feedback=t.get("feedback", "")[:1500], history=t.get("history", [])[-6:], mutations=t.get("mutations", []),
                     merge_feedback=t.get("merge_feedback", "")[:800] if t.get("awaiting") == "merge" else "",
                     diagnosis=t.get("diagnosis"))
            est = estimate(t, sz, own_files)
            if est:
                x["estimate"] = est
        task_out.append(x)
    gs = goals()
    backlog = jload(os.path.join(ctx.FD, "backlog.json"), [])
    return dict(project=ctx.PROJECT or "default", repo=sbx.FORGE_REPO, pool=pool, now=now, alive=alive, paused=os.path.exists(os.path.join(ctx.FD, "PAUSED")), killswitch=sbx.killswitch_active(),
                owner_lang=ctx.C.get("OWNER_LANG", "English"),
                autopilot=bool(ctx.C["AUTOPILOT"]) and not os.path.exists(os.path.join(ctx.FD, "AUTOPILOT_OFF")),
                claude_until=st.get("claude_until") if st.get("claude_until", 0) > now else None,
                orchestrator=orchestrator_label()[1], orchestrator_provider=orchestrator_label()[0],
                agy_cooldown={w: u for w, u in st.get("agy_cooldown", {}).items() if u > now},
                live=live, backlog=backlog, forecast=forecast(ts, len(backlog)), onboarding=onboarding(pool, gs),
                approve=sorted(approvals()), routing=str(ctx.C.get("MODEL_ROUTING", "off")),
                owner_questions=jload(os.path.join(ctx.FD, "owner_questions.json"), []), roadmap_request=st.get("roadmap_request"),
                goals=[dict(id=g["id"], text=g["text"], status=g["status"], source=g.get("source", "owner"), summary=g.get("summary", ""),
                            notes=g.get("notes", ""), created=g.get("created"), finished=g.get("finished"), rounds=g.get("rounds", 0),
                            acceptance=g.get("acceptance", []), criteria=g.get("criteria", []), criteria_at=g.get("criteria_at"),
                            paused=bool(g.get("paused")), priority=int(g.get("priority") or 0), owner_notes=g.get("owner_notes", ""))
                       for g in gs],
                forge=dict(kind=ctx.C["FORGE_KIND"] or "", repo=ctx.C["UPSTREAM_REPO"], url=ctx.C["UPSTREAM_URL"],
                           setup_needed=not ctx.C["FORGE_KIND"] and not gs),
                maintenance=st.get("maint"), api=dict(spent_today=sbx.api_spent_today(), spent_month=sbx.api_spent(period="month"),
                                                      budget=float(lim.get("API_DAILY_BUDGET_USD", 0)),
                                                      budget_month=float(lim.get("API_MONTHLY_BUDGET_USD") or 0),
                                                      workers=sorted(w for w, r in WORKER_RT.items() if runtimes.is_api(r)),
                                                      runtimes={w: r for w, r in WORKER_RT.items()}),
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
               orchestrator=orchestrator_label()[1],

               goals=[dict(id=g["id"], text=g["text"], status=g["status"], summary=g.get("summary", ""),
                           tasks=[dict(id=t["id"], title=t.get("title") or t.get("text", ""), role=t.get("role", ""), status=t.get("status", "pending"), attempts=t.get("attempts", 0),
                                       worker=t.get("worker"), pr=t.get("pr"), blocked_why=t.get("blocked_why", ""))
                                  for t in ts if t.get("goal") == g["id"]]) for g in goals()],
               events=evs)
    if as_json:
        print(json.dumps(out, ensure_ascii=False)); return
    print(f"foreman: {'PAUSED' if out['paused'] else 'kill switch ON' if out['killswitch'] else 'active'}"
          f"   orchestrator: {out['orchestrator']}" + (f" (Claude Code back {out['claude_until']})" if out['claude_until'] and 'claude-cli' in (providers_for('plan') + providers_for('review')) else ""))
    for g in out["goals"]:
        print(f"\n[{g['status']}] {g['id']}: {g['text'][:100]}" + (f"\n    => {g['summary']}" if g["summary"] else ""))
        for t in g["tasks"]:
            print(f"    {t['status']:8} {t['id']}  ({t['role']}, attempt {t['attempts'] + 1}{', ' + t['worker'] if t['worker'] else ''})"
                  + (f"  PR #{t['pr']}" if t["pr"] else "") + (f"\n             ! {t['blocked_why'][:160]}" if t["blocked_why"] else ""))
    print("\nrecent:")
    for e in evs[-10:]:
        print(f"  {e['at']}  {e['msg'][:160]}")


# ---------------------------------------------------------------- views for the dashboard (each one read-only)
def ab_verdicts(eff):
    """The running experiments, judged on 95 % intervals: AB_MODEL against the usual worker model, context pack on / off."""
    out = []
    try:
        default = ctx.C["WORKER_MODEL"] or str(sbx.load_limits().get("WORKER_MODEL") or "")
    except SystemExit:
        default = ctx.C["WORKER_MODEL"]
    m = eff.get("model") or {}
    if ctx.C["AB_MODEL"] and (m.get(ctx.C["AB_MODEL"]) or m.get(default)):
        out.append(dict(kind="model", a=default, b=ctx.C["AB_MODEL"], **ab_verdict(m.get(default), m.get(ctx.C["AB_MODEL"]), default, ctx.C["AB_MODEL"])))
    p = eff.get("pack") or {}
    if p.get("pack") and p.get("no pack"):
        out.append(dict(kind="pack", a="no pack", b="pack", **ab_verdict(p["no pack"], p["pack"], "no pack", "pack")))
    return out


def trends(ts, rs, tok=None, weeks=8):
    """Per week (by the day a task was merged): merged tasks, % approved first time, tokens / USD / quota per merged task."""
    pt = per_task(ts, rs)
    out = {}
    for t in ts:
        if t["status"] != "done" or not t.get("finished"):
            continue
        try:
            d = dt.datetime.fromisoformat(t["finished"].replace("Z", "+00:00"))
        except ValueError:
            continue
        y, w, _ = d.isocalendar()
        b = out.setdefault(f"{y}-W{w:02d}", dict(done=0, fp=0, tokens=0, cost=0.0, week=0.0, week_n=0))
        e = pt.get(t["id"], {})
        b["done"] += 1
        b["fp"] += not any(h.get("outcome") == "rework" for h in t.get("history", []))
        b["tokens"] += (tok or {}).get(t["id"], e.get("tokens", 0))
        b["cost"] += e.get("cost", 0) or 0
        if e.get("week_n"):
            b["week"] += e["week"]; b["week_n"] += 1
    return [dict(week=k, done=v["done"], first_pass_pct=round(100 * v["fp"] / v["done"]), tokens_per_done=round(v["tokens"] / v["done"]),
                 cost_per_done=round(v["cost"] / v["done"], 3) if v["cost"] else None,
                 week_per_done=round(v["week"] / v["week_n"], 2) if v["week_n"] else None)
            for k, v in sorted(out.items())[-weeks:]]


OPEN = ("pending", "running", "gate", "review", "splitting", "merging", "approval", "merge-ready")


def forecast(ts, backlog_n, now=None):
    """When the open work is done at the recent pace: merged tasks per day over the last 7 days, the open tasks, plus the
    backlog's goals at this project's average number of tasks per goal. Pure (selftested)."""
    now = now or time.time()
    cutoff = dt.datetime.fromtimestamp(now - 7 * 86400, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    done = [t for t in ts if t["status"] == "done"]
    recent = [t for t in done if (t.get("finished") or "") >= cutoff]
    per_day = len(recent) / 7
    open_n = sum(1 for t in ts if t["status"] in OPEN)
    goal_ids = {t["goal"] for t in done}
    per_goal = len(done) / len(goal_ids) if goal_ids else 3.0
    remaining = open_n + backlog_n * per_goal
    eta = remaining / per_day if per_day and remaining else None
    return dict(per_day=round(per_day, 1), open=open_n, backlog=backlog_n, per_goal=round(per_goal, 1), remaining=round(remaining),
                eta_days=round(eta, 1) if eta is not None else None, eta_at=now + eta * 86400 if eta is not None else None)


def onboarding(pool, gs):
    """The first steps of a new install, done or not - the dashboard shows them until all are done (or dismissed)."""
    return [dict(key="forge", title="Choose where the code lives", done=bool(ctx.C["FORGE_KIND"])),
            dict(key="workers", title="Add a worker", done=bool(pool)),
            dict(key="notify", title="Get notified (ntfy, Slack, Discord)", done=bool(ctx.C["NOTIFY_URL"])),
            dict(key="goal", title="Give the team its first goal", done=bool(gs))]


def _ledger_runs(task_ids):
    """This project's finished runs of the given tasks, newest first, with what run_stats knows (model, quota, variant)."""
    rs = run_stats()
    out = []
    for e in sbx.read_ledger():
        tid = str(e.get("task", "")).removesuffix(".md")
        if e.get("test") or e.get("project", "") != sbx.PROJECT or tid not in task_ids:
            continue
        r = rs.get(e.get("run_id"), {})
        out.append(dict(run_id=e.get("run_id"), task=tid, at=e.get("at"), worker=e.get("worker"), runtime=e.get("runtime") or "agy",
                        model=r.get("model"), variant=r.get("variant"), reason=e.get("reason"), tokens=e.get("tokens") or 0,
                        tools=e.get("tool_calls") or 0, duration_s=e.get("duration_s") or 0, cost_usd=e.get("cost_usd"), week=r.get("week")))
    return sorted(out, key=lambda x: x["at"] or "", reverse=True)


def _llm_calls(subjects):
    out = dict(calls=0, secs=0.0, cost_usd=0.0, kinds={})
    try:
        for l in open(os.path.join(ctx.FD, "llm.jsonl")):
            e = json.loads(l)
            if e.get("subject") in subjects:
                out["calls"] += 1; out["secs"] += e.get("secs") or 0; out["cost_usd"] += e.get("cost_usd") or 0
                out["kinds"][e.get("kind", "?")] = out["kinds"].get(e.get("kind", "?"), 0) + 1
    except (FileNotFoundError, ValueError):
        pass
    out["secs"] = round(out["secs"]); out["cost_usd"] = round(out["cost_usd"], 4)
    return out


def _totals(runs):
    return dict(runs=len(runs), tokens=sum(r["tokens"] for r in runs), cost_usd=round(sum(r["cost_usd"] or 0 for r in runs), 4),
                week=round(sum(r["week"] or 0 for r in runs), 2), duration_s=sum(r["duration_s"] for r in runs))


def task_detail(tid):
    """Everything about one task for its sheet in the dashboard: the task, each run (worker, model, tokens, money / quota),
    the orchestrator's calls about it, and the estimate it had."""
    t = jload(os.path.join(ctx.FD, "tasks", tid + ".json"))
    if not isinstance(t, dict):
        return None
    runs = _ledger_runs({tid})
    sz = None
    try:
        sz = sizes(tasks(), run_stats(), own_files)
    except Exception:
        pass
    keep = ("id", "goal", "title", "role", "risk", "status", "attempts", "worker", "pr", "pr_url", "depends_on", "files", "spec", "acceptance",
            "blocked_why", "feedback", "history", "dispatched", "finished", "created", "diff", "total_lines", "mutations", "awaiting",
            "pin_worker", "model_override", "owner_notes", "cancel_requested", "merge_reviewer", "merge_feedback", "variant", "branch",
            "split_from", "planned_by", "cancelled_why", "orphan_streak", "orphan_warning", "diagnosis")
    orphan_warn = int(ctx.C.get("ORPHAN_WARN", 3))
    orphan_limit = int(ctx.C.get("ORPHAN_LIMIT", 5))
    streak = int(t.get("orphan_streak") or 0)
    warn = is_orphan_warning(t, orphan_warn, orphan_limit)
    t["orphan_streak"] = streak
    t["orphan_warning"] = warn
    x = {k: t.get(k) for k in keep if k in t}
    x["orphan_streak"] = streak
    x["orphan_warning"] = warn
    x["orphan_limit"] = orphan_limit
    x["max_attempts"] = ctx.C["MAX_REWORK"] + 1
    x["upstream_pr"] = t.get("upstream_pr")
    x["gate_tail"] = (t.get("gate_tail") or "")[-3000:]
    return dict(task=x, runs=runs, totals=_totals(runs), orchestrator=_llm_calls({tid}), estimate=estimate(t, sz, own_files))


def goal_detail(gid):
    """One goal on one page: its tasks, every event of it and its tasks in order, what it cost, and its criteria."""
    g = jload(os.path.join(ctx.FD, "goals", gid + ".json"))
    if not isinstance(g, dict):
        return None
    mine = [t for t in tasks() if t["goal"] == gid]
    ids = {t["id"] for t in mine}
    evs = []
    try:
        for l in open(os.path.join(ctx.FD, "events.jsonl")):
            if gid not in l and not any(i in l for i in ids):
                continue
            e = json.loads(l)
            msg = e.get("msg", "")
            if e.get("goal") == gid or e.get("task") in ids or gid in msg or set(e.get("tasks") or []) & ids or any(i in msg for i in ids):
                evs.append(e)
    except (FileNotFoundError, ValueError):
        pass
    runs = _ledger_runs(ids)
    end = g.get("finished") or sbx.now()
    try:
        wall = (dt.datetime.fromisoformat(end.replace("Z", "+00:00")) - dt.datetime.fromisoformat(str(g.get("created")).replace("Z", "+00:00"))).total_seconds()
    except (TypeError, ValueError):
        wall = None
    return dict(goal=g, tasks=[dict(id=t["id"], title=t["title"], role=t["role"], status=t["status"], attempts=t["attempts"],
                                    worker=t.get("worker"), pr=t.get("pr"), pr_url=t.get("pr_url"), diff=t.get("diff"),
                                    depends_on=t.get("depends_on", []), finished=t.get("finished"), dispatched=t.get("dispatched"),
                                    reworks=sum(1 for h in t.get("history", []) if h.get("outcome") == "rework")) for t in mine],
                events=evs[-400:], runs=runs, totals=dict(_totals(runs), wall_s=wall), orchestrator=_llm_calls(ids | {gid}))


def search(q, limit=80):
    """Goals, tasks, events and worker transcripts of this project that contain `q` (case-insensitive), newest first."""
    ql = " ".join(str(q).split()).lower()[:200]
    if len(ql) < 2:
        return []
    snip = lambda text: (lambda i: ("…" if i > 40 else "") + text[max(0, i - 40): i + 110].replace("\n", " ") + ("…" if i + 110 < len(text) else ""))(text.lower().find(ql))
    out = []
    for g in reversed(goals()):
        blob = " ".join(str(g.get(k) or "") for k in ("text", "summary", "notes", "owner_notes"))
        if ql in blob.lower() and sum(1 for x in out if x["kind"] == "goal") < 15:
            out.append(dict(kind="goal", id=g["id"], title=g["text"][:140], status=g["status"], snippet=snip(blob)))
    for t in reversed(tasks()):
        blob = " ".join([t.get("title", ""), t.get("spec", ""), t.get("feedback", "") or "", t.get("blocked_why", "") or "", t["id"]] +
                        [str(h.get("feedback") or h.get("why") or "") for h in t.get("history", [])])
        if ql in blob.lower():
            out.append(dict(kind="task", id=t["id"], title=t["title"], status=t["status"], snippet=snip(blob)))
            if sum(1 for x in out if x["kind"] == "task") >= 30:
                break
    n = 0
    try:
        for l in reversed(open(os.path.join(ctx.FD, "events.jsonl")).read().splitlines()):
            if ql in l.lower():
                e = json.loads(l)
                if ql in e.get("msg", "").lower():
                    out.append(dict(kind="event", at=e.get("at"), task=e.get("task"), goal=e.get("goal"), snippet=snip(e["msg"])))
                    n += 1
                    if n >= 20:
                        break
    except (FileNotFoundError, ValueError):
        pass
    runs = [d for d in reversed(sbx.run_dirs()) if sbx.meta(d).get("project", "") == sbx.PROJECT][:300]
    for d in runs:
        for f in ("stream.jsonl", "stream.jsonl.gz"):
            p = os.path.join(d, f)
            if not os.path.exists(p):
                continue
            rc, line, _ = sh(["zgrep" if f.endswith(".gz") else "grep", "-i", "-F", "-m", "1", "--", ql, p], timeout=10)
            if rc == 0 and line:
                m = sbx.meta(d)
                out.append(dict(kind="run", run_id=os.path.basename(d), task=str(m.get("task", "")).removesuffix(".md"),
                                worker=m.get("worker"), at=m.get("finished") or m.get("dispatched"), snippet=snip(line[:4000])))
        if sum(1 for x in out if x["kind"] == "run") >= 15:
            break
    return out[:limit]


def money(days=14):
    """Money and quota: USD per day by runtime (all projects - the budgets are per install), by project and by worker model
    this month, the caps, when today's budget runs out at the pace of the last 3 hours, and each Antigravity worker's weekly
    quota burn (% per day over the last 3 days)."""
    led = [e for e in sbx.read_ledger() if not e.get("test")]
    try:
        lim = sbx.load_limits()
    except SystemExit:
        lim = dict(sbx.DEFAULTS)
    today = sbx.today()
    day0 = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days - 1)).strftime("%Y-%m-%d")
    per_day = {}
    for e in led:
        if e.get("cost_usd") and e.get("date", "") >= day0:
            d = per_day.setdefault(e["date"], {})
            d[e.get("runtime") or "agy"] = round(d.get(e.get("runtime") or "agy", 0) + e["cost_usd"], 4)
    month = [e for e in led if str(e.get("date", "")).startswith(today[:7]) and e.get("cost_usd")]
    by = lambda key: {k: round(sum(e["cost_usd"] for e in month if (e.get(key) or "") == k), 4) for k in {e.get(key) or "" for e in month}}
    rs = run_stats()
    by_model = {}
    for e in month:
        r = rs.get(e.get("run_id"))
        if r:
            by_model[r["model"]] = round(by_model.get(r["model"], 0) + e["cost_usd"], 4)
    now = time.time()
    recent = [e for e in led if e.get("cost_usd") and e.get("at") and _ts(e["at"]) and now - _ts(e["at"]) < 3 * 3600]
    rate = sum(e["cost_usd"] for e in recent) / 3                     # USD per hour
    spent = sbx.api_spent(led)
    budget = float(lim.get("API_DAILY_BUDGET_USD") or 0)
    runs_out = now + (budget - spent) / rate * 3600 if rate > 0 and budget > spent else None
    end_of_day = dt.datetime.now(dt.timezone.utc).replace(hour=23, minute=59, second=59).timestamp()
    burn = {}
    for e in led:
        r = rs.get(e.get("run_id"))
        if r and r.get("week") is not None and e.get("at") and _ts(e["at"]) and now - _ts(e["at"]) < 3 * 86400:
            burn[e.get("worker")] = burn.get(e.get("worker"), 0) + r["week"] / 3
    week0 = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=6)).strftime("%Y-%m-%d")
    conf = sbx.project_conf()
    seen_runtimes = set()
    for r in rs.values():
        if isinstance(r, dict):
            seen_runtimes.add(r.get("runtime") or "agy")
    cost_models = {}
    for rt in sorted(seen_runtimes):
        try:
            cost_models[rt] = runtimes.cost_model(rt)
        except ValueError:
            cost_models[rt] = dict(kind="unknown", detail=f"unknown runtime {rt}")
    return dict(per_day=[dict(date=d, **per_day.get(d, {})) for d in
                         ((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1))],
                today=round(spent, 4), week=round(sum(e["cost_usd"] for e in led if e.get("cost_usd") and e.get("date", "") >= week0), 4),
                month=round(sum(e["cost_usd"] for e in month), 4), by_runtime=by("runtime"), by_project=by("project"), by_model=by_model,
                rate_per_hour=round(rate, 4), runs_out_at=runs_out if runs_out and runs_out < end_of_day else None,
                caps=dict(run=float(lim.get("API_RUN_BUDGET_USD") or 0), day=budget, month=float(lim.get("API_MONTHLY_BUDGET_USD") or 0),
                          project_day=float(conf.get("PROJECT_DAILY_BUDGET_USD") or 0), project_month=float(conf.get("PROJECT_MONTHLY_BUDGET_USD") or 0),
                          workers=sbx.worker_budgets(lim.get("WORKER_DAILY_BUDGET_USD"))),
                project_today=sbx.api_spent(led, "day", sbx.PROJECT), project_month=sbx.api_spent(led, "month", sbx.PROJECT),
                workers_today={w: sbx.api_spent(led, "day", worker=w) for w in sbx.worker_budgets(lim.get("WORKER_DAILY_BUDGET_USD"))},
                quota_burn={w: round(v, 2) for w, v in burn.items() if w},
                top_tasks=sorted(({"task": k, "usd": v} for k, v in _task_costs(month).items()), key=lambda x: -x["usd"])[:10],
                cost_models=cost_models)


def _ts(iso):
    try:
        return dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _task_costs(entries):
    out = {}
    for e in entries:
        if e.get("project", "") == sbx.PROJECT:
            k = str(e.get("task", "")).removesuffix(".md")
            out[k] = round(out.get(k, 0) + (e.get("cost_usd") or 0), 4)
    return out
