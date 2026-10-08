"""Prompts: planning, goal checks, reviews and the worker's task spec."""
import os

import sbx, codemap

from . import ctx
from .store import event, goals, jload, tasks
from .pool import workers
from .repo import codemap_main, context_pack, read_repo, repo_tree, variant
from .knowledge import decisions, lessons, role_playbook


# ---------------------------------------------------------------- prompts
TASK_SCHEMA = """{"tasks": [{"key": "short-unique-key", "title": "...", "role": "<a slug from the role catalogue>", "depends_on": ["key of a task in THIS list"],
  "files": ["exact repo paths the task may create/modify"], "risk": "routine|risky",
  "spec": "markdown: precisely what to build/write - interfaces, data shapes, edge cases, which existing file to follow",
  "acceptance": ["verifiable criterion", "..."]}],
 "goal_acceptance": ["observable outcome that proves the WHOLE goal works, e.g. an end-to-end behaviour", "..."],
 "notes": "one or two sentences for the owner"}"""


PLAN_EXAMPLE = """Example of a GOOD plan for 4 workers and the goal "add a contacts module with birthdays":
- contacts-store (backend-developer): contacts.py + test_contacts.py - storage, add/list operations, interface written in the spec
- birthdays-ops (backend-developer): birthdays.py + test_birthdays.py - built against the contacts interface from the spec, NOT waiting for it
- contacts-docs (technical-writer): docs/contacts.md - user-facing how-to, from the same interface spec
- contacts-security (security-tester, depends_on contacts-store) - only because it stores personal data (risky)
Three tasks start at once; one waits for a real reason. A BAD plan chains store -> ops -> tests -> review -> docs."""


def context_bundle():
    vf = os.path.join(ctx.PDIR or ctx.ROOT, ctx.C["VISION_FILE"])
    vision = open(vf).read()[:30000] if os.path.exists(vf) else "(none)"
    hist = "\n".join(f"- {t['id']} [{t['status']}] {t['title']} ({t['role']})" for t in tasks()[-40:]) or "(none yet)"
    active = "\n".join(f"- [{g['status']}] {g['text'][:200]}" for g in goals() if g["status"] in ("new", "active")) or "(none)"
    tree = repo_tree()
    pd = ctx.C["PROJECT_DIR"].strip("/")
    cm = codemap_main()
    mods = codemap.render(cm, [p for p in sorted(cm) if not cm[p]["test"]], max_chars=50000)
    tests = [p for p in sorted(cm) if cm[p]["test"]]
    mods += f"\n\nTest files: {', '.join(tests)}" if tests else ""
    agents = read_repo((pd + "/AGENTS.md") if pd else "AGENTS.md", 8000) or "(none)"
    dec = decisions()
    return (f"## Owner's vision ({ctx.C['VISION_FILE']})\n{vision}\n\n" + (f"## Owner's decisions (binding)\n{dec}\n\n" if dec else "") +
            f"## AGENTS.md (every worker reads it)\n{agents}\n\n"
            f"## Repository files on main\n{tree}\n\n## Code map (purpose + interface of every module on main)\n{mods}\n## Goals in progress (do not duplicate them)\n{active}\n\n"
            f"## Tasks so far\n{hist}\n")


