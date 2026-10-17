"""Steps that need the planner: plan a goal (and check it when its tasks finished), split a task that is too big,
the autopilot's next goal, and a roadmap for a big request."""
import os, time

import sbx

from . import ctx
from .util import _strs, approvals, extract_json, norm_tasks, sanitize_acceptance
from .store import (event, goals, gsave, gstate, jload, jsave, locked, new_goal_id, next_task_number, notify, save_goal, save_task,
                    state_tx, tasks)
from .pool import workers
from .repo import own_files, refresh_repo
from .llm import llm
from .prompts import TASK_SCHEMA, context_bundle, plan_prompt


def hold_open_ended(g2, d):
    """An open-ended goal is never planned: the owner is asked for its finite version, and the answer replaces the goal's text."""
    question = str(d.get("question") or "").strip()[:300] or "What exactly should be finished, and how will we know it is done?"
    proposal = str(d.get("finite_goal") or "").strip()[:600]
    g2.update(status="blocked", blocked_why="open-ended goal: waiting for the owner's finite version"); save_goal(g2)
    q = f"[{g2['id']}] {question}" + (f" Proposed finite goal: {proposal}" if proposal else "")
    with locked():
        p = os.path.join(ctx.FD, "owner_questions.json")
        jsave(p, jload(p, []) + [q])
    event(f"goal {g2['id']} is open-ended - not planned, asking the owner: {question[:200]}", goal=g2["id"])
    notify("questions", text=q)


def step_plan(g, followup=None):
    refresh_repo()
    text, who = llm("check" if followup else "plan", plan_prompt(g, followup), g["id"])
    d = extract_json(text)

    def apply():
        g2 = jload(os.path.join(ctx.FD, "goals", g["id"] + ".json"))
        if not g2:
            return                                  # the goal file was removed while the planner was thinking
        if g2.get("status") != g.get("status") or g2.get("text") != g.get("text"):
            event(f"goal {g['id']}: changed by the owner while it was planned - this plan is dropped", goal=g["id"])
            return                                  # edited, cancelled or re-planned meanwhile: the next round plans the new text
        if d and d.get("open_ended") is True and followup is None:
            hold_open_ended(g2, d)
            return
        if not d or (not d.get("done") and not norm_tasks(d, 0) and followup is None):
            g2["plan_errors"] = g2.get("plan_errors", 0) + 1
            event(f"goal {g['id']}: {who} replied without valid JSON (attempt {g2['plan_errors']})", goal=g["id"])
            if g2["plan_errors"] >= 3:
                g2["status"] = "blocked"; sbx.alert(f"foreman: goal {g['id']} could not be planned")
            save_goal(g2); return
        if isinstance(d.get("criteria"), list):
            g2["criteria"] = [dict(criterion=str(c.get("criterion", ""))[:300], met=bool(c.get("met")), evidence=str(c.get("evidence", ""))[:300])
                              for c in d["criteria"][:8] if isinstance(c, dict)]
            g2["criteria_at"] = sbx.now()
        if d.get("done") is True:
            g2.update(status="done", summary=str(d.get("summary") or "")[:2000], finished=sbx.now()); save_goal(g2)
            for ot in [t for t in tasks() if t["goal"] == g["id"] and t["status"] in ("blocked", "pending", "approval")]:
                ot["status"] = "cancelled"; ot["cancelled_why"] = "goal completed via replacement task"; save_task(ot)
            event(f"goal {g['id']} DONE ({who}): {str(d.get('summary') or '')[:500]}", goal=g["id"]); notify("goal_done", g2); return
        for ot in [t for t in tasks() if t["goal"] == g["id"] and t["status"] in ("blocked", "pending", "approval")]:
            ot["status"] = "cancelled"; ot["cancelled_why"] = f"superseded by round {g2.get('rounds', 0) + 1}"; save_task(ot)
        planned = norm_tasks(d, next_task_number())
        presplit, gate = [], approvals()
        for tid, x, deps in planned:
            prefix = "test" if "test" in x["role"].lower() and "developer" not in x["role"].lower() else "feat"
            nt = dict(id=tid, goal=g["id"], branch=f"{prefix}/{tid}", depends_on=deps, status="pending", attempts=0, history=[],
                      created=sbx.now(), planned_by=who, **x)
            if len(own_files(nt)) > int(ctx.C["MAX_TASK_FILES"]):
                nt.update(status="splitting", presplit=True); presplit.append(tid)
            if "plan" in gate:
                nt.update(status="approval", awaiting="plan", after=nt["status"])     # the owner approves the plan first
            else:
                hold_if_risky(nt, gate)
            save_task(nt)
        if presplit:
            event(f"goal {g['id']}: {', '.join(presplit)} own more than {ctx.C['MAX_TASK_FILES']} files - splitting before dispatch", goal=g["id"])
        waiting = "plan" in gate and planned
        g2.update(status="approval" if waiting else "active", rounds=g2.get("rounds", 0) + 1, notes=str(d.get("notes") or "")[:1000])
        if d.get("goal_acceptance") and not g2.get("acceptance"):
            g2["acceptance"] = [a[:300] for a in _strs(d["goal_acceptance"])][:6]
        save_goal(g2)
        event(f"goal {g['id']} planned by {who}: {len(planned)} task(s)" + (" - waiting for your approval" if waiting else "") +
              f" - {str(d.get('notes') or '')[:300]}", goal=g["id"], tasks=[p[0] for p in planned])
        if waiting:
            notify("approval", text=f"The plan for: {g2['text'][:200]}\n" + "\n".join(f"- {p[1]['title']} ({p[1]['role']})" for p in planned))
    return apply


