"""The deterministic loop: every tick it applies finished steps, advances tasks and goals, dispatches ready tasks to
free workers and keeps the idle ones fed. No LLM decides when anything happens."""
import json, os, re, signal, sys, threading, time, traceback

import sbx, runtimes

from . import ctx
from .util import _iso_ts, fmt_ts, parse_limit, ready_tasks, sh
from .store import event, goals, gstate, jload, jsave, locked, new_goal_id, notify, save_goal, save_task, state_tx, tasks
from .jobs import INFLIGHT, PHASE, POOL, SINCE, submit
from .pool import (COOLDOWN, LAST_DISPATCHED, LAST_QUOTA_MSG, QUOTA_CACHE, RESERVED, api_budget_left, order_by_quota, pool_quota_out,
                   quota_pct, quota_rows, runtime_of, worker_busy, workers)

DISPATCHING = set()           # task ids currently being dispatched
DISPATCHING_FILES = set()     # files currently claimed by in-flight dispatches
DISPATCHING_WORKERS = set()   # workers currently being dispatched to
DISPATCH_LOCK = threading.RLock()
RUN_DIR_RE = re.compile(r"^\d{8}T\d{6}Z-(.+)$")
from .repo import variant
from .llm import Unavailable, llm_available
from .prompts import worker_spec
from .planning import step_next, step_plan, step_roadmap, step_split
from .merge import maybe_upstream_sync, step_gate_review_merge, step_owner_merge, step_upstream_merge
from .maintenance import maybe_maintenance


def write_live():
    st = gstate()
    try:
        idle = explain_idle()
    except Exception as e:                          # a status line must never stop the loop
        idle = {"_": f"(could not explain: {e})"}
    jsave(os.path.join(ctx.FD, "live.json"), dict(
        at=time.time(), steps=[dict(key=k, since=SINCE.get(k), phase=PHASE.get(k.split(":", 1)[1], "")) for k in INFLIGHT],
        reserved=sorted(RESERVED), cooldown={w: dict(until=u, reason=r) for w, (u, r) in COOLDOWN.items() if u > time.time()},
        unavailable=st.get("unavail_msg") if not llm_available() else None, idle=idle))


def why_no_task(ts, gm, stopped="", running_n=0, max_parallel=99, planning=0, backlog=0, autopilot=True, worker=None):
    """One sentence: why an idle worker has nothing to do right now. Pure (selftested); the first blocker wins."""
    if stopped:
        return stopped
    if running_n >= max_parallel:
        return f"{running_n} runs are already running - the limit (MAX_PARALLEL) is {max_parallel}"
    gm_norm = {}
    for gid, g in gm.items():
        g_copy = dict(g)
        if g_copy.get("state") == "paused" or g_copy.get("status") == "paused" or g_copy.get("paused"):
            g_copy["paused"] = True
        gm_norm[gid] = g_copy
    running_files = {f for t in ts if t["status"] == "running" for f in t.get("files", [])}
    ready = ready_tasks(ts, running_files, gm_norm)
    mine = [t for t in ready if not t.get("pin_worker") or t.get("pin_worker") == worker]
    if mine:
        return f"{mine[0]['id']} is ready - it starts on the next round"
    if ready:
        return f"the ready task {ready[0]['id']} is assigned to {ready[0]['pin_worker']}"
    waiting = [t for t in ts if t["status"] == "pending"]
    if waiting:
        t = waiting[0]
        more = f" ({len(waiting)} tasks wait)" if len(waiting) > 1 else ""
        gid = t.get("goal") or t.get("goal_id")
        g = gm_norm.get(gid) or {}
        if g.get("paused"):
            return f"{t['id']} waits: its goal is paused{more}"
        done = {x["id"] for x in ts if x["status"] == "done"}
        deps = [d for d in t.get("depends_on", []) if d not in done]
        if deps:
            return f"{t['id']} waits for {', '.join(deps[:2])} to be merged first{more}"
        busy = [x["id"] for x in ts if x["status"] == "running" and set(x.get("files", [])) & set(t.get("files", []))]
        if busy:
            return f"{t['id']} waits: {busy[0]} is changing the same files{more}"
        if t.get("not_before", 0) > time.time():
            return f"{t['id']} is retried at {fmt_ts(t['not_before'])}{more}"
        return f"{t['id']} waits{more}"
    appr = [t for t in ts if t["status"] == "approval"] + [g for g in gm.values() if g.get("status") == "approval"]
    if appr:
        return f"{len(appr)} item(s) wait for your approval"
    if any(t["status"] in ("gate", "review", "merging", "merge-ready") for t in ts):
        return "the finished work is being tested, reviewed or merged"
    if planning:
        return "the planner is preparing the next tasks"
    if backlog:
        return "the next backlog goal starts on the next round"
    return "nothing to do - add a goal" + ("" if autopilot else " or turn on the autopilot")


