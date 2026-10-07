#!/usr/bin/env python3
"""Usage: tools/demo_state.py <agent-forge copy>
Fills a COPY of Agent-Forge with an invented but realistic team state (the demo project "tasks CLI" from examples/demo: goals,
tasks in every state, 24 h of worker runs, live transcripts, metrics, a planner question), so the dashboard can be shown and
photographed without a real install, real accounts or real data (tools/screenshots.sh). Never point it at a real install."""
import json, os, random, sys, time
from datetime import datetime, timezone

root = os.path.abspath(sys.argv[1]) if len(sys.argv) == 2 else sys.exit(__doc__)
if os.path.exists(os.path.join(root, "runs", "ledger.jsonl")) and not os.path.exists(os.path.join(root, ".demo")):
    sys.exit("refusing: this looks like a real install (runs/ledger.jsonl exists)")
random.seed(7)
NOW = time.time()
iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
runs = os.path.join(root, "runs"); fd = os.path.join(runs, "foreman")
for d in ("goals", "tasks", "diffs"):
    os.makedirs(os.path.join(fd, d), exist_ok=True)
open(os.path.join(root, ".demo"), "w").write("demo state from tools/demo_state.py\n")
os.makedirs(os.path.join(root, "etc"), exist_ok=True)
open(os.path.join(root, "etc", "limits.conf"), "w").write(open(os.path.join(root, "etc", "limits.example.conf")).read() +
    "\nMAX_PARALLEL=4\nRUN_TOKEN_CAP=1500000\nAPI_DAILY_BUDGET_USD=10\nAPI_RUN_BUDGET_USD=2\n")
open(os.path.join(root, "etc", "foreman.conf"), "w").write(
    "FORGE_KIND=github\nUPSTREAM_REPO=acme/tasks-cli\nUPSTREAM_URL=https://github.com\nOWNER_LANG=English\nAUTOPILOT=1\n"
    "CONTEXT_PACK=on\nWORKER_MODEL_CLAUDE=sonnet\nNOTIFY_URL=https://ntfy.sh/acme-forge-demo\n")
open(os.path.join(root, "docs", "vision.md"), "w").write(open(os.path.join(root, "examples", "demo", "vision.md")).read())


def save(path, obj):
    json.dump(obj, open(path, "w"), indent=1)


goals = [
    dict(id="G20261007-081500", text="Create the tasks CLI: add, list, done and rm with a JSON store written atomically, with pytest tests.",
         status="done", source="owner", created=iso(NOW - 30 * 3600), finished=iso(NOW - 20 * 3600), rounds=1,
         summary="The CLI covers add/list/done/rm, the store writes atomically and survives a broken file; 41 tests pass.",
         acceptance=["`tasks add` then `tasks list` shows the task with a stable id", "a crash while writing never loses the list",
                     "a broken JSON file gives a clear error, not a traceback"],
         criteria=[dict(criterion="", met=True, evidence="test_cli_roundtrip"), dict(criterion="", met=True, evidence="test_atomic_write_kill"),
                   dict(criterion="", met=True, evidence="test_broken_file_message")], criteria_at=iso(NOW - 20 * 3600)),
    dict(id="G20261008-062000", text="Add due dates to tasks: sorting, overdue marks and a `today` command, with tests.", status="active",
         source="owner", created=iso(NOW - 5 * 3600), rounds=1, notes="Store, CLI and tests are built in parallel against the interface in the specs.",
         acceptance=["`tasks list` sorts by due date and marks overdue tasks", "`tasks today` shows what is due today or overdue",
                     "dates in the past and invalid dates are handled with a clear message"],
         criteria=[]),
    dict(id="G20261008-063000", text="Write docs/USAGE.md: a short user guide with an example session.", status="active", source="autopilot",
         created=iso(NOW - 4.5 * 3600), rounds=1, acceptance=["a new user can install and use every command from the guide alone"], criteria=[]),
]
for g in goals:
    save(os.path.join(fd, "goals", g["id"] + ".json"), g)