def step_split(t):
    """A task stopped by its token cap SPLIT_AFTER_CAPS times is too big: the planner splits it into 2-3 smaller tasks. The first
    new task keeps the branch, so it continues from the auto-saved WIP."""
    refresh_repo()
    prompt = (f"{context_bundle()}\n## A task that is too big\nTask {t['id']} ({t['role']}): {t['title']}\n### Spec\n{t['spec']}\n### Acceptance\n" +
              "\n".join(f"- {a}" for a in sanitize_acceptance(t.get("acceptance", []))) + f"\n### Files\n{', '.join(t.get('files', []))}\n\n"
              + (f"It owns {len(own_files(t))} files; tasks spread over more than {ctx.C['MAX_TASK_FILES']} files hit the token cap about twice "
                 f"as often, so it is split before anyone works on it. " if t.get("presplit") and not t.get("caps") else
                 f"Its worker was stopped by the per-run token cap {t.get('caps', 0)} times. Work already done may be on branch `{t['branch']}` "
                 f"(the first task you list continues on that branch). ") +
              f"Split the task into 2-3 smaller tasks that together deliver exactly the same spec and acceptance criteria, each well under "
              f"{ctx.C['TASK_TOKEN_BUDGET']:,} tokens of work and at most {ctx.C['MAX_TASK_FILES']} files, disjoint files where possible.\n"
              f"Reply with ONLY one JSON object in this schema:\n{TASK_SCHEMA}\n")
    text, who = llm("plan", prompt, t["id"])
    d = extract_json(text) or {}

    def apply():
        t2 = jload(os.path.join(ctx.FD, "tasks", t["id"] + ".json"))
        new = norm_tasks(d, next_task_number(), limit=3)
        if not new:
            if t2.get("presplit") and not t2.get("caps"):
                t2.update(status="pending", presplit=False); save_task(t2)
                event(f"task {t['id']}: {who} could not split it - dispatching it as planned", task=t["id"]); return
            t2.update(status="gate"); save_task(t2)
            event(f"task {t['id']}: {who} could not split it - reviewing what exists", task=t["id"]); return
        ids = []
        for i, (tid, x, deps) in enumerate(new):
            ids.append(tid)
            if t.get("risk") == "risky":
                x["risk"] = "risky"                    # a part of risky work stays risky
            nt = dict(id=tid, goal=t["goal"], branch=t["branch"] if i == 0 else f"{t['branch'].split('/')[0]}/{tid}",
                      depends_on=list(t.get("depends_on", [])) + deps, status="pending", attempts=0, history=[],
                      created=sbx.now(), planned_by=who, split_from=t["id"], **x)
            for k in ("pin_worker", "model_override", "owner_notes"):         # the owner's choices for the task carry over
                if t.get(k):
                    nt[k] = t[k]
            hold_if_risky(nt, approvals())
            save_task(nt)
        t2.update(status="cancelled", cancelled_why=f"split into {', '.join(ids)}"); save_task(t2)
        for ot in tasks():
            if t["id"] in ot.get("depends_on", []):
                ot["depends_on"] = [x for x in ot["depends_on"] if x != t["id"]] + ids; save_task(ot)
        event(f"task {t['id']} split by {who} into {', '.join(ids)}", task=t["id"])
    return apply