def explain_idle():
    """worker -> why it is idle (only idle workers of this project's pool)."""
    ts, gs, now = tasks(), goals(), time.time()
    gm = {g["id"]: g for g in gs}
    try:
        lim = sbx.load_limits()
    except SystemExit:
        lim = {"MAX_PARALLEL": 99}
    ks = sbx.killswitch_active()
    stopped = ("the project is stopped (kill switch): " + str(ks.get("reason", ""))[:100]) if ks else \
        ("the foreman is paused" if os.path.exists(os.path.join(ctx.FD, "PAUSED")) else "")
    running_n = len([d for d in sbx.run_dirs() if sbx.is_running(d)])
    planning = sum(1 for g in gs if g["status"] == "new") + sum(1 for k in INFLIGHT if k.startswith(("goal:", "roadmap", "next:")))
    backlog = len(jload(os.path.join(ctx.FD, "backlog.json"), []))
    autopilot = bool(ctx.C["AUTOPILOT"]) and not os.path.exists(os.path.join(ctx.FD, "AUTOPILOT_OFF"))
    busy = {t.get("worker") for t in ts if t["status"] == "running"}
    out = {}
    for w in workers():
        if w in busy or worker_busy(w):
            continue
        if w in RESERVED:
            out[w] = "lent to the orchestrator for a planning or review call"
            continue
        cd = COOLDOWN.get(w)
        if cd and cd[0] > now:
            out[w] = f"paused until {fmt_ts(cd[0])}: {cd[1]}"
            continue
        out[w] = why_no_task(ts, gm, stopped, running_n, int(lim["MAX_PARALLEL"]), planning, backlog, autopilot, worker=w)
    return out


def collect():
    for key, fut in list(INFLIGHT.items()):
        if not fut.done():
            continue
        del INFLIGHT[key]
        SINCE.pop(key, None)
        PHASE.pop(key.split(":", 1)[1], None)
        try:
            fut.result()()
        except Unavailable as e:
            with state_tx() as st:
                changed = st.get("unavail_msg") != str(e)
                st["unavail_msg"] = str(e)
            if changed:
                event(f"waiting: {e}")
        except Exception:
            event(f"{key}: internal error, will retry: {traceback.format_exc()[-600:]}")