def plan_prompt(g, followup=None):
    w_list = workers() or ["worker-1", "worker-2", "worker-3", "worker-4"]
    n = len(w_list)
    head = f"You are the manager of a {n}-worker team ({', '.join(w_list)}) working in the git repository described below.\n\n{context_bundle()}\n"
    if followup is None:
        ask = (f"## Goal from the owner\n{g['text']}\n\nBreak this goal into tasks.\n"
               f"- PARALLEL FIRST: aim for at least {n} tasks that can start right now. Each task owns DISJOINT files; write the interface "
               f"other tasks rely on (function names, data shapes, schemas) into every spec that needs it, so they are built at the same time "
               f"instead of waiting. depends_on only when a task cannot even start without another task's merged code.\n"
               f"- No routine review tasks: every branch is already gated (full test suite in a clean container) and reviewed. Add a tester / "
               f"security-tester task only for risky work (money, personal data, security, dates/timezones) or when the goal asks.\n"
               f"- Size every task for about {ctx.C['TASK_TOKEN_BUDGET']:,} tokens of agent work (one module plus its tests); split anything larger. "
               f"A task owns at most {ctx.C['MAX_TASK_FILES']} files (shared registries do not count) - tasks spread over more files hit the "
               f"token cap about twice as often, and the foreman splits them before anyone starts.\n"
               f"- Pick each task's role from the role catalogue; non-code roles (writer, designer, translator, analyst) are welcome when the "
               f"goal needs text, docs or design.\n- Do not duplicate goals already in progress.\n- Write 2-5 goal_acceptance criteria: observable "
               f"outcomes of the whole goal (not per task). Every task's spec must serve them; the goal is done only when all hold.\n\n{PLAN_EXAMPLE}\n")
    else:
        ga = "\n".join(f"- {a}" for a in g.get("acceptance", [])) or "(none recorded - judge from the goal text)"
        ask = (f"## Goal from the owner\n{g['text']}\n\n## Goal acceptance criteria\n{ga}\n\n## Results of this goal's tasks\n{followup}\n\n"
               f"Check every goal acceptance criterion against the merged results. Decide whether the goal is achieved. "
               f"If yes, reply {{\"done\": true, \"summary\": \"2-4 sentences for the owner\", \"criteria\": [...]}}. If not, reply with "
               f"follow-up tasks in the schema below (only what is genuinely missing, parallel where possible) plus \"criteria\". "
               f"\"criteria\" is a list with one entry per goal acceptance criterion, in order: "
               f"{{\"criterion\": \"...\", \"met\": true|false, \"evidence\": \"one short sentence\"}}.\n")
    return head + ask + f"\nReply with ONLY one JSON object, no prose, in this schema:\n{TASK_SCHEMA}\n"


def review_prompt(t, gate_out, diff):
    pb = role_playbook(t["role"])
    dod = ""
    if pb and "## Definition of done" in pb:
        dod = "\n## The role's definition of done\n" + pb.split("## Definition of done", 1)[1].split("\n## ", 1)[0].strip() + "\n"
    return (f"You are the reviewing manager. Decide whether this worker branch fulfils its task. You do not write code.\n"
            f"You have NO file or tool access and must not call any tool - everything you need is below. Do not explore; judge from what "
            f"you have. Your entire reply is one JSON object and nothing else.\n\n"
            f"## Task {t['id']}: {t['title']}\nRole: {t['role']}   Risk: {t.get('risk', 'routine')}   Attempt: {t['attempts'] + 1}\n"
            f"Allowed files: {', '.join(t.get('files', [])) or '(not restricted)'}\n\n### Spec\n{t['spec']}\n\n### Acceptance criteria\n" +
            "\n".join(f"- {a}" for a in t.get("acceptance", [])) + f"\n{dod}" + goal_context(t) +
            f"\n## Deterministic gate (full test suite in a clean, offline container + static scans)\n```\n{gate_out[-6000:]}\n```\n\n"
            f"## Diff against main\n```diff\n{diff}\n```\n\n"
            "Approve when the acceptance criteria are met and nothing is faked; do not demand gold-plating. An EMPTY diff with a passing gate "
            "means the work already reached main earlier - approve with feedback \"no diff: already merged\", never reject. For risk=risky you "
            "may list up to 4 mutations (small exact text replacements in the NEW code that a good test suite must catch). Judge a tester / "
            "security tester by what they found or covered, a writer/designer by the deliverable's quality for its audience.\n"
            'Reply with ONLY: {"verdict": "approve" | "rework" | "reject", "feedback": "concrete, actionable instructions for the worker '
            '(rework) or a one-line summary (approve)", "mutations": [{"name": "...", "file": "repo/path", "old": "exact existing text", '
            '"new": "replacement"}], "lesson": "only when sending work back: ONE general sentence that would have prevented this mistake in '
            'ANY future task (not specific to this file), else empty"}\nUse "reject" only when the task itself is wrong/impossible.\n')


def goal_context(t):
    g = jload(os.path.join(ctx.FD, "goals", t["goal"] + ".json")) or {}
    ga = g.get("acceptance") or []
    return ("\n## The goal this task serves\n" + str(g.get("text", ""))[:600] +
            ("\nThe goal is done when:\n" + "\n".join(f"- {a}" for a in ga) if ga else "") + "\n") if g else ""