T = lambda **k: k
tasks = [
    T(id="0001-store", goal=goals[0]["id"], title="Task store: load/save with atomic writes", role="backend-developer", status="done", worker="worker-1",
      files=["tasks/store.py", "tests/test_store.py"], diff=dict(added=212, removed=0), pr=3, attempts=0, run_tokens=612000),
    T(id="0002-cli", goal=goals[0]["id"], title="CLI: add, list, done, rm", role="backend-developer", status="done", worker="worker-2",
      files=["tasks/cli.py", "tests/test_cli.py"], diff=dict(added=268, removed=4), pr=4, attempts=1, run_tokens=804000),
    T(id="0003-errors", goal=goals[0]["id"], title="Clear errors for a broken or missing file", role="security-tester", status="done", worker="worker-3",
      files=["tests/test_errors.py"], diff=dict(added=96, removed=0), pr=5, attempts=0, run_tokens=388000),
    T(id="0004-due-store", goal=goals[1]["id"], title="Store due dates and sort by them", role="backend-developer", status="done", worker="worker-1",
      files=["tasks/store.py", "tests/test_store.py"], diff=dict(added=88, removed=12), pr=7, attempts=0, run_tokens=455000),
    T(id="0005-due-cli", goal=goals[1]["id"], title="`--due` option, overdue marks in `list`", role="backend-developer", status="running", worker="worker-2",
      files=["tasks/cli.py"], depends_on=["0004-due-store"], attempts=0),
    T(id="0006-today", goal=goals[1]["id"], title="`tasks today` command", role="backend-developer", status="running", worker="worker-4",
      files=["tasks/today.py", "tests/test_today.py"], attempts=0),
    T(id="0007-date-tests", goal=goals[1]["id"], title="Edge cases: past, invalid and timezone-boundary dates", role="tester", status="gate", worker="worker-3",
      files=["tests/test_dates.py"], diff=dict(added=141, removed=0), attempts=0, run_tokens=301000),
    T(id="0008-usage", goal=goals[2]["id"], title="docs/USAGE.md with an example session", role="technical-writer", status="merging", worker="worker-1",
      files=["docs/USAGE.md"], diff=dict(added=119, removed=0), attempts=0, run_tokens=210000,
      upstream_pr=dict(number=9, url="https://github.com/acme/tasks-cli/pull/9")),
    T(id="0009-readme", goal=goals[2]["id"], title="README: install and first steps", role="technical-writer", status="pending",
      files=["README.md"], depends_on=["0008-usage"], attempts=0),
]
spec = ("Add a `due` field (ISO date, optional) to each task in `tasks/store.py`. `load()` keeps old files working (no `due` = None).\n"
        "`list_tasks(include_done=False)` returns tasks sorted by due date, tasks without one last.\n\nInterface used by 0005 and 0006:\n"
        "```python\ndef list_tasks(include_done: bool = False) -> list[Task]\ndef overdue(task: Task, today: date) -> bool\n```")
for i, t in enumerate(tasks):
    t.update(branch=("test/" if t["role"] == "tester" else "feat/") + t["id"], risk="routine", spec=spec, depends_on=t.get("depends_on", []),
             acceptance=["the full test suite passes", "behaviour matches the interface in the spec"], created=iso(NOW - (30 - i) * 3600),
             history=[], planned_by="claude-cli")
    if t["status"] == "done":
        t["finished"] = iso(NOW - (22 - i * 2) * 3600)
        t["history"] = [dict(at=t["finished"], outcome="done", what=f"merged github PR #{t['pr']}", reviewer="claude-cli",
                             feedback="Meets the spec; tests cover the edge cases.")]
    if t["id"] == "0002-cli":
        t["history"].insert(0, dict(at=iso(NOW - 25 * 3600), outcome="rework", feedback="`tasks rm` with an unknown id exits 0; it must exit 1 with a message."))
    if t["status"] in ("running",):
        t.update(dispatched=iso(NOW - random.randint(4, 14) * 60))
    if t["worker"] if "worker" in t else False:
        pass
    save(os.path.join(fd, "tasks", t["id"] + ".json"), t)