def dispatch_ready():
    with DISPATCH_LOCK:
        dispatched = []
        ts = tasks()
        running = [t for t in ts if t["status"] == "running"]
        busy_files = set(f for t in running for f in t.get("files", [])) | set(DISPATCHING_FILES)
        lim = sbx.load_limits()
        free = [w for w in workers() if not worker_busy(w) and w not in RESERVED and w not in DISPATCHING_WORKERS and COOLDOWN.get(w, (0, ""))[0] <= time.time()]
        if len([d for d in sbx.run_dirs() if sbx.is_running(d)]) >= lim["MAX_PARALLEL"]:
            return dispatched
        # Each worker is its own account with its own quota: give work to the one with the most remaining worker-model quota, so
        # all accounts drain evenly (overnight 2026-10-04 "least recently dispatched" still left worker-4 with zero runs).
        fam = str(lim.get("QUOTA_MODEL_FILTER", "gemini"))
        # API-key workers cost money per token: by default they come last ("overflow" - only when no subscription worker can take
        # the task); API_WORKERS_POLICY=always puts them first
        api_rank = 1000 if ctx.C["API_WORKERS_POLICY"] == "always" else -5
        pct = lambda w: api_rank if runtimes.is_api(runtime_of(w)) else quota_pct(quota_rows(w, 120), fam)
        by_quota = lambda ws: order_by_quota(ws, pct, LAST_DISPATCHED)
        free = by_quota(free)
        shared = {f.strip() for f in str(ctx.C.get("SHARED_FILES", "")).split(",") if f.strip()}
        claimed_ids = {t["id"] for t in running} | set(DISPATCHING)
        claimed_keys = {t.get("key") for t in running if t.get("key")} | {t.get("key") for t in ts if t["id"] in DISPATCHING and t.get("key")}
        gm = {}
        for g in goals():
            g_copy = dict(g)
            if g_copy.get("state") == "paused" or g_copy.get("status") == "paused" or g_copy.get("paused"):
                g_copy["paused"] = True
            gm[g["id"]] = g_copy
        for t in ready_tasks(ts, busy_files, gm):
            if not free:
                return dispatched
            t_key = t.get("key")
            gid = t.get("goal") or t.get("goal_id")
            g = gm.get(gid) or {}
            if g.get("paused") or g.get("state") == "paused" or g.get("status") == "paused":
                continue
            # Invariant: do not dispatch tasks with overlapping files, duplicate keys, or already-claimed runs
            if t["id"] in claimed_ids or t["id"] in DISPATCHING or (t_key and t_key in claimed_keys):
                continue
            t_files = set(t.get("files", [])) - shared
            if t_files & (busy_files | DISPATCHING_FILES):
                continue
            fresh = jload(os.path.join(ctx.FD, "tasks", t["id"] + ".json")) or {}
            if fresh.get("status") != "pending" or fresh.get("worker"):
                continue
            if t.get("pin_worker"):                     # the owner assigned this task to one worker: wait for that one
                if t["pin_worker"] not in free:
                    continue
                w = t["pin_worker"]
            else:
                prev = (t.get("last_run") or {}).get("worker")
                w = prev if t.get("feedback") and prev in free and ctx.C["CONTINUE_CONVERSATIONS"] else free[0]
            rt = runtime_of(w)
            if runtimes.is_api(rt):
                rows = []
                ok, msg = sbx.budget_check(w, lim, conf=sbx.project_conf())
                msg = msg and "quota low: " + msg
            else:
                rows = quota_rows(w, 60)
                ok, msg = sbx.quota_verdict(rows, lim)
            if not ok and runtimes.is_api(rt):
                COOLDOWN[w] = (time.time() + 3600, msg)
                free.remove(w)
                if LAST_QUOTA_MSG.get(w) != msg:
                    LAST_QUOTA_MSG[w] = msg
                    event(f"{w}: {msg} - checking again every 60 min")
                continue
            if not ok:
                cd_sec = 180
                resets = []
                for r in rows:
                    if str(lim.get("QUOTA_MODEL_FILTER", "")).lower() in r.get("model", "").lower():
                        rst = parse_limit(str(r.get("reset", ""))) or _iso_ts(r.get("reset"))
                        if rst and rst > time.time():
                            resets.append(rst)
                soonest = min(resets) if resets else None
                if soonest and soonest - time.time() <= 600:
                    cd_sec = max(30, int(soonest - time.time()) + 15)
                elif soonest and soonest - time.time() > 3600:
                    cd_sec = 1800                       # a weekly limit days away: look again every 30 min, not every 3
                COOLDOWN[w] = (time.time() + cd_sec, msg)
                free.remove(w)
                if LAST_QUOTA_MSG.get(w) != msg:        # say it once per change, not every few minutes for days
                    LAST_QUOTA_MSG[w] = msg
                    event(f"{w}: {msg} - checking quota again every {cd_sec // 60 or 1} min")
                continue
            LAST_QUOTA_MSG.pop(w, None)
            lr = t.get("last_run") or {}
            continuing = bool(ctx.C["CONTINUE_CONVERSATIONS"] and t.get("feedback") and lr.get("conversation") and lr.get("reason") == "ok"
                              and lr.get("worker") == w and lr.get("tokens", 0) < 0.4 * lim["RUN_TOKEN_CAP"])
            spec = os.path.join(ctx.FD, "tasks", t["id"] + ".md")
            open(spec, "w").write(worker_spec(t, continuing))
            v = variant(t)
            model = t.get("model_override") or (ctx.C["WORKER_MODEL_" + rt.upper()] if runtimes.is_api(rt) else v["model"])
            env = {"DISPATCH_BRANCH": t["branch"], "DISPATCH_CONVERSATION": lr["conversation"] if continuing else "",
                   "DISPATCH_MODEL": model, "DISPATCH_SELF_CHECK": str(int(ctx.C["SELF_CHECK_ROUNDS"])),
                   "DISPATCH_VARIANT": ("pack" if v["pack"] else "nopack") + "-" + v["why"]}

            DISPATCHING.add(t["id"])
            DISPATCHING_FILES.update(t_files)
            DISPATCHING_WORKERS.add(w)
            claimed_ids.add(t["id"])
            if t_key:
                claimed_keys.add(t_key)
            busy_files.update(t_files)
            free.remove(w)
            free = by_quota(free)

            t.update(status="running", worker=w, dispatched=sbx.now())
            save_task(t)

            try:
                rc, out, err = sh([os.path.join(ctx.BIN, "dispatch"), w, spec], timeout=300, env=env)
                if rc != 0:
                    reason = (err or out).strip()[-200:]
                    t.update(status="pending")
                    t.pop("worker", None)
                    t.pop("run", None)
                    t.pop("dispatched", None)
                    save_task(t)
                    claimed_ids.discard(t["id"])
                    if t_key:
                        claimed_keys.discard(t_key)
                    busy_files.difference_update(t_files)
                    for w2 in free + [w]:
                        COOLDOWN[w2] = (time.time() + 1800, reason)
                    free.clear()
                    event(f"dispatch of {t['id']} to {w} refused: {reason} - retry in 30 min", task=t["id"])
                    continue
                run = out.split("-> ")[-1].split(" (")[0].strip()
                t.update(status="running", worker=w, run=run, dispatched=sbx.now())
                save_task(t)
                LAST_DISPATCHED[w] = time.time()
                QUOTA_CACHE.pop(w, None)
                dispatched.append(t)
                event(f"task {t['id']} ({t['role']}) dispatched to {w}" + (f", attempt {t['attempts'] + 1}" if t["attempts"] else "") +
                      (" (continuing its conversation)" if continuing else "") + (f" ({rt})" if rt != "agy" else "") +
                      (f" on {env['DISPATCH_MODEL']}" if env["DISPATCH_MODEL"] else ""), task=t["id"])
            finally:
                DISPATCHING.discard(t["id"])
                DISPATCHING_FILES.difference_update(t_files)
                DISPATCHING_WORKERS.discard(w)
        return dispatched