def clone_hint():
    return "~/" + os.path.relpath(sbx.WORKER_CLONE, "/home/worker")


def rules_block(t):
    pd = ctx.C["PROJECT_DIR"].strip("/")
    agents = f"`{pd}/AGENTS.md`" if pd else "`AGENTS.md`"
    ls = lessons()
    return (f"""## How you work (always)
- Work only inside `{clone_hint()}`. Follow {agents}. No `pip install`, no network (the sandbox blocks it and repeated attempts stop
  the whole team), do not touch git config.
- Start with `git fetch origin`. If `origin/{t['branch']}` exists, `git checkout -B {t['branch']} origin/{t['branch']}` and continue from it
  (it holds earlier attempts and possibly an auto-saved WIP commit - do not start over); otherwise `git checkout -b {t['branch']} origin/main`.
- Budget: about {ctx.C['TASK_TOKEN_BUDGET']:,} tokens; the run is killed at the hard cap. COMMIT AND PUSH AFTER EVERY GREEN STEP
  (`git add -A && git commit -m "..." && git push -u origin {t['branch']}`). Do not re-read large files you already read.
- If `main` moved and your branch conflicts: `git rebase origin/main`, resolve, then `git push --force-with-lease`.
- Trial and error until the FULL suite passes: run `{sbx.test_cmd()}` from the repo root, read failures, fix, repeat.
- Never fake it: no skip/xfail, no detecting tests or callers, no weakened or deleted assertions, no editing existing tests just to make them pass.
- Every module you create starts with a docstring: what it is for, its public interface, important invariants (3-8 lines). When you
  change a module, keep its docstring true. The team's code map is generated from these docstrings - it is how others find your code.
- Never push `main`, never open a PR.
- Final reply: what you did, the final test summary line, and anything you could not do and why.
""" + ("\n## Team lessons (from earlier reworks - avoid repeating them)\n" + "\n".join(f"- {l}" for l in ls) + "\n" if ls else ""))


def rework_block(t):
    s = []
    lr = t.get("last_run") or {}
    if lr.get("reason") in ("token-cap", "timeout"):
        s.append(f"Your previous run was stopped by its {lr['reason']}" + (" - your unfinished work was auto-saved as a WIP commit on the branch, "
                 "continue from it" if (lr.get("salvage") or {}).get("rc") == 0 else "") + ". Work in smaller steps and push after each.\n")
    if t.get("feedback"):
        s.append(f"## Rework - attempt {t['attempts'] + 1} of {ctx.C['MAX_REWORK'] + 1}. The reviewer sent your branch back:\n{t['feedback']}\n")
        if t.get("gate_tail"):
            s.append(f"Gate output (tests in a clean container):\n```\n{t['gate_tail'][-3000:]}\n```\n")
    return "\n".join(s)


def worker_spec(t, continuing=False):
    """The prompt a worker gets. `continuing`: the worker resumes its own earlier agy conversation (it already has the full
    task and playbook), so it only needs what changed - much shorter, and no re-reading of the repository."""
    if continuing:
        return "\n".join([f"# Task {t['id']} - continue (same branch `{t['branch']}`)\n", rework_block(t), rules_block(t)])
    pb = role_playbook(t["role"])
    s = [f"# Task {t['id']} - {t['title']}\n", f"Branch: `{t['branch']}`   Repo clone: `{clone_hint()}`   Goal: {t['goal']}\n",
         f"**Your role for this task: {t['role']}.**\n" + (f"\n{pb.strip()}\n" if pb else "Act as that role would, and still do whatever it takes to finish.\n"),
         "## The task\n" + t["spec"].rstrip() + "\n", "## Acceptance criteria\n" + "\n".join(f"- {a}" for a in t.get("acceptance", [])) + "\n",
         goal_context(t),
         "## Files\n" + ("\n".join(f"- `{f}`" for f in t.get("files", [])) or "- (as the spec says)") +
         "\nTouch other files only when the spec clearly requires it, and say so in your final reply.\n", rules_block(t), rework_block(t)]
    if variant(t)["pack"]:
        try:
            s.append(context_pack(t))
        except Exception as e:                  # the pack is an optimisation; a task never fails because of it
            event(f"task {t['id']}: context pack failed: {e}", task=t["id"])
    return "\n".join(x for x in s if x)