open(os.path.join(fd, "diffs", "0004-due-store.diff"), "w").write(
    "diff --git a/tasks/store.py b/tasks/store.py\n@@ -31,6 +31,18 @@ class Task:\n     title: str\n     done: bool = False\n+    due: date | None = None\n"
    "+\n+\n+def overdue(task: Task, today: date) -> bool:\n+    \"\"\"True when the task is open and its due date is before today.\"\"\"\n"
    "+    return not task.done and task.due is not None and task.due < today\n")

save(os.path.join(fd, "backlog.json"), ["Search: `tasks find <text>`, case-insensitive over titles and tags, with tests.",
                                        "Export and import: `tasks export --format csv|json`, `tasks import <file>` skipping duplicates."])
save(os.path.join(fd, "owner_questions.json"), ["Should `tasks today` also list tasks due tomorrow, or strictly today and overdue?"])
save(os.path.join(fd, "state.json"), {})
save(os.path.join(fd, "live.json"), dict(at=NOW + 3600, steps=[dict(key="task:0007-date-tests", since=NOW - 50, phase="review by the orchestrator")],
                                         reserved=[], cooldown={}, unavailable=None))
events = [(NOW - 5 * 3600, "goal G20261008-062000 planned by claude-cli: 4 task(s) - Store, CLI and tests are built in parallel"),
          (NOW - 4.4 * 3600, "task 0004-due-store (backend-developer) dispatched to worker-1 on gemini-3.8-flash-high"),
          (NOW - 3.9 * 3600, "task 0004-due-store done: merged github PR #7 (review: claude-cli)"),
          (NOW - 3.0 * 3600, "maintenance on main 4f2a91c0e3: suite 41 passed / 41 passed / 41 passed - nothing to do"),
          (NOW - 50 * 60, "task 0007-date-tests: worker-3 finished (ok, 301,000 tokens) - testing + review"),
          (NOW - 14 * 60, "task 0005-due-cli (backend-developer) dispatched to worker-2 on gemini-3.8-flash-high"),
          (NOW - 9 * 60, "task 0006-today (backend-developer) dispatched to worker-4 (claude) on sonnet"),
          (NOW - 6 * 60, "task 0008-usage: PR opened on github: https://github.com/acme/tasks-cli/pull/9"),
          (NOW - 2 * 60, "task 0008-usage: github PR #9 not merged yet (waiting for required checks) - checking every 2 min")]
with open(os.path.join(fd, "events.jsonl"), "w") as f:
    for t, m in events:
        f.write(json.dumps(dict(at=iso(t), msg=m)) + "\n")
with open(os.path.join(fd, "llm.jsonl"), "w") as f:
    for i in range(40):
        f.write(json.dumps(dict(at=iso(NOW - i * 1800), kind=random.choice(["review", "review", "plan", "check"]),
                                who=random.choice(["claude", "claude", "agy:claude-sonnet-4-6@worker-2"]), rc=0, secs=random.randint(8, 70),
                                cost_usd=None, usage=None, head="")) + "\n")

# history: a week of merged tasks (for the per-day chart and realistic metrics), then 24 h of worker runs for the timeline -
# about 1.7 runs per merged task, as on the reference install
features = ["tags on tasks", "priority levels", "colored output", "config file", "shell completions", "search by text", "CSV export",
            "JSON import", "undo last change", "archive done tasks", "notes on tasks", "rename a task", "list filters", "stats command",
            "editor integration", "packaging (pyproject)", "man page", "error messages", "locking against two writers", "backup on save"]
roles = ["backend-developer"] * 5 + ["tester", "technical-writer", "security-tester"]
hist_goal = dict(id="G20261001-090000", text="Build out the tasks CLI from the vision (roadmap)", status="done", source="roadmap",
                 created=iso(NOW - 7 * 86400), finished=iso(NOW - 3 * 3600), rounds=2, summary="18 roadmap goals merged.", acceptance=[], criteria=[])