def orphaned(run):
    """A run whose supervisor died without finishing it (machine restart, OOM, killed by hand). A finished run is never
    an orphan; an actively running run is never an orphan. If it has neither finished nor is running, it is orphaned."""
    base = os.path.basename(run.rstrip("/"))
    if not RUN_DIR_RE.match(base):
        return False
    if os.path.exists(os.path.join(run, "finished")):
        return False
    if os.path.exists(os.path.join(run, "exitcode")):
        return False
    m = sbx.meta(run)
    if m.get("finished") or m.get("reason"):
        return False
    tid = m.get("task")
    if tid:
        tpath = os.path.join(ctx.FD, "tasks", tid + ".json")
        t = jload(tpath) if os.path.exists(tpath) else None
        if t and t.get("status") in ("done", "cancelled"):
            return False
    if sbx.is_running(run):
        return False
    started_ts = None
    started_file = os.path.join(run, "started")
    if os.path.exists(started_file):
        try:
            started_ts = _iso_ts(open(started_file).read().strip())
        except Exception:
            pass
    if not started_ts and m.get("dispatched"):
        try:
            started_ts = _iso_ts(m["dispatched"])
        except Exception:
            pass
    pid_file = os.path.join(run, "pid")
    has_live_pid = os.path.exists(pid_file) and sbx.alive(open(pid_file).read().strip())
    if started_ts and (time.time() - started_ts < 60) and (has_live_pid or os.path.exists(started_file)):
        return False
    return True


