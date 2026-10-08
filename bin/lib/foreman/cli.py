"""Usage: bin/foreman goal "<what the owner wants>"   queue a goal (the foreman plans, dispatches, reviews and merges on its own)
       bin/foreman status [--json]                  goals, tasks, workers, orchestrator availability, recent events
       bin/foreman pause | resume                   stop / restart taking new steps (running worker runs are not killed)
       bin/foreman retry <task-id>                  put a blocked task back into the queue (attempt counter reset)
       bin/foreman cancel <goal-id>                 drop a goal and its unfinished tasks
       bin/foreman project "<big request>"         the orchestrator turns it into a roadmap of many small goals in the backlog
       bin/foreman questions [--json]               things the roadmap could not decide without the owner
       echo "<answer>" | bin/foreman answer <n>     answer question n (goes to decisions.md, read by every later plan)
       (goal / project / backlog add accept "-" to read the text from stdin)
       bin/foreman diff <task-id>                   the diff the reviewer saw (or the merged PR's diff)
       bin/foreman projects [--json]                every project on this install (default + projects/<name>/)
       bin/foreman settings [--refresh-models]      editable settings, worker containers and Antigravity models as JSON
       echo '{"KEY": "value"}' | bin/foreman settings apply      validate + write (project keys to its config, limits to etc/limits.conf)
       bin/foreman pack <task>              the context pack a worker would get for this task (no LLM)
       bin/foreman init-project <name> --repo org/repo [--workers worker-5,worker-6] [--lang Slovak] [--vision -  (vision.md from stdin)]
       --project <name> (any command)               act on that project: its config, repository, worker pool and state
       echo "<question>" | bin/foreman ask          answer a question about the project from the repository (one orchestrator call)
       bin/foreman backlog [add "<goal>" | drop <n> | move <n> <to>]   goals waiting for a free worker (taken in order)
       bin/foreman autopilot on|off                 when a worker would idle and the backlog is empty, the orchestrator proposes
                                                    the next goal from the vision file (VISION_FILE, default on)
       bin/foreman run                              the loop itself (systemd: scripts/install-services)
       bin/foreman tick                             one loop iteration in the foreground (debugging)
       bin/foreman --selftest                       pure-function tests, spends nothing

A deterministic loop: no LLM decides WHEN anything happens. LLMs are called only for the three decisions that need judgement:
  plan     goal -> tasks (role, files, spec, acceptance criteria, dependencies)
  review   gated branch -> approve / rework / reject (+ optional mutation checks for risky logic)
  check    all tasks of a goal finished -> goal done, or follow-up tasks
Each call is a fresh, small, tool-less session (no long resumed history) with a stable system prompt (prompts/orchestrator.md +
etc/team-rules.md + the role catalogue from roles/). Providers are tried in the order of PLAN_PROVIDERS / REVIEW_PROVIDERS:
Claude Code, Claude or Gemini via Antigravity in a worker container (the worker account with the most remaining quota; exhausted
accounts are skipped), and optional paid APIs. A provider out of quota is retried after its printed reset.
Worker runs go through bin/dispatch (all guards of etc/limits.conf apply), every branch through bin/review-branch (tests in a clean
container) before any LLM looks at it; merges through bin/open-pr + bin/merge-pr. State: runs/foreman/ (files only)."""
import json, os, re, sys, time

import sbx

from . import ctx
from .store import event, jload, jsave, locked, new_goal_id, save_goal, save_task, state_tx, tasks
from .jobs import INFLIGHT
from .repo import context_pack, refresh_repo, variant
from .llm import Unavailable
from .loop import collect, run_loop, tick
from .report import dashboard_json, list_projects, status, task_diff
from .settings import init_project, models_cached, settings_apply, settings_get
from .owner import answer_question, ask
from .selftest import selftest