save(os.path.join(fd, "goals", hist_goal["id"] + ".json"), hist_goal)
hist, old_runs = [], []
n = 20
for day in range(6, -1, -1):
    for k in range(random.randint(9, 16) if day else 40):
        n += 1
        fin = NOW - day * 86400 - random.randint(1800, 23 * 3600 if day else 22 * 3600)
        f = features[n % len(features)]
        tid = f"{n:04d}-{f.split()[0]}-{n}"
        t = dict(id=tid, goal=hist_goal["id"], title=f"{f.capitalize()} ({['store', 'CLI', 'tests', 'docs'][n % 4]})", role=roles[n % len(roles)],
                 status="done", worker=f"worker-{n % 4 + 1}", files=[f"tasks/{f.split()[0]}.py"], depends_on=[], attempts=1 if n % 5 == 0 else 0,
                 diff=dict(added=random.randint(40, 420), removed=random.randint(0, 60)), pr=n, finished=iso(fin), created=iso(fin - 7200),
                 branch=f"feat/{tid}", risk="routine", spec=spec, acceptance=["the full test suite passes"], planned_by="claude-cli",
                 history=([dict(at=iso(fin - 3600), outcome="rework", feedback="A test for the empty list is missing.")] if n % 5 == 0 else []) +
                         [dict(at=iso(fin), outcome="done", what=f"merged github PR #{n}", reviewer="claude-cli", feedback="Meets the spec.")])
        save(os.path.join(fd, "tasks", tid + ".json"), t)
        if day == 0:
            hist.append(t)
        else:                                   # older runs: ledger lines only (tokens for the metrics)
            old_runs.extend([(t, fin - random.randint(1, 3) * 3600)] * (2 if t["attempts"] else 1))

models = {"worker-1": ("agy", "gemini-3.8-flash-high"), "worker-2": ("agy", "gemini-3.8-flash-high"),
          "worker-3": ("agy", "gemini-3.8-flash-high"), "worker-4": ("claude", "sonnet")}
plan = [t for t in hist for _ in range(2 if t["attempts"] else 1)] + [t for t in hist if random.random() < 0.3]
random.shuffle(plan)
with open(os.path.join(runs, "ledger.jsonl"), "w") as led:
    clock = {w: NOW - 23.5 * 3600 + random.randint(0, 3600) for w in models}
    for t in plan:
        w = min(clock, key=clock.get)
        start = clock[w]
        if start > NOW - 45 * 60:
            break
        rt, model = models[w]
        dur = random.randint(9, 24) * 60
        reason = random.choices(["ok", "token-cap", "failed"], [0.86, 0.11, 0.03])[0]
        tok = random.randint(320, 880) * 1000
        rid = datetime.fromtimestamp(start, timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + w
        cost = round(tok / 1e6 * 0.32, 2) if rt == "claude" else None
        checks = random.choices([[True], [False, True], [False, False]], [0.75, 0.2, 0.05])[0]
        e = dict(run_id=rid, date=iso(start + dur)[:10], at=iso(start + dur), worker=w, task=t["id"] + ".md", test=False, reason=reason,
                 exit_code=0, tokens=tok, duration_s=dur, tool_calls=random.randint(25, 70), egress_alarms=0, runtime=rt, cost_usd=cost, project="")
        led.write(json.dumps(e) + "\n")
        d = os.path.join(runs, rid); os.makedirs(d, exist_ok=True)
        pct = 80 - random.random()
        save(os.path.join(d, "meta.json"), dict(run_id=rid, worker=w, task=t["id"] + ".md", runtime=rt, model=model, variant="pack-default",
                                                 reason=reason, tokens=tok, tool_calls=e["tool_calls"], cost_usd=cost, project="",
                                                 quota_before=[dict(model="Gemini Models", label="Weekly Limit Remaining", pct=round(pct + 0.8, 1), reset="")],
                                                 quota_after=[dict(model="Gemini Models", label="Weekly Limit Remaining", pct=round(pct, 1), reset="")],
                                                 self_checks=[dict(ok=c) for c in checks]))
        open(os.path.join(d, "finished"), "w").write(iso(start + dur))
        os.utime(os.path.join(d, "finished"), (start + dur, start + dur))
        clock[w] = start + dur + random.randint(30, 110) * 60
    for t, at in old_runs:
        led.write(json.dumps(dict(run_id=f"old-{t['id']}", date=iso(at)[:10], at=iso(at), worker=t["worker"], task=t["id"] + ".md", test=False,
                                  reason="ok", exit_code=0, tokens=random.randint(320, 880) * 1000, duration_s=900, tool_calls=40, egress_alarms=0,
                                  runtime="agy", cost_usd=None, project="")) + "\n")

# the runs in progress: live transcripts (agy format for worker-2, Claude Code format for worker-4)
def live_run(w, minutes_ago, lines):
    rid = datetime.fromtimestamp(NOW - minutes_ago * 60, timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + w
    d = os.path.join(runs, rid); os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "stream.jsonl"), "w").write("\n".join(json.dumps(x) for x in lines) + "\n")
    save(os.path.join(d, "meta.json"), dict(run_id=rid, worker=w, runtime=models[w][0], model=models[w][1], project=""))
    open(os.path.join(d, "started"), "w").write(iso(NOW - minutes_ago * 60))