def recover_orphan(t):
    """Close an orphaned run (stop whatever it left running in the worker, mark it finished) and re-queue its task without
    counting an attempt - before this, such a task stayed "running" forever and blocked its files and its goal."""
    run, w = t.get("run", ""), t.get("worker", "")
    if re.fullmatch(r"worker-\d+", w):
        sh(["docker", "exec", w, "bash", "-c", 'sid=$(cat /tmp/run.sid 2>/dev/null); [ -n "$sid" ] && pkill -TERM -s "$sid"; exit 0'], timeout=30)
    if run:
        m = sbx.meta(run)
        m.update(reason="orphaned", finished=sbx.now())
        try:
            json.dump(m, open(os.path.join(run, "meta.json"), "w"), indent=1)
            open(os.path.join(run, "exitcode"), "w").write("128\n")
            open(os.path.join(run, "finished"), "w").write(sbx.now() + "\n")
        except OSError:
            pass
        sbx.append_ledger(dict(run_id=os.path.basename(run), date=sbx.today(), at=sbx.now(), worker=w, task=m.get("task", ""), test=False,
                               reason="orphaned", exit_code=128, tokens=sbx.stream_tokens(os.path.join(run, "stream.jsonl")), duration_s=0,
                               tool_calls=0, egress_alarms=0))
    t.update(status="pending")
    t.pop("worker", None)
    t.pop("run", None)
    t.pop("dispatched", None)
    save_task(t)
    if w:
        LAST_DISPATCHED.pop(w, None)
        COOLDOWN.pop(w, None)
        RESERVED.discard(w)
        QUOTA_CACHE.pop(w, None)
    event(f"task {t['id']}: its run on {w} ended without its supervisor (machine restart or crash) - re-queued, no attempt counted",
          task=t["id"])


