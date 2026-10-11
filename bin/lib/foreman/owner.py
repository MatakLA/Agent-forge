"""The owner's questions and answers, and the owner's actions on tasks and goals."""
import os, re

import sbx

from . import ctx
from .util import approvals
from .store import event, goals, jload, jsave, locked, save_goal, save_task, tasks
from .repo import refresh_repo
from .llm import llm
from .prompts import context_bundle
from .planning import hold_if_risky


def ask(question):
    """Answer a question about the project from the repository context (one orchestrator call)."""
    refresh_repo()
    prompt = (f"{context_bundle()}\n## Question from the owner\n{question.strip()[:4000]}\n\nAnswer in {ctx.C['OWNER_LANG']}, concretely, "
              "from the repository content above. Cite file paths. If the repository does not contain the answer, say so plainly. "
              "Plain text or markdown, no JSON.")
    text, who = llm("ask", prompt)
    event(f"owner question answered by {who}: {question.strip()[:120]}")
    return text, who


def answer_question(n, answer):
    """The owner answers the planner's question n: it leaves the open list and goes into decisions.md, which every later
    plan and roadmap reads - so the team does not ask again and builds on the decision."""
    with locked():
        p = os.path.join(ctx.FD, "owner_questions.json")
        qs = jload(p, [])
        if not 1 <= n <= len(qs):
            return None
        q = qs.pop(n - 1)
        jsave(p, qs)
        if answer.strip():
            dp = os.path.join(ctx.CFG_DIR, "decisions.md")
            new = not os.path.exists(dp)
            with open(dp, "a") as f:
                if new:
                    f.write("# Owner decisions (answers to the planner's questions; read by every plan)\n\n")
                f.write(f"- Q: {q}\n  A: {' '.join(answer.split())[:2000]}  ({sbx.today()})\n")
    event(f"owner answered: {q[:120]}" if answer.strip() else f"owner dismissed the question: {q[:120]}")
    return q


# ---------------------------------------------------------------- the owner steers: tasks and goals (dashboard / CLI)
TASK_ID = re.compile(r"\d{4}-[\w-]{1,60}")
GOAL_ID = re.compile(r"G\d{8}-\d{6}(-[\w-]{1,30})?")
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,99}")
TASK_ACTIONS = ("approve", "cancel", "retry", "feedback", "assign", "model")
GOAL_ACTIONS = ("approve", "replan", "pause", "resume", "prioritize", "edit", "cancel")


def _request_stop(t):
    """Ask the supervisor of the task's running run to stop it (it checks for this file every few seconds)."""
    run = t.get("run") or ""
    if run and os.path.isdir(run) and not os.path.exists(os.path.join(run, "finished")):
        open(os.path.join(run, "stop"), "w").write(sbx.now() + "\n")


def task_action(tid, action, text="", target=""):
    """One owner action on a task. Returns (ok, message). Every action is checked against the task's state:
    approve   a task waiting for approval: a risky task starts, an approved merge is merged, a planned task is released
    cancel    any open task; a running one is stopped first (its work stays on its branch)
    retry     a blocked or cancelled task goes back to the queue with fresh attempts
    feedback  work that exists (waiting for the merge, blocked, ...) goes back to the worker with the owner's words; for a
              task nobody worked on yet the words are added to its spec
    assign    only this worker may take it ("" = any)       model   this worker model for it ("" = the usual choice)"""
    if not TASK_ID.fullmatch(tid or ""):
        return False, "invalid task id"
    if action not in TASK_ACTIONS:
        return False, f"unknown action (one of: {', '.join(TASK_ACTIONS)})"
    p = os.path.join(ctx.FD, "tasks", tid + ".json")
    text = " ".join(str(text or "").split())[:3000]
    with locked():
        t = jload(p)
        if not isinstance(t, dict):
            return False, "no such task"
        st = t["status"]
        closed = st in ("done", "cancelled")
        if action == "approve":
            if st != "approval":
                return False, f"{tid} is not waiting for your approval (it is {st})"
            kind = t.pop("awaiting", "")
            if kind == "merge":
                t.update(status="merge-ready", merge_approved=True); msg = "approved - it is merged within a minute"
            elif kind == "plan":
                t["status"] = t.pop("after", "pending")
                hold_if_risky(t, approvals() - {"plan"})
                msg = "released" + (" - it still needs your OK as a risky task" if t["status"] == "approval" else "")
            else:
                t["status"] = "pending"; msg = "approved - the next free worker starts it"
        elif action == "cancel":
            if closed:
                return False, f"{tid} is already {st}"
            if st == "running":
                t.update(cancel_requested=True, cancelled_why="cancelled by the owner")
                _request_stop(t)
                msg = "its run is being stopped; the task is cancelled when it ends (the work stays on its branch)"
            else:
                t.update(status="cancelled", cancelled_why="cancelled by the owner"); msg = "cancelled"
        elif action == "retry":
            if st not in ("blocked", "cancelled"):
                return False, f"only a blocked or cancelled task can be retried ({tid} is {st})"
            t.update(status="pending", attempts=0, caps=0, review_fails=0, regates=0, blocked_why="", not_before=0)
            for k in ("cancel_requested", "cancelled_why", "awaiting", "merge_approved"):
                t.pop(k, None)
            msg = "back in the queue"
        elif action == "feedback":
            if not text:
                return False, "empty feedback"
            if st == "running":
                return False, "its worker is busy with it right now - send the feedback when the run has ended"
            if closed:
                return False, f"{tid} is {st} - for more changes add a new goal"
            t.setdefault("history", []).append(dict(at=sbx.now(), outcome="owner", feedback=text[:1500]))
            if st == "pending" and not t.get("attempts") and not t.get("last_run"):
                t["owner_notes"] = (t.get("owner_notes", "") + "\n- " + text).strip()
                msg = "added to the task's spec"
            else:
                if st == "blocked":
                    t["attempts"] = 0
                t.update(status="pending", feedback=f"The owner looked at your work and asks:\n{text}", gate_tail="", review_fails=0,
                         regates=0, not_before=0, blocked_why="")
                for k in ("merge_approved", "awaiting", "after"):
                    t.pop(k, None)
                msg = "sent back to its worker with your words"
        elif action == "assign":
            if st == "running" or closed:
                return False, f"{tid} is {st} - assign it before it starts"
            if target and not re.fullmatch(r"worker-\d+", target):
                return False, "a worker name such as worker-3, or empty for any"
            t["pin_worker"] = target; msg = f"only {target} takes it" if target else "any worker takes it"
        elif action == "model":
            if st == "running" or closed:
                return False, f"{tid} is {st} - choose the model before it starts"
            if target and not MODEL_NAME.fullmatch(target):
                return False, "invalid model name"
            t["model_override"] = target; msg = f"its next run uses {target}" if target else "it uses the usual model again"
        save_task(t)
    event(f"task {tid}: {action} by the owner - {msg}" + (f": {text[:160]}" if text and action == "feedback" else ""), task=tid)
    return True, msg


