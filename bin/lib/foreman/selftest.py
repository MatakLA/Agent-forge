"""Pure-function and small integration tests of the foreman; spend nothing (`bin/foreman --selftest`)."""
import datetime as dt, json, os, subprocess, time

import sbx, runtimes

from . import ctx
from .util import extract_json, norm_tasks, parse_limit, parse_numstat, ready_tasks, sh, slugify
from .store import goals, jload, new_goal_id, save_goal, tasks
from .pool import has_quota, order_by_quota, quota_pct
from .repo import CODEMAP, context_pack, own_files, variant
from .knowledge import role_catalogue, role_playbook
from .prompts import worker_spec
from .merge import auto_rebase
from .maintenance import MAINT_PREFIX, _maintenance_findings
from .loop import orphaned
from .report import efficiency, task_diff, team_metrics
from .settings import settings_apply


def selftest():
    import shutil, tempfile
    bad = 0

    def check(name, ok):
        nonlocal bad
        bad += not ok
        print(("ok   " if ok else "FAIL ") + name)
    now = dt.datetime(2026, 10, 2, 12, 0, tzinfo=dt.timezone.utc).timestamp()      # 14:00 in Prague
    r = parse_limit("You've hit your session limit · resets 2:40pm (Europe/Prague)", now)
    check("session limit later today", r == dt.datetime(2026, 10, 2, 12, 42, tzinfo=dt.timezone.utc).timestamp())
    r = parse_limit("You've hit your limit · resets 1pm (Europe/Prague)", now)
    check("reset time already passed -> tomorrow", r == dt.datetime(2026, 10, 3, 11, 2, tzinfo=dt.timezone.utc).timestamp())
    r = parse_limit("Weekly limit reached ∙ resets Oct 6, 9am (Europe/Prague)", now)
    check("weekly limit with a date", r == dt.datetime(2026, 10, 6, 7, 2, tzinfo=dt.timezone.utc).timestamp())
    check("limit without a parseable time -> 60 min", parse_limit("usage limit reached", now) == now + 3600)
    check("normal text is not a limit", parse_limit("All tests pass, resets the counter at 3pm", now) is None)
    check("json inside prose and fences", extract_json('Sure:\n```json\n{"verdict": "approve", "feedback": "ok {fine}"}\n```') ==
          {"verdict": "approve", "feedback": "ok {fine}"})
    check("json: invalid first object, valid second", extract_json('{bad} then {"a": 1}') == {"a": 1})
    check("no json -> None", extract_json("nothing here") is None)
    ts = [dict(id="0001-a", status="done", files=["x.py"]), dict(id="0002-b", status="pending", depends_on=["0001-a"], files=["y.py"]),
          dict(id="0003-c", status="pending", depends_on=["0002-b"], files=[]), dict(id="0004-d", status="pending", files=["z.py"]),
          dict(id="0005-e", status="pending", files=["y.py"], not_before=time.time() + 99)]
    check("ready: deps done, not deferred", [t["id"] for t in ready_tasks(ts, set())] == ["0002-b", "0004-d"])
    check("ready: file overlap with a running task waits", [t["id"] for t in ready_tasks(ts, {"z.py"})] == ["0002-b"])
    ts2 = [dict(id="0006-f", status="pending", files=["a.py", "tools/registry.json"])]
    ctx.C["SHARED_FILES"] = "tools/registry.json"
    check("ready: a shared registry file does not block", [t["id"] for t in ready_tasks(ts2, {"b.py", "tools/registry.json"})] == ["0006-f"])
    check("slugify", slugify("Spending Alert: Firefly!") == "spending-alert-firefly")
    check("numstat: adds and removes summed, binary files ignored",
          parse_numstat("12\t3\ta.py\n0\t7\tb.py\n-\t-\timg.png\n") == (12, 10))
    check("numstat: empty diff", parse_numstat("") == (0, 0))
    rows = [dict(model="Gemini Models", label="Five Hour Limit Remaining", pct=24.0), dict(model="Gemini Models", label="Weekly Limit Remaining", pct=48.0),
            dict(model="Claude and GPT models", label="Five Hour Limit Remaining", pct=100.0)]
    check("quota_pct picks the family and window", quota_pct(rows, "gemini") == 24.0 and quota_pct(rows, "claude") == 100.0 and quota_pct(rows, "x") == -1)
    dead = [dict(model="Claude and GPT models", label="Weekly Limit Remaining", pct=0.0)]   # 5h row shows "disabled" -> not parsed
    check("has_quota: exhausted weekly skipped, healthy kept, unknown kept",
          not has_quota(dead, "claude") and has_quota(rows, "claude") and has_quota([], "claude") and not has_quota(rows[:1] + [dict(model="Gemini Models", label="Weekly Limit Remaining", pct=1.0)], "gemini"))
    t_now = 1_700_000_000
    check("parse_limit: agy 'Individual quota reached ... Resets in 110h11m52s'",
          parse_limit("Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 110h11m52s.", t_now) == t_now + 110 * 3600 + 11 * 60 + 52 + 120)
    pcts = {"worker-1": 24, "worker-2": 79, "worker-3": 100, "worker-4": 100}
    check("order_by_quota: most quota first, ties by least recently used",
          order_by_quota(list(pcts), pcts.get, {"worker-3": 50, "worker-4": 10}) == ["worker-4", "worker-3", "worker-2", "worker-1"])
    mts = [dict(id="0001-a", role="backend developer", status="done", history=[dict(outcome="rework"), dict(outcome="done")], diff=dict(added=10, removed=2)),
           dict(id="0002-b", role="Backend Developer", status="done", history=[dict(outcome="done")], diff=dict(added=5, removed=0)),
           dict(id="0003-c", role="tester", status="blocked", history=[dict(outcome="rework"), dict(outcome="blocked")]),
           dict(id="0004-d", role="tester", status="cancelled", history=[])]
    mm = team_metrics(mts)
    check("team_metrics: roles merged by slug, first-pass %, reworks, cancelled ignored",
          mm["roles"]["backend-developer"]["done"] == 2 and mm["roles"]["backend-developer"]["first_pass_pct"] == 50
          and mm["roles"]["backend-developer"]["lines_done"] == 17 and mm["roles"]["tester"]["tasks"] == 1
          and mm["totals"]["reworks"] == 2 and mm["totals"]["blocked"] == 1)
    check("task_diff rejects ids that are not task ids", task_diff("../../etc/passwd") == "(invalid task id)" and task_diff("x; rm -rf /") == "(invalid task id)")
    # auto_rebase against real git repositories (a local bare repo stands in for the forge)
    old_fd, tmp = ctx.FD, tempfile.mkdtemp(prefix="foreman-rebase-")
    old_url, old_repo, old_sec, old_shared = sbx.FORGE_URL, sbx.FORGE_REPO, sbx.SECRETS, ctx.C["SHARED_FILES"]
    try:
        ctx.FD = os.path.join(tmp, "fd"); os.makedirs(ctx.FD)
        sbx.FORGE_URL, sbx.FORGE_REPO, sbx.SECRETS = "file://" + tmp, "o/r", tmp
        open(os.path.join(tmp, "orchestrator.token"), "w").write("x")
        ctx.C["SHARED_FILES"] = "reg.jsonl"
        G = lambda *a, cwd=os.path.join(tmp, "w"): subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd,
                                                                    capture_output=True, text=True)
        os.makedirs(os.path.join(tmp, "o")); G("init", "-q", "--bare", "-b", "main", os.path.join(tmp, "o", "r.git"), cwd=tmp)
        G("clone", "-q", os.path.join(tmp, "o", "r.git"), "w", cwd=tmp)
        w = os.path.join(tmp, "w")
        open(os.path.join(w, "reg.jsonl"), "w").write('{"a":1}\n'); open(os.path.join(w, "code.py"), "w").write("x = 1\n")
        G("add", "-A"); G("commit", "-qm", "base"); G("push", "-q", "origin", "HEAD:main")
        G("checkout", "-qb", "feat/1"); open(os.path.join(w, "reg.jsonl"), "a").write('{"b":2}\n'); G("commit", "-qam", "b"); G("push", "-q", "origin", "feat/1")
        G("checkout", "-qb", "feat/2", "main"); open(os.path.join(w, "code.py"), "w").write("x = 2\n"); G("commit", "-qam", "x2"); G("push", "-q", "origin", "feat/2")
        G("checkout", "-q", "main"); open(os.path.join(w, "reg.jsonl"), "a").write('{"c":3}\n')
        open(os.path.join(w, "code.py"), "w").write("x = 3\n"); G("commit", "-qam", "c"); G("push", "-q", "origin", "main")
        r1 = auto_rebase("feat/1")
        G("fetch", "-q")
        merged = G("show", "origin/feat/1:reg.jsonl").stdout
        anc = G("merge-base", "--is-ancestor", "origin/main", "origin/feat/1").returncode == 0
        check("auto_rebase: append-conflict in a shared .jsonl is merged keeping both sides, branch now on top of main",
              r1 == "rebased" and anc and all(k in merged for k in ('"a"', '"b"', '"c"')))
        check("auto_rebase: a real code conflict is refused (worker resolves it)", auto_rebase("feat/2") == "conflict")
        check("auto_rebase: already up to date / missing branch", auto_rebase("feat/1") == "uptodate" and auto_rebase("feat/nope") == "missing")
    finally:
        ctx.FD = old_fd
        sbx.FORGE_URL, sbx.FORGE_REPO, sbx.SECRETS, ctx.C["SHARED_FILES"] = old_url, old_repo, old_sec, old_shared
        shutil.rmtree(tmp, ignore_errors=True)
    old_root, old_fd, st_dir = ctx.ROOT, ctx.FD, tempfile.mkdtemp(prefix="foreman-settings-")
    os.makedirs(os.path.join(st_dir, "etc")); ctx.FD = os.path.join(st_dir, "fd"); os.makedirs(ctx.FD)
    open(os.path.join(st_dir, "etc", "foreman.conf"), "w").write("# keep me\nCLAUDE_MODEL=sonnet\nAUTOPILOT=1\n")
    open(os.path.join(st_dir, "etc", "limits.conf"), "w").write("# limits\nMAX_PARALLEL=4\n")
    ctx.ROOT = st_dir
    try:
        errs = settings_apply({"CLAUDE_MODEL": "x; rm -rf /", "MAX_PARALLEL": "999", "NOPE": "1", "WORKERS": "worker-1,evil"})
        check("settings_apply rejects shell-ish values, out-of-range numbers, unknown keys, bad pools - and writes nothing",
              set(errs) == {"CLAUDE_MODEL", "MAX_PARALLEL", "NOPE", "WORKERS"} and "x; rm" not in open(os.path.join(st_dir, "etc", "foreman.conf")).read())
        ok_ = settings_apply({"CLAUDE_MODEL": "opus", "WORKERS": "worker-1,worker-3", "WORKER_MODEL": "gemini-3.1-pro-high", "MAX_PARALLEL": "8",
                              "OWNER_LANG": "Slovak"})
        fc, lc = open(os.path.join(st_dir, "etc", "foreman.conf")).read(), open(os.path.join(st_dir, "etc", "limits.conf")).read()
        check("settings_apply writes project keys in place (comments kept) and limits to etc/limits.conf",
              not ok_ and "# keep me" in fc and "CLAUDE_MODEL=opus" in fc and "CLAUDE_MODEL=sonnet" not in fc and "WORKERS=worker-1,worker-3" in fc
              and "MAX_PARALLEL=8" in lc and "MAX_PARALLEL=4" not in lc and "MAX_PARALLEL" not in fc)
    finally:
        ctx.ROOT, FD = old_root, old_fd
        shutil.rmtree(st_dir, ignore_errors=True)
    ct = dict(id="0009-x", title="t", role="tester", branch="test/0009-x", goal="G1", spec="SPEC-TEXT", acceptance=["a"], files=["f.py"],
              attempts=1, feedback="add a test for empty input", last_run=dict(reason="token-cap", salvage=dict(rc=0)))
    full, short = worker_spec(ct), worker_spec(ct, continuing=True)
    check("worker_spec: continuation prompt is short, keeps feedback + rules, drops the spec; WIP note after a token cap",
          "SPEC-TEXT" in full and "SPEC-TEXT" not in short and "add a test for empty input" in short and "COMMIT AND PUSH" in short
          and "auto-saved as a WIP commit" in short and len(short) < len(full))
    old_root, rd = ctx.ROOT, tempfile.mkdtemp(prefix="foreman-roles-")
    os.makedirs(os.path.join(rd, "roles"))
    open(os.path.join(rd, "roles", "backend-developer.md"), "w").write("# Backend developer\n**Mission:** build it\n## Definition of done\n- tests\n")
    open(os.path.join(rd, "roles", "security-tester.md"), "w").write("# Security tester\n**Mission:** break it\n")
    open(os.path.join(rd, "roles", "tester.md"), "w").write("# Tester\n**Mission:** find bugs\n")
    ctx.ROOT = rd
    try:
        check("role_playbook: exact slug, free-text name, unknown role",
              role_playbook("backend-developer").startswith("# Backend") and role_playbook("Security Tester").startswith("# Security")
              and role_playbook("QA tester").startswith("# Tester")
              and role_playbook("astronaut") == "")
        check("role_catalogue lists slug, title and mission", "`backend-developer` - Backend developer: build it" in role_catalogue())
    finally:
        ctx.ROOT = old_root
        shutil.rmtree(rd, ignore_errors=True)
    d = tempfile.mkdtemp(prefix="foreman-tasks-")
    os.makedirs(os.path.join(d, "tasks"))
    json.dump([{"name": "m1"}], open(os.path.join(d, "tasks", "0001-a.mutations.json"), "w"))     # a list, not a task dict
    json.dump({"id": "0002-b", "status": "pending"}, open(os.path.join(d, "tasks", "0002-b.json"), "w"))
    open(os.path.join(d, "tasks", "0003-c.md"), "w").write("not json at all")
    old_fd = ctx.FD
    ctx.FD = d
    try:
        check("tasks(): a sibling *.mutations.json (a list) and a *.md spec do not crash the loader", [t["id"] for t in tasks()] == ["0002-b"])
    finally:
        ctx.FD = old_fd
        shutil.rmtree(d, ignore_errors=True)
    # variants: context pack A/B splits tasks roughly in half and is stable per task; model routing
    saved = {k: ctx.C[k] for k in ("CONTEXT_PACK", "WORKER_MODEL", "WORKER_MODEL_RISKY", "WORKER_MODEL_LIGHT", "AB_MODEL", "AB_SHARE", "LIGHT_ROLES",
                               "SHARED_FILES", "PROJECT_DIR", "CONTEXT_PACK_CHARS")}
    try:
        ctx.C.update(CONTEXT_PACK="ab", WORKER_MODEL="m-default", WORKER_MODEL_RISKY="m-risky", WORKER_MODEL_LIGHT="m-light", AB_MODEL="m-ab",
                 AB_SHARE=30, LIGHT_ROLES="tester,technical-writer")
        vs = [variant(dict(id=f"{i:04d}-t", role="backend developer", risk="routine")) for i in range(400)]
        packs, abs_ = sum(v["pack"] for v in vs), sum(v["model"] == "m-ab" for v in vs)
        t1 = dict(id="0001-x", role="tester", risk="routine")
        check("variant: ab packs ~half the tasks, AB_SHARE ~30 % on the A/B model, stable per task",
              150 < packs < 250 and 80 < abs_ < 160 and variant(dict(t1)) == variant(dict(t1)))
        check("variant: risky -> risky model, light role -> light model (unless in the A/B share)",
              variant(dict(id="0002-y", role="backend developer", risk="risky"))["model"] == "m-risky" and
              all(v["model"] in ("m-light", "m-ab") for v in (variant(dict(id=f"{i:04d}-z", role="Tester", risk="routine")) for i in range(20)))
              and variant(dict(id="0003-s", role="security tester", risk="routine"))["model"] in ("m-default", "m-ab")
              and any(variant(dict(id=f"{i:04d}-z", role="tester", risk="routine"))["model"] == "m-light" for i in range(20)))
        ctx.C.update(CONTEXT_PACK="off"); check("variant: CONTEXT_PACK=off never packs", not any(variant(dict(id=f"{i:04d}-q", role="x"))["pack"] for i in range(50)))
        ctx.C.update(SHARED_FILES="tools.json")
        check("own_files ignores shared registries", own_files(dict(files=["a.py", "test_a.py", "pkg/tools.json", "tools.json"])) == ["a.py", "test_a.py"])
    finally:
        ctx.C.update(saved)
    # context pack: a throwaway git repository as "main"
    old_repo, old_fd = ctx.REPO, ctx.FD
    d = tempfile.mkdtemp(prefix="forge-pack-")
    try:
        ctx.REPO, FD = os.path.join(d, "repo"), os.path.join(d, "fd")
        os.makedirs(ctx.REPO); os.makedirs(ctx.FD)
        open(os.path.join(ctx.REPO, "store.py"), "w").write('"""Contact storage: add/list contacts in a JSON file."""\n\ndef add(name: str) -> dict:\n'
                                                         '    """Add a contact."""\n    return {"name": name}\n')
        open(os.path.join(ctx.REPO, "birthdays.py"), "w").write('"""Birthday reminders."""\nimport store\n\ndef upcoming(days=7):\n    return []\n')
        open(os.path.join(ctx.REPO, "test_birthdays.py"), "w").write('import birthdays\n\ndef test_up():\n    assert birthdays.upcoming() == []\n')
        open(os.path.join(ctx.REPO, "other.py"), "w").write("def unrelated():\n    pass\n")
        for c in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
            sh(["git"] + c, cwd=ctx.REPO)
        sh(["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=ctx.REPO)
        ctx.C.update(PROJECT_DIR="", CONTEXT_PACK_CHARS=60000)
        cp = context_pack(dict(id="0003-b", files=["birthdays.py", "test_birthdays.py"], spec="Use store.add() for new contacts.", acceptance=[]))
        check("context pack: own files in full, imported module's interface, one-line map of the rest, no bodies of others",
              "def upcoming(days=7):" in cp and "def add(name: str) -> dict  # Add a contact." in cp and "`other.py`" in cp
              and 'return {"name": name}' not in cp and "NO MODULE DOCSTRING" in cp)
        cp2 = context_pack(dict(id="0003-b", files=["birthdays.py"], spec="", acceptance=[], attempts=1))
        check("context pack: a rework does not resend main's version of the worker's own files", "def upcoming(days=7):\n" not in cp2 and "git diff origin/main" in cp2)
        ctx.C.update(CONTEXT_PACK_CHARS=400)
        check("context pack respects CONTEXT_PACK_CHARS", len(context_pack(dict(id="0003-b", files=["birthdays.py"], spec="", acceptance=[]))) < 1100)
    finally:
        ctx.C.update(saved)
        ctx.REPO, FD = old_repo, old_fd
        CODEMAP.clear()
        shutil.rmtree(d, ignore_errors=True)
    # efficiency metrics: quota per merged task by model / pack
    rs = {"r1": dict(task="0001-a", model="m1", variant="pack-default", reason="ok", tokens=100, tools=10, week=1.0, checks=[False, True]),
          "r2": dict(task="0001-a", model="m1", variant="pack-default", reason="token-cap", tokens=300, tools=50, week=2.0, checks=[]),
          "r3": dict(task="0002-b", model="m2", variant="nopack-default", reason="ok", tokens=200, tools=40, week=None, checks=[True])}
    ts = [dict(id="0001-a", status="done", history=[dict(outcome="rework")]), dict(id="0002-b", status="done", history=[])]
    ef = efficiency(ts, rs)
    check("efficiency: per model and pack - runs, cap %, quota per run and per merged task, first pass, self-check fixes",
          ef["model"]["m1"]["runs"] == 2 and ef["model"]["m1"]["cap_pct"] == 50 and ef["model"]["m1"]["week_per_run"] == 1.5
          and ef["model"]["m1"]["week_per_done"] == 3.0 and ef["pack"]["no pack"]["first_pass_pct"] == 100
          and ef["pack"]["pack"]["first_pass_pct"] == 0 and ef["self_check"] == dict(runs=2, green_first=1, fixed=1, still_red=0))
    # runtimes: the event streams as the real CLIs print them (claude 2.1, codex 0.161, gemini 0.63; agy 1.2)
    cl = ['{"type":"system","subtype":"init","session_id":"S1","tools":["Bash"]}',
          '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"ls"}}],'
          '"usage":{"input_tokens":1000,"output_tokens":1,"cache_creation_input_tokens":200,"cache_read_input_tokens":300}},"session_id":"S1"}',
          '{"type":"user","message":{"content":[{"tool_use_id":"t1","type":"tool_result","content":"x"}]},"session_id":"S1"}',
          '{"type":"result","subtype":"success","is_error":false,"result":"Done.","session_id":"S1","total_cost_usd":0.0089,'
          '"usage":{"input_tokens":2000,"cache_creation_input_tokens":400,"cache_read_input_tokens":600,"output_tokens":84}}']
    a = runtimes.scan("claude", cl)
    check("runtimes: claude - session, tool calls, tokens without cache reads, cost, success",
          a["conversation_id"] == "S1" and a["tools"] == 1 and a["tokens"] == 2484 and a["result"]["status"] == "SUCCESS"
          and a["result"]["cost_usd"] == 0.0089)
    b = runtimes.scan("claude", cl[:3] + ['{"type":"result","subtype":"error_max_budget_usd","is_error":true,"session_id":"S1"}'])
    check("runtimes: claude - a budget stop is an error with its subtype", b["result"]["status"] == "ERROR" and b["result"]["subtype"] == "error_max_budget_usd")
    cx = ['{"type":"thread.started","thread_id":"TH1"}', '{"type":"turn.started"}',
          '{"type":"item.started","item":{"id":"i1","type":"command_execution","command":"ls","status":"in_progress"}}',
          '{"type":"item.completed","item":{"id":"i1","type":"command_execution","command":"ls","exit_code":0,"status":"completed"}}',
          '{"type":"item.completed","item":{"id":"i2","type":"agent_message","text":"Done."}}',
          '{"type":"turn.completed","usage":{"input_tokens":3000,"cached_input_tokens":1000,"output_tokens":120}}']
    c = runtimes.scan("codex", cx, "1.25,0.125,10")
    check("runtimes: codex - thread id, one tool, tokens minus cache, estimated cost, success",
          c["conversation_id"] == "TH1" and c["tools"] == 1 and c["tokens"] == 2120 and c["result"]["status"] == "SUCCESS"
          and abs(c["result"]["cost_usd"] - (2000 * 1.25 + 1000 * 0.125 + 120 * 10) / 1e6) < 1e-9)
    check("runtimes: codex - turn.failed is an error with its message",
          runtimes.scan("codex", cx[:2] + ['{"type":"turn.failed","error":{"message":"Missing environment variable"}}'])["result"]["error"] == "Missing environment variable")
    gm = ['{"type":"init","timestamp":"t","session_id":"G1","model":"m"}', '{"type":"message","timestamp":"t","role":"user","content":"do it"}',
          '{"type":"tool_use","timestamp":"t","tool_name":"run_shell_command","tool_id":"x","parameters":{"command":"ls"}}',
          '{"type":"tool_result","timestamp":"t","tool_id":"x","status":"success","output":""}',
          '{"type":"message","timestamp":"t","role":"assistant","content":"Do","delta":true}',
          '{"type":"message","timestamp":"t","role":"assistant","content":"ne.","delta":true}',
          '{"type":"result","timestamp":"t","status":"success","stats":{"total_tokens":2460,"input_tokens":2400,"output_tokens":60,"cached":0}}']
    g = runtimes.scan("gemini", gm, "1,0,10")
    check("runtimes: gemini - session, tool, tokens, streamed answer joined, success",
          g["conversation_id"] == "G1" and g["tools"] == 1 and g["tokens"] == 2460 and g["result"]["response"] == "Done." and g["result"]["status"] == "SUCCESS")
    ag = ['{"event":"init","init":{"conversation_id":"A1"}}', '{"event":"step_update","step_update":{"step_type":"tool","state":"DONE","usage":{"total_tokens":500}}}',
          '{"event":"result","result":{"status":"SUCCESS","response":"ok"}}', 'not json', '[1,2]']
    d_ = runtimes.scan("agy", ag)
    check("runtimes: agy unchanged; garbage lines are ignored", d_["conversation_id"] == "A1" and d_["tools"] == 1 and d_["tokens"] == 500
          and d_["result"]["status"] == "SUCCESS")
    argv, stdin_text = runtimes.command("codex", "PROMPT", "gpt-x", "TH1", 10, 1.0)
    check("runtimes: API prompts go on stdin, resume flags per CLI, codex quiet flags",
          stdin_text == "PROMPT" and "PROMPT" not in argv and argv[-3:] == ["resume", "TH1", "-"] and "check_for_update_on_startup=false" in argv
          and runtimes.command("claude", "P", "sonnet", "S1", 10, 2.0)[0][-4:] == ["--max-budget-usd", "2", "--resume", "S1"]
          and runtimes.command("agy", "P", "m", None, 10)[1] is None)
    # planner output normalisation: garbage never raises, keys are unique, dependencies resolve by key or slug
    nt = norm_tasks({"tasks": [{"key": "a", "title": "A", "files": ["x.py", "../etc/passwd", 5], "depends_on": "b"},
                               "garbage", {"key": "a", "title": "A again", "risk": "RISKY", "acceptance": "one"},
                               {"key": "b", "depends_on": ["a", "zzz"], "spec": {"not": "text"}}]}, 10)
    check("norm_tasks: objects only, unique ids, safe files, coerced fields, deps by key",
          [x[0] for x in nt] == ["0010-a", "0011-a", "0012-b"] and nt[0][1]["files"] == ["x.py"] and nt[0][2] == ["0012-b"]
          and nt[1][1]["risk"] == "risky" and nt[1][1]["acceptance"] == ["one"] and nt[2][2] == ["0010-a"] and isinstance(nt[2][1]["spec"], str))
    check("norm_tasks: a reply that is not a plan gives no tasks", norm_tasks({"tasks": "x"}, 1) == [] and norm_tasks(None, 1) == [])
    # kill switch streak: neutral outcomes neither count nor reset; ok resets; other projects are ignored
    L = [dict(reason=r, project=p) for r, p in (("ok", ""), ("failed", ""), ("killswitch", ""), ("token-cap", ""), ("failed", "other"),
                                                 ("quota", ""), ("failed", ""))]
    check("consecutive_failures: skips killswitch/token-cap/quota, ignores other projects, stops at ok",
          sbx.consecutive_failures(L, "") == 2 and sbx.consecutive_failures(L + [dict(reason="ok", project="")], "") == 0
          and sbx.consecutive_failures(L, "other") == 1)
    # notifications payloads
    b, h = sbx.notify_payload("ntfy", "goal_done", "Cieľ hotový", "text")
    b2, _ = sbx.notify_payload("slack", "goal_done", "T", "m")
    check("notify_payload: ntfy encodes a non-ASCII title (RFC 2047), slack sends text",
          b == b"text" and h["Title"].startswith("=?UTF-8?B?") and json.loads(b2)["text"] == "*T*\nm")
    # maintenance findings, unique goal ids, orphaned runs - on a throwaway state dir
    old_fd = ctx.FD
    d = tempfile.mkdtemp(prefix="forge-maint-")
    try:
        ctx.FD = d
        os.makedirs(os.path.join(d, "goals"))
        f1 = _maintenance_findings([(5, "no tests ran", []), (5, "no tests ran", []), (125, "docker error", [])], [], "abc")
        check("maintenance: 'no tests' and docker errors are not a red main", not goals() and not any("red" in x for x in f1))
        f2 = _maintenance_findings([(1, "1 failed", ["FAILED t.py::x"])] * 3, ["a.py"], "abc")
        f3 = _maintenance_findings([(0, "ok", []), (1, "1 failed", ["FAILED t.py::y"]), (0, "ok", [])], [], "abc")
        bl = jload(os.path.join(d, "backlog.json"), [])
        check("maintenance: red main -> one goal, flaky -> backlog first, missing docstrings -> backlog once",
              len(goals()) == 1 and "main is red" in goals()[0]["text"] and any("flaky" in x for x in f3)
              and bl[0].startswith(MAINT_PREFIX + " flaky") and sum(x.startswith(MAINT_PREFIX + " module docstrings") for x in bl) == 1)
        a1 = new_goal_id(); save_goal(dict(id=a1, text="x", status="new")); a2 = new_goal_id()
        check("new_goal_id: unique within the same second", a1 != a2)
        run = os.path.join(d, "20260101T000000Z-worker-9")
        os.makedirs(run)
        open(os.path.join(run, "pid"), "w").write("999999999")
        check("orphaned: dead supervisor + not finished = orphan; finished = not", orphaned(run))
        open(os.path.join(run, "finished"), "w").write("x")
        check("orphaned: a finished run is never an orphan", not orphaned(run))
    finally:
        ctx.FD = old_fd
        shutil.rmtree(d, ignore_errors=True)
    print("foreman selftest: " + ("ALL OK" if not bad else f"{bad} FAILED"))
    return 1 if bad else 0