def advance():
    cancelled = {g["id"] for g in goals() if g.get("status") == "cancelled"}
    known_goals = {g["id"] for g in goals()}
    can_think = llm_available()
    for t in tasks():
        gid = t.get("goal") or t.get("goal_id")
        # Invariant: cancel tasks referencing missing or cancelled goals, protecting lookups from KeyError
        if gid not in known_goals or gid in cancelled:
            if t.get("status") not in ("done", "cancelled"):
                if t.get("status") == "running" and not os.path.exists(os.path.join(t.get("run", ""), "finished")):
                    pass
                else:
                    why = f"goal {gid} no longer exists" if gid not in known_goals else (t.get("cancelled_why") or "goal cancelled")
                    t.update(status="cancelled", cancelled_why=why)
                    save_task(t)
                    event(f"task {t['id']}: {why}", task=t["id"])
                    continue
        if t["status"] == "running" and t.get("run") and orphaned(t["run"]):
            recover_orphan(t); continue
        if t["status"] == "running" and t.get("cancel_requested") and os.path.exists(os.path.join(t.get("run", ""), "finished")):
            t.update(status="cancelled", cancelled_why=t.get("cancelled_why") or "cancelled by the owner"); save_task(t)
            event(f"task {t['id']}: its run stopped - cancelled", task=t["id"]); continue
        if t["status"] == "running" and t.get("run") and os.path.exists(os.path.join(t["run"], "finished")):
            m = sbx.meta(t["run"])
            reason = m.get("reason")
            if reason == "setup-failed":
                sh(["docker", "exec", "-w", sbx.WORKER_CLONE, t["worker"], "git", "stash", "-u"], timeout=60)
                t.update(status="pending"); save_task(t)
                event(f"task {t['id']}: {t['worker']} clone was not clean - stashed it, task re-queued", task=t["id"]); continue
            if reason in ("killswitch", "orphaned"):
                t.update(status="pending"); save_task(t); continue
            if reason == "auth":
                COOLDOWN[t["worker"]] = (time.time() + 3600, "API key rejected (wrong, revoked or no credit)")
                t.update(status="pending", last_run=dict(worker=t["worker"], reason="auth", tokens=m.get("tokens", 0)))
                save_task(t)
                msg = f"{t['worker']}: the provider rejected its API key ({str(m.get('result_error') or '')[:160]}) - task re-queued, worker paused 60 min"
                event(msg, task=t["id"]); sbx.alert(msg); notify("auth", text=msg)
                continue
            if reason == "quota":
                # the account ran out mid-run: not the task's fault - re-queue without an attempt, rest that worker
                until = time.time() + 1800
                COOLDOWN[t["worker"]] = (until, "quota low: the account ran out during a run")
                QUOTA_CACHE.pop(t["worker"], None)
                t.update(status="pending", last_run=dict(worker=t["worker"], reason="quota", tokens=m.get("tokens", 0),
                                                         salvage=m.get("salvage")))
                save_task(t)
                event(f"task {t['id']}: {t['worker']} ran out of quota during the run - re-queued (no attempt counted); "
                      f"{t['worker']} rests 30 min", task=t["id"]); continue
            t["last_run"] = dict(worker=t["worker"], conversation=m.get("conversation_id"), tokens=m.get("tokens", 0), reason=reason,
                                 salvage=m.get("salvage"))
            if reason in ("token-cap", "timeout"):
                t["caps"] = t.get("caps", 0) + 1
            saved = " (WIP auto-saved to the branch)" if (m.get("salvage") or {}).get("rc") == 0 else ""
            if t.get("caps", 0) >= int(ctx.C["SPLIT_AFTER_CAPS"]) and not t.get("split_from"):
                t.update(status="splitting", run_reason=reason, run_tokens=m.get("tokens")); save_task(t)
                event(f"task {t['id']}: stopped by its {reason} {t['caps']}x{saved} - asking the planner to split it", task=t["id"])
            else:
                t.update(status="gate", run_reason=reason, run_tokens=m.get("tokens")); save_task(t)
                event(f"task {t['id']}: {t['worker']} finished ({reason}, {m.get('tokens', 0):,} tokens){saved} - testing + review", task=t["id"])
        if t["status"] in ("gate", "review") and can_think and t.get("not_before", 0) <= time.time():
            submit("task:" + t["id"], step_gate_review_merge, t)
        if t["status"] == "splitting" and can_think:
            submit("split:" + t["id"], step_split, t)
        if t["status"] == "merging" and t.get("not_before", 0) <= time.time():
            submit("merge:" + t["id"], step_upstream_merge, t)
        if t["status"] == "merge-ready":           # the owner approved the merge (APPROVE=merge)
            submit("task:" + t["id"], step_owner_merge, t)
    for g in goals():
        if g.get("status") == "new" and can_think:
            submit("goal:" + g["id"], step_plan, g)
        elif g.get("status") == "active":
            mine = [t for t in tasks() if (t.get("goal") or t.get("goal_id")) == g["id"]]
            if not mine:
                g.update(status="done", summary="no tasks generated", finished=sbx.now()); save_goal(g); continue
            all_ts = {t2["id"]: t2.get("status") for t2 in tasks()}
            for t in mine:
                if t.get("status") == "pending":
                    bad_deps = [d for d in t.get("depends_on", []) if all_ts.get(d) in ("blocked", "cancelled")]
                    if bad_deps:
                        msg = "dependency " + ", ".join(bad_deps) + " blocked/cancelled"
                        t.update(status="blocked", blocked_why=msg)
                        save_task(t)
                        event("task " + t["id"] + " blocked: " + msg, task=t["id"])
            if all(t.get("status") in ("done", "blocked", "cancelled") for t in mine):
                # Invariant: a goal must leave active state once all its tasks are done, blocked, or cancelled
                if all(t.get("status") == "cancelled" for t in mine):
                    g.update(status="cancelled", summary="all tasks cancelled", finished=sbx.now())
                    save_goal(g); event(f"goal {g['id']} cancelled: all tasks cancelled", goal=g["id"]); continue
                if all(t.get("status") in ("blocked", "cancelled") for t in mine):
                    g.update(status="blocked", summary="all tasks blocked or cancelled", finished=sbx.now())
                    save_goal(g); event(f"goal {g['id']} blocked: all tasks blocked or cancelled", goal=g["id"]); continue
                max_rnd = int(ctx.C.get("MAX_GOAL_ROUNDS", 3))
                if g.get("rounds", 0) >= max_rnd or not can_think:
                    g.update(status="done", summary="all tasks completed", finished=sbx.now())
                    save_goal(g); event(f"goal {g['id']} closed after {g.get('rounds', 0)} rounds", goal=g["id"]); notify("goal_done", g); continue
                res = "\n".join(f"- {t['id']} {t.get('title', t['id'])}: {t.get('status')}; {(t.get('history') or [{}])[-1].get('feedback', '')[:300]}" for t in mine)
                PHASE[g["id"]] = "checking whether the goal is met"
                submit("goal:" + g["id"], step_plan, g, res)
    for g in goals():
        if g.get("status") == "new" and "goal:" + g["id"] in INFLIGHT:
            PHASE.setdefault(g["id"], "planning tasks")
    req = gstate().get("roadmap_request")
    if req and can_think:
        PHASE["roadmap"] = "project roadmap"
        submit("roadmap:roadmap", step_roadmap, req)
    feed_idle_workers()