def main():
    a = sys.argv[1:]
    if a[:1] == ["--selftest"]:
        sys.exit(selftest())
    if a[:1] == ["--version"]:
        print(open(os.path.join(ctx.ROOT, "VERSION")).read().strip() if os.path.exists(os.path.join(ctx.ROOT, "VERSION")) else "unknown"); return
    # `-` instead of the text: read it from stdin (the dashboard never puts owner text into a command line)
    if (a[:1] in (["goal"], ["project"]) and a[1:2] == ["-"]) or (a[:2] == ["backlog", "add"] and a[2:3] == ["-"]):
        a[a.index("-")] = sys.stdin.read()
    if a[:1] == ["answer"] and len(a) == 2 and a[1].isdigit():
        q = answer_question(int(a[1]), sys.stdin.read())
        if q is None:
            print("no such question", file=sys.stderr); sys.exit(2)
        print(f"answered: {q}"); return
    if a[:2] == ["questions", "--json"]:
        print(json.dumps(jload(os.path.join(ctx.FD, "owner_questions.json"), []), ensure_ascii=False)); return
    if a[:1] == ["goal"] and len(a) == 2 and a[1].strip():
        with locked():
            gid = new_goal_id()
            save_goal(dict(id=gid, text=a[1].strip()[:4000], status="new", created=sbx.now()))
        event(f"goal {gid} queued: {a[1].strip()[:200]}", goal=gid)
        print(f"queued {gid}; the foreman plans it within ~{int(ctx.C['LOOP_SEC'])} s (bin/foreman status)")
    elif a[:1] == ["project"] and len(a) == 2 and a[1].strip():
        with state_tx() as st:
            busy = bool(st.get("roadmap_request"))
            if not busy:
                st["roadmap_request"] = a[1].strip()[:6000]
        if busy:
            print("a roadmap is already being prepared; wait for it (bin/foreman status)", file=sys.stderr); sys.exit(3)
        event(f"project queued for a roadmap: {a[1].strip()[:200]}")
        print("queued; the orchestrator turns it into a roadmap of goals in the backlog (bin/foreman backlog)")
    elif a == ["questions"]:
        qs = jload(os.path.join(ctx.FD, "owner_questions.json"), [])
        print("\n".join(f"{i}. {q}" for i, q in enumerate(qs, 1)) or "(no open questions)")
    elif a[:1] == ["settings"]:
        if a[1:2] == ["apply"]:
            errs = settings_apply(json.loads(sys.stdin.read() or "{}"))
            print(json.dumps({"ok": not errs, "errors": errs})); sys.exit(0 if not errs else 2)
        if "--refresh-models" in a:
            models_cached(refresh=True)
        print(json.dumps(settings_get(), ensure_ascii=False))
    elif a[:1] == ["projects"]:
        ps = list_projects()
        if "--json" in a:
            print(json.dumps(ps, ensure_ascii=False)); return
        for x in ps:
            print(f"{x['name']:16} {x['repo']:30} workers={x['workers'] or 'all':28} {'running' if x['alive'] else 'stopped'}"
                  f"{' paused' if x['paused'] else ''}  tasks={x['tasks']}")
    elif a[:1] == ["init-project"] and len(a) >= 2:
        opt = lambda k, d="": a[a.index(k) + 1] if k in a and a.index(k) + 1 < len(a) else d
        if opt("--workers") and not re.fullmatch(r"(worker-\d+)(,worker-\d+)*", opt("--workers")):
            sys.exit("--workers: comma-separated worker-N names")
        if not re.fullmatch(r"[A-Za-z][A-Za-z ()-]{0,30}", opt("--lang", "English")):
            sys.exit("--lang: a language name such as English or Slovak")
        init_project(a[1], opt("--repo"), opt("--workers"), opt("--lang", "English"),
                     vision=sys.stdin.read()[:20000] if opt("--vision") == "-" else None)
    elif a[:1] == ["pack"] and len(a) == 2:
        t = next((x for x in tasks() if x["id"] == a[1]), None)
        if not t:
            sys.exit(f"no task {a[1]}")
        refresh_repo()
        cp = context_pack(t)
        print(cp)
        print(f"\n({len(cp):,} characters, ~{len(cp) // 4:,} tokens; variant: {variant(dict(t)).get('why')}, "
              f"pack {'on' if variant(dict(t))['pack'] else 'off'} for this task)", file=sys.stderr)
    elif a[:1] == ["diff"] and len(a) == 2:
        print(task_diff(a[1]))
    elif a == ["ask"]:
        q = sys.stdin.read()
        if not q.strip():
            print("usage: echo 'question' | bin/foreman ask", file=sys.stderr); sys.exit(2)
        try:
            text, who = ask(q)
        except Unavailable as e:
            print(f"(no orchestrator available right now: {e})"); sys.exit(3)
        print(text)
    elif a[:1] == ["status"] and "--dashboard" in a:
        print(json.dumps(dashboard_json(), ensure_ascii=False))
    elif a[:1] == ["status"]:
        status("--json" in a)
    elif a[:1] == ["backlog"]:
        p = os.path.join(ctx.FD, "backlog.json")
        with locked():
            bl = jload(p, [])
            if a[1:2] == ["add"] and len(a) == 3 and a[2].strip():
                bl.append(a[2].strip()[:4000]); jsave(p, bl); event(f"backlog +{len(bl)}: {a[2].strip()[:200]}")
            elif a[1:2] == ["drop"] and len(a) == 3 and a[2].isdigit() and 1 <= int(a[2]) <= len(bl):
                x = bl.pop(int(a[2]) - 1); jsave(p, bl); event(f"backlog dropped: {x[:200]}")
            elif a[1:2] == ["move"] and len(a) == 4 and a[2].isdigit() and a[3].isdigit() and 1 <= int(a[2]) <= len(bl) \
                    and 1 <= int(a[3]) <= len(bl):
                x = bl.pop(int(a[2]) - 1); bl.insert(int(a[3]) - 1, x); jsave(p, bl); event(f"backlog: moved to {a[3]}: {x[:200]}")
            elif len(a) != 1:
                print(__doc__, file=sys.stderr); sys.exit(2)
        for i, x in enumerate(bl, 1):
            print(f"{i}. {x}")
        if not bl:
            print("(backlog empty" + (", autopilot proposes goals from " + ctx.C["VISION_FILE"] + ")" if ctx.C["AUTOPILOT"] else ")"))
    elif a[:1] == ["autopilot"] and a[1:] in (["on"], ["off"]):
        f = os.path.join(ctx.FD, "AUTOPILOT_OFF")
        if a[1] == "off":
            open(f, "w").write(sbx.now())
        elif os.path.exists(f):
            os.remove(f)
        with state_tx() as st:
            st.pop("autopilot_quiet_until", None)
        event(f"autopilot {a[1]}")
    elif a == ["pause"]:
        os.makedirs(ctx.FD, exist_ok=True); open(os.path.join(ctx.FD, "PAUSED"), "w").write(sbx.now()); event("paused by " + os.environ.get("USER", "?"))
    elif a == ["resume"]:
        if os.path.exists(os.path.join(ctx.FD, "PAUSED")):
            os.remove(os.path.join(ctx.FD, "PAUSED"))
        event("resumed")
    elif a[:1] == ["retry"] and len(a) == 2:
        t = jload(os.path.join(ctx.FD, "tasks", a[1] + ".json"))
        if not t or t["status"] != "blocked":
            print("no blocked task with that id", file=sys.stderr); sys.exit(2)
        t.update(status="pending", attempts=0, caps=0, review_fails=0, blocked_why=""); save_task(t)
        event(f"task {a[1]} re-queued by the owner", task=a[1])
    elif a[:1] == ["cancel"] and len(a) == 2:
        g = jload(os.path.join(ctx.FD, "goals", a[1] + ".json"))
        if not g:
            print("no such goal", file=sys.stderr); sys.exit(2)
        g["status"] = "cancelled"; save_goal(g)
        for t in tasks():
            if t["goal"] == a[1] and t["status"] in ("pending", "blocked"):
                t["status"] = "cancelled"; save_task(t)
        event(f"goal {a[1]} cancelled (running worker runs finish but are not reviewed)", goal=a[1])
    elif a == ["run"]:
        run_loop()
    elif a == ["tick"]:
        tick()
        while INFLIGHT:
            time.sleep(2); collect()
    else:
        print(__doc__, file=sys.stderr); sys.exit(2)
