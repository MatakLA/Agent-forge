"""Usage: bin/foreman goal "<what the owner wants>"   queue a goal (the foreman plans, dispatches, reviews and merges on its own)
       bin/foreman status [--json]                  goals, tasks, workers, orchestrator availability, recent events
       bin/foreman pause | resume                   stop / restart taking new steps (running worker runs are not killed)
       bin/foreman task <task-id> show              the task, each of its runs and what it cost (JSON)
       bin/foreman task <task-id> approve | cancel | retry        approve a waiting task / stop and drop it / queue a blocked one again
       echo "<words>" | bin/foreman task <task-id> feedback -     send its work back with your words (or add them to its spec)
       bin/foreman task <task-id> assign <worker-N|any>  |  model <model|default>     who / which model takes it next
       bin/foreman goal <goal-id> show              the goal's tasks, events and cost (JSON)
       bin/foreman goal <goal-id> approve | pause | resume | prioritize | cancel
       echo "<note>" | bin/foreman goal <goal-id> replan -        plan it again with your note   (edit -: new text before it starts)
       echo "<words>" | bin/foreman search          goals, tasks, events and transcripts containing the words (JSON)
       bin/foreman money                            spend per day / runtime / project / model, caps, quota burn (JSON)
       bin/foreman doc list | get <name> | reset <role:slug>    echo "<text>" | bin/foreman doc set <name>   (vision, team-rules,
                                                    decisions, lessons, role:<slug> - a role is saved as this project's own version)
       bin/foreman disk                             what takes the space (JSON)   bin/foreman cleanup compress|transcripts|docker [days]
       bin/foreman retry <task-id> | cancel <goal-id>          older spellings of `task <id> retry` / `goal <id> cancel`
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
       bin/foreman backlog [add "<goal>" | drop <n> | move <n> <to> | top <n> | edit <n> -]   goals waiting for a free worker (in order)
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
from .report import dashboard_json, goal_detail, list_projects, money, search, status, task_detail, task_diff
from .knowledge import doc_get, doc_list, doc_reset, doc_set
from .maintenance import cleanup, disk_usage
from .settings import init_project, models_cached, settings_apply, settings_get
from .owner import GOAL_ID, TASK_ID, answer_question, ask, goal_action, task_action
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
    # the owner's actions on one task / goal, and read-only detail views (JSON for the dashboard)
    if a[:1] == ["task"] and len(a) >= 3 and TASK_ID.fullmatch(a[1]):
        if a[2] == "show":
            d = task_detail(a[1])
            if d is None:
                print("no such task", file=sys.stderr); sys.exit(2)
            print(json.dumps(d, ensure_ascii=False)); return
        arg = a[3] if len(a) > 3 else ""
        text = sys.stdin.read() if arg == "-" else ""
        target = "" if arg in ("any", "default", "-") else arg
        ok, msg = task_action(a[1], a[2], text=text, target=target)
        print(json.dumps({"ok": ok, "message": msg})); sys.exit(0 if ok else 2)
    if a[:1] == ["goal"] and len(a) >= 3 and GOAL_ID.fullmatch(a[1]):
        if a[2] == "show":
            d = goal_detail(a[1])
            if d is None:
                print("no such goal", file=sys.stderr); sys.exit(2)
            print(json.dumps(d, ensure_ascii=False)); return
        ok, msg = goal_action(a[1], a[2], text=sys.stdin.read() if a[3:4] == ["-"] else "")
        print(json.dumps({"ok": ok, "message": msg})); sys.exit(0 if ok else 2)
    if a == ["search"]:
        print(json.dumps(search(sys.stdin.read()), ensure_ascii=False)); return
    if a == ["money"]:
        print(json.dumps(money(), ensure_ascii=False)); return
    if a[:1] == ["doc"] and len(a) in (2, 3):
        if a[1] == "list":
            print(json.dumps(doc_list(), ensure_ascii=False)); return
        if a[1] == "get" and len(a) == 3:
            d = doc_get(a[2])
            if d is None:
                print("unknown document", file=sys.stderr); sys.exit(2)
            print(json.dumps(d, ensure_ascii=False)); return
        if a[1] in ("set", "reset") and len(a) == 3:
            err = doc_set(a[2], sys.stdin.read()) if a[1] == "set" else doc_reset(a[2])
            print(json.dumps({"ok": not err, "message": err or ("saved" if a[1] == "set" else "back to the install's version")}))
            sys.exit(2 if err else 0)
    if a == ["disk"]:
        print(json.dumps(disk_usage())); return
    if a[:1] == ["cleanup"] and len(a) in (2, 3) and (len(a) == 2 or a[2].isdigit()):
        print(json.dumps({"ok": True, "message": cleanup(a[1], int(a[2]) if len(a) == 3 else None)})); return
    if a[:2] == ["backlog", "edit"] and len(a) == 4 and a[2].isdigit() and a[3] == "-":
        a[3] = sys.stdin.read()
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
            elif a[1:2] == ["top"] and len(a) == 3 and a[2].isdigit() and 1 <= int(a[2]) <= len(bl):
                x = bl.pop(int(a[2]) - 1); bl.insert(0, x); jsave(p, bl); event(f"backlog: to the top: {x[:200]}")
            elif a[1:2] == ["edit"] and len(a) == 4 and a[2].isdigit() and 1 <= int(a[2]) <= len(bl) and a[3].strip():
                bl[int(a[2]) - 1] = a[3].strip()[:4000]; jsave(p, bl); event(f"backlog {a[2]} edited: {a[3].strip()[:200]}")
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
    elif a[:1] == ["retry"] and len(a) == 2:                 # older spelling of `task <id> retry`
        ok, msg = task_action(a[1], "retry")
        print(msg, file=sys.stdout if ok else sys.stderr); sys.exit(0 if ok else 2)
    elif a[:1] == ["cancel"] and len(a) == 2:                # older spelling of `goal <id> cancel`
        ok, msg = goal_action(a[1], "cancel")
        print(msg, file=sys.stdout if ok else sys.stderr); sys.exit(0 if ok else 2)
    elif a == ["run"]:
        run_loop()
    elif a == ["tick"]:
        tick()
        while INFLIGHT:
            time.sleep(2); collect()
    else:
        print(__doc__, file=sys.stderr); sys.exit(2)