def feed_idle_workers():
    """Never let a worker idle. Capacity that is coming anyway = ready tasks + ~2 tasks per goal still being planned; while idle
    workers outnumber that, start further backlog goals in the SAME tick (planning runs in parallel), else ask the autopilot."""
    ts = tasks()
    free = [w for w in workers() if not worker_busy(w) and w not in RESERVED and COOLDOWN.get(w, (0, ""))[0] <= time.time()]
    ahead = False
    if not free:
        # PLAN_AHEAD: when every worker waits for its quota (Antigravity's weekly limit can idle a team for days), plan backlog
        # goals now - that costs only orchestrator quota - so up to ~2 tasks per worker are ready the moment the quota resets
        if not (ctx.C["PLAN_AHEAD"] and not any(t["status"] == "running" for t in ts) and pool_quota_out()):
            return
        ahead = True
        if not gstate().get("quota_out_since"):
            with state_tx() as st:
                st["quota_out_since"] = time.time()
            resets = sorted({str(u[1]).split("(resets ")[-1].rstrip(")") for u in COOLDOWN.values() if "resets" in str(u[1])})
            notify("quota_out", text="The team waits for the quota reset" + (f" ({', '.join(resets[:4])})" if resets else "") +
                   ". Backlog goals are planned ahead meanwhile.")
    elif gstate().get("quota_out_since"):
        with state_tx() as st:
            st.pop("quota_out_since", None)
    running_files = set(f for t in ts if t["status"] == "running" for f in t.get("files", []))
    planning = [g for g in goals() if g["status"] == "new"] + [k for k in INFLIGHT if k.startswith(("goal:", "roadmap"))]
    planning_tasks = sum(1 for t in ts if t.get("status") in ("planning", "splitting"))
    if ahead:
        slots = 2 * len(workers())
        coming = sum(1 for t in ts if t["status"] in ("pending", "splitting", "planning")) + 2 * len(planning)
    else:
        slots = len(free)
        coming = len(ready_tasks(ts, running_files)) + planning_tasks + 2 * len(planning)
    unfinished = {t["goal"] for t in ts if t["status"] in ("pending", "running", "gate", "review", "splitting", "merging", "approval", "merge-ready")}
    live = [g for g in goals() if g["status"] in ("new", "approval") or (g["status"] == "active" and g["id"] in unfinished)]
    cap = max(int(ctx.C["MAX_ACTIVE_GOALS"]), 2 * len(workers()))
    started = []
    with locked():                                   # the owner may add to the backlog (CLI / dashboard) at the same moment
        bl = jload(os.path.join(ctx.FD, "backlog.json"), [])
        while bl and slots > coming and len(live) + len(started) < cap:
            text = bl.pop(0)
            gid = new_goal_id()
            save_goal(dict(id=gid, text=text, status="new", created=sbx.now(), source="backlog"))
            started.append(gid)
            coming += 2
            event((f"all workers wait for quota - planning ahead: goal {gid}" if ahead else f"{len(free)} idle worker(s) - started goal {gid}") +
                  f" from the backlog: {text[:200]}", goal=gid)
        if started:
            jsave(os.path.join(ctx.FD, "backlog.json"), bl)
    if started:
        return
    if slots <= coming or "next:autopilot" in INFLIGHT:
        return
    st = gstate()
    if ctx.C["AUTOPILOT"] and not os.path.exists(os.path.join(ctx.FD, "AUTOPILOT_OFF")) and st.get("autopilot_quiet_until", 0) <= time.time():
        PHASE["autopilot"] = "autopilot picks the next goal from the vision"
        submit("next:autopilot", step_next)