def goal_action(gid, action, text=""):
    """One owner action on a goal. Returns (ok, message).
    approve     the plan waiting for approval goes ahead (risky tasks may still wait, APPROVE=risky)
    replan      the plan waiting for approval is dropped; the planner plans again with the owner's note
    pause       none of its tasks starts (running ones finish)            resume      they may start again
    prioritize  its tasks go before every other goal's                     edit        new text for a goal not started yet
    cancel      the goal and its open tasks are dropped (running runs are stopped)"""
    if not GOAL_ID.fullmatch(gid or ""):
        return False, "invalid goal id"
    if action not in GOAL_ACTIONS:
        return False, f"unknown action (one of: {', '.join(GOAL_ACTIONS)})"
    p = os.path.join(ctx.FD, "goals", gid + ".json")
    text = str(text or "").strip()[:4000]
    with locked():
        g = jload(p)
        if not isinstance(g, dict):
            return False, "no such goal"
        mine = [t for t in tasks() if t["goal"] == gid]
        st = g["status"]
        if action in ("approve", "replan") and st != "approval":
            return False, f"the goal is not waiting for a plan approval (it is {st})"
        if action == "approve":
            n = 0
            for t in mine:
                if t["status"] == "approval" and t.get("awaiting") == "plan":
                    t.pop("awaiting", None)
                    t["status"] = t.pop("after", "pending")
                    hold_if_risky(t, approvals())
                    save_task(t); n += 1
            g["status"] = "active"; msg = f"plan approved - {n} task(s) released"
        elif action == "replan":
            if not text:
                return False, "say what to change in the plan"
            for t in mine:
                if t["status"] == "approval" and t.get("awaiting") == "plan":
                    t.update(status="cancelled", cancelled_why="plan sent back by the owner"); save_task(t)
            g["owner_notes"] = (g.get("owner_notes", "") + "\n- " + text).strip()[-4000:]
            g.update(status="new", rounds=max(0, g.get("rounds", 1) - 1)); msg = "the planner plans it again with your note"
        elif action in ("pause", "resume"):
            if st in ("done", "cancelled"):
                return False, f"the goal is {st}"
            g["paused"] = action == "pause"
            msg = "paused - none of its tasks starts (running ones finish)" if g["paused"] else "resumed"
        elif action == "prioritize":
            if st in ("done", "cancelled"):
                return False, f"the goal is {st}"
            g["priority"] = max([int(x.get("priority") or 0) for x in goals()] + [0]) + 1; msg = "its tasks go first"
        elif action == "edit":
            if st not in ("new", "approval"):
                return False, "only a goal whose work has not started can be edited - cancel it and add a new one"
            if not text:
                return False, "empty text"
            g["text"] = text
            if st == "approval":
                for t in mine:
                    if t["status"] == "approval" and t.get("awaiting") == "plan":
                        t.update(status="cancelled", cancelled_why="goal edited by the owner"); save_task(t)
                g.update(status="new", rounds=max(0, g.get("rounds", 1) - 1))
            msg = "changed" + (" - planned again" if st == "approval" else "")
        elif action == "cancel":
            if st in ("done", "cancelled"):
                return False, f"the goal is already {st}"
            g["status"] = "cancelled"
            for t in mine:
                if t["status"] == "running":
                    t.update(cancel_requested=True, cancelled_why="goal cancelled by the owner"); _request_stop(t); save_task(t)
                elif t["status"] not in ("done", "cancelled"):
                    t.update(status="cancelled", cancelled_why="goal cancelled by the owner"); save_task(t)
            msg = "cancelled (running runs are stopped)"
        save_goal(g)
    event(f"goal {gid}: {action} by the owner - {msg}" + (f": {text[:160]}" if text else ""), goal=gid)
    return True, msg