su = lambda **k: {"event": "step_update", "step_update": k}
live_run("worker-2", 14, [
    su(step_type="user_input", text="# Task 0005-due-cli - `--due` option, overdue marks in `list`", state="DONE"),
    su(step_index=1, step_type="agent_response", text_delta="I'll read the store interface from the context pack and add the option to the CLI.", state="DONE"),
    su(step_index=2, step_type="tool", tool_info={"name": "run_command", "parameters": {"CommandLine": "git checkout -b feat/0005-due-cli origin/main"}}, state="ACTIVE"),
    su(step_index=2, step_type="tool", state="DONE", duration_seconds=0.4),
    su(step_index=3, step_type="tool", tool_info={"name": "edit_file", "parameters": {"command": "tasks/cli.py: add --due YYYY-MM-DD, mark overdue rows with !"}}, state="ACTIVE"),
    su(step_index=3, step_type="tool", state="DONE", duration_seconds=1.1),
    su(step_index=4, step_type="tool", tool_info={"name": "run_command", "parameters": {"CommandLine": "python3 -m pytest -q tests/test_cli.py"}}, state="ACTIVE"),
    su(step_index=4, step_type="tool", state="DONE", duration_seconds=3.7),
    su(step_index=5, step_type="agent_response", text_delta="All 23 CLI tests pass. Next: overdue marks in `list` and two tests for them.", state="DONE"),
    su(step_index=6, step_type="tool", tool_info={"name": "edit_file", "parameters": {"command": "tests/test_cli.py: test_list_marks_overdue, test_due_option_validates"}}, state="ACTIVE"),
])
live_run("worker-4", 9, [
    {"type": "system", "subtype": "init", "session_id": "demo-1"},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "Starting with tests/test_today.py: due today, overdue, done tasks excluded."}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Write", "input": {"file_path": "tests/test_today.py"}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "content": "File created successfully"}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "python3 -m pytest -q tests/test_today.py"}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "content": "3 failed in 0.21s (tasks/today.py does not exist yet)"}]}},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "Tests fail as expected. Now tasks/today.py with `due_today(tasks, today)`."}]}},
])
# what agy /usage reports for the subscription workers (the fake docker of tools/screenshots.sh prints it)
open(os.path.join(root, ".demo-usage"), "w").write("Gemini Models\tFive Hour Limit Remaining\t72%\tResets in 2h\nGemini Models\tWeekly Limit Remaining\t61%\tResets in 4d\n")
print(f"demo state written to {root}")