CONF_MTIME = {}


def reload_conf_if_changed():
    """Settings changed in the dashboard (or by hand) take effect on the next tick - no restart."""
    paths = [os.path.join(ctx.ROOT, "etc", "foreman.conf")] + ([os.path.join(ctx.PDIR, "project.conf")] if ctx.PDIR else [])
    mt = {p: os.path.getmtime(p) for p in paths if os.path.exists(p)}
    if CONF_MTIME and mt != CONF_MTIME:
        ctx.C.update(ctx.load_conf())
        event("settings reloaded")
    CONF_MTIME.clear(); CONF_MTIME.update(mt)


def tick():
    reload_conf_if_changed()
    collect()
    try:
        if os.path.exists(os.path.join(ctx.FD, "PAUSED")) or sbx.killswitch_active():
            return
        advance()
        dispatch_ready()
        maybe_maintenance()
        maybe_upstream_sync()
    finally:
        write_live()


def run_loop():
    import fcntl
    os.makedirs(ctx.FD, exist_ok=True)
    lk = open(os.path.join(ctx.FD, ".lock"), "w")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another foreman is running", file=sys.stderr); sys.exit(3)
    for t in tasks():        # steps interrupted by a restart are simply redone
        if t["status"] == "review":
            t["status"] = "gate"; save_task(t)
    stop = []
    signal.signal(signal.SIGTERM, lambda *a: stop.append(1))
    event("foreman started")
    was_off = None
    while not stop:
        off = "paused" if os.path.exists(os.path.join(ctx.FD, "PAUSED")) else ("kill switch ON" if sbx.killswitch_active() else None)
        if off != was_off:
            event(f"foreman idle: {off}" if off else "foreman active"); was_off = off
        try:
            tick()
        except Exception:
            event("loop error: " + traceback.format_exc()[-600:])
        for _ in range(int(ctx.C["LOOP_SEC"])):
            if stop:
                break
            time.sleep(1)
    event("foreman stopping (running worker runs continue under their supervisors)")
    POOL.shutdown(wait=False, cancel_futures=True)