def step_next():
    """Autopilot: a worker would idle and the backlog is empty -> the orchestrator proposes the next goal from the owner's vision."""
    refresh_repo()
    gl = "\n".join(f"- [{g['status']}] {g['text'][:200]}" for g in goals()) or "(none)"
    w_count = len(workers()) or 4
    prompt = (f"You are the manager of a {w_count}-worker team working in the git repository described below.\n\n"
              f"{context_bundle()}\n## Goals so far (done, active, cancelled)\n{gl}\n\n"
              f"Workers are idle. Propose the single most valuable NEXT goal from the owner's vision that is not done or in progress yet, "
              f"formulated so it can be split into 2-5 parallel tasks across {w_count} workers, "
              "buildable offline inside the sandbox (skip anything needing external APIs, "
              "real personal data or decisions only the owner can make).\n"
              "The goal MUST BE FINITE: a concrete deliverable or a closed list with an end state a reviewer can tick off. Never propose "
              "'more', 'improve', 'finish X to production readiness', 'continue' or a variation of a goal above (check every goal above, "
              "done or not, for overlap); if the vision's remaining work is only open-ended polishing, the project is DONE: reply with goal null. "
              "Reply with ONLY one JSON object: "
              f'{{"goal": "the goal in {ctx.C["OWNER_LANG"]}, concrete, as the owner would write it", "why": "one sentence"}} '
              'or {"goal": null, "why": "..."} if nothing sensible is left.\n')
    text, who = llm("next", prompt)
    d = extract_json(text) or {}

    def apply():
        if d.get("goal") and str(d["goal"]).strip():
            gid = new_goal_id()
            save_goal(dict(id=gid, text=str(d["goal"])[:4000], status="new", created=sbx.now(), source="autopilot"))
            event(f"autopilot ({who}): new goal {gid}: {str(d['goal'])[:200]} - {str(d.get('why', ''))[:200]}", goal=gid)
        else:
            why = str(d.get("why", ""))[:300]
            open(os.path.join(ctx.FD, "AUTOPILOT_OFF"), "w").write(sbx.now() + "\n")       # a finished project is not worked on forever
            event(f"autopilot ({who}): PROJECT DONE - nothing left from the vision ({why}); autopilot switched off")
            notify("project_done", text=f"Nothing is left from the vision, so the autopilot switched itself off.\n{why}\nAdd a goal or edit the vision to continue.")
    return apply


def step_roadmap(text):
    """`bin/foreman project`: one big owner request -> an ordered roadmap of small goals appended to the backlog (workers drain it nonstop)."""
    refresh_repo()
    gl = "\n".join(f"- [{g['status']}] {g['text'][:200]}" for g in goals()) or "(none)"
    bl = "\n".join(f"- {x[:200]}" for x in jload(os.path.join(ctx.FD, "backlog.json"), [])) or "(empty)"
    prompt = (f"You are the manager of a team of {max(2, len(workers()))} workers working in the git repository described below.\n\n"
              f"{context_bundle()}\n## Goals so far\n{gl}\n\n## Backlog already queued\n{bl}\n\n"
              f"## The owner's request\n{text}\n\nTurn this into a ROADMAP: an ordered list of 6-30 goals that together deliver the request, "
              "skipping what is already done, in progress or queued. Each goal: small (about 2-5 worker tasks), self-contained, buildable "
              f"offline inside the sandbox, written in {ctx.C['OWNER_LANG']} the way the owner would write it, mentioning which module(s) it owns so "
              "goals running at the same time touch different files. Order: foundations first, then features, then hardening (tester / "
              "security tester goals) and integration. Leave out anything needing external APIs, real personal data or a decision only "
              'the owner can make - list those under "owner_questions" instead.\nReply with ONLY one JSON object: '
              f'{{"goals": ["...", "..."], "owner_questions": ["..."], "notes": "1-2 sentences in {ctx.C["OWNER_LANG"]}"}}\n')
    out, who = llm("roadmap", prompt)
    d = extract_json(out) or {}

    def apply():
        goals_ = d.get("goals") if isinstance(d.get("goals"), list) else []
        new = [str(x)[:4000] for x in goals_ if str(x).strip()][:40]
        qs = [str(q)[:300] for q in (d.get("owner_questions") if isinstance(d.get("owner_questions"), list) else [])][:10]
        with locked():
            st = gstate(); st.pop("roadmap_request", None); gsave(st)
            if new:
                p = os.path.join(ctx.FD, "backlog.json")
                jsave(p, jload(p, []) + new)
            if qs:
                jsave(os.path.join(ctx.FD, "owner_questions.json"), jload(os.path.join(ctx.FD, "owner_questions.json"), []) + qs)
        if qs:
            notify("questions", text="\n".join(f"- {q}" for q in qs))
        if not new:
            event(f"roadmap ({who}): no usable goals in the reply - nothing queued")
            return
        event(f"roadmap ({who}): {len(new)} goal(s) queued in the backlog" + (f", {len(qs)} question(s) for the owner" if qs else "") +
              f" - {str(d.get('notes', ''))[:200]}")
    return apply


def hold_if_risky(t, gate):
    """APPROVE contains "risky": a risky task waits for the owner before any worker starts it."""
    if "risky" in gate and t.get("risk") == "risky" and t["status"] == "pending":
        t.update(status="approval", awaiting="risky")
