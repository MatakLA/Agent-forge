"""Pure-function and small integration tests of the foreman; spend nothing (`bin/foreman --selftest`)."""
import datetime as dt, json, os, subprocess, time

import sbx, runtimes, codemap

from . import ctx
from .util import extract_json, norm_tasks, parse_limit, parse_numstat, ready_tasks, sh, slugify
from .store import goals, jload, new_goal_id, save_goal, tasks
from .pool import has_quota, order_by_quota, quota_pct
from .repo import CODEMAP, branch_diff, codemap_main, context_pack, own_files, usage_block, variant
from .knowledge import role_catalogue, role_playbook
from .prompts import worker_spec
from .merge import auto_rebase
from .maintenance import MAINT_PREFIX, _maintenance_findings
from .loop import orphaned
from .report import task_diff, team_metrics
from .stats import efficiency
from .settings import settings_apply


def selftest():
    """Runs every check; the event lines the tested code writes to stderr are swallowed (they are expected)."""
    import contextlib, io
    with contextlib.redirect_stderr(io.StringIO()):
        return _selftest()


def _selftest():
    import shutil, tempfile
    bad = 0
    import sys
    llm_mod = sys.modules[__package__ + ".llm"]          # (foreman.llm the module; the package attribute `llm` is the function)

    def no_model(*a, **k):
        raise llm_mod.Unavailable("selftest: no model calls")
    real_notify, real_llm = sbx.notify, llm_mod._llm_inner
    sbx.notify, llm_mod._llm_inner = (lambda *a, **k: False), no_model     # hermetic: on a machine with workers a test must not call a model
    try:
        return _selftest_body(bad, real_notify)
    finally:
        import concurrent.futures as cf                  # steps the tested loop submitted to the pool must end while the stub is still in place
        cf.wait(list(sys.modules[__package__ + ".jobs"].INFLIGHT.values()), timeout=60)
        sbx.notify, llm_mod._llm_inner = real_notify, real_llm


def _selftest_body(bad, real_notify):
    import shutil, tempfile

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
    check("slugify", slugify("Spending Alert: Billing!") == "spending-alert-billing")
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
    mts = [dict(id="0001-a", role="backend developer", status="done", history=[dict(outcome="rework", kind="tests-own", flags=["scope", "duplicate"]), dict(outcome="done")], diff=dict(added=10, removed=2)),
           dict(id="0002-b", role="Backend Developer", status="done", history=[dict(outcome="done")], diff=dict(added=5, removed=0)),
           dict(id="0003-c", role="tester", status="blocked", history=[dict(outcome="rework"), dict(outcome="blocked")]),
           dict(id="0004-d", role="tester", status="cancelled", history=[])]
    mm = team_metrics(mts)
    check("team_metrics: roles merged by slug, first-pass %, reworks, cancelled ignored",
          mm["roles"]["backend-developer"]["done"] == 2 and mm["roles"]["backend-developer"]["first_pass_pct"] == 50
          and mm["roles"]["backend-developer"]["lines_done"] == 17 and mm["roles"]["tester"]["tasks"] == 1
          and mm["totals"]["reworks"] == 2 and mm["totals"]["blocked"] == 1)
    check("team_metrics: rework reasons and the gate flags set at the time are counted (old entries: unknown)",
          mm["totals"]["rework_kinds"] == {"tests-own": 1, "unknown": 1} and mm["totals"]["rework_flags"] == {"scope": 1, "duplicate": 1})
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
                                                                    capture_output=True, text=True, errors="replace")
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
        ok_ = settings_apply({"CLAUDE_MODEL": "opus", "WORKERS": "worker-1,worker-3", "WORKER_MODEL": "gemini-3.8-flash-high", "MAX_PARALLEL": "8",
                              "OWNER_LANG": "Slovak"})
        fc, lc = open(os.path.join(st_dir, "etc", "foreman.conf")).read(), open(os.path.join(st_dir, "etc", "limits.conf")).read()
        check("settings_apply writes project keys in place (comments kept) and limits to etc/limits.conf",
              not ok_ and "# keep me" in fc and "CLAUDE_MODEL=opus" in fc and "CLAUDE_MODEL=sonnet" not in fc and "WORKERS=worker-1,worker-3" in fc
              and "MAX_PARALLEL=8" in lc and "MAX_PARALLEL=4" not in lc and "MAX_PARALLEL" not in fc)
        new_bad = {
            "DAILY_TOKEN_BUDGET": "99", "MAX_RUNS_PER_DAY": "0", "RUN_TIMEOUT_MAX_MIN": "2",
            "QUOTA_MODEL_FILTER": "evil; rm -rf /", "POLL_SEC": "0", "OPENAI_API_BASE": "ftp://bad.example",
            "DIFF_MAX": "10", "MAX_GOAL_ROUNDS": "0", "LESSONS_MAX": "0", "LOOP_SEC": "0",
            "LOCAL_TZ": "bad; tz", "SHARED_FILES": "bad; file", "VISION_FILE": "/etc/passwd", "PROJECT_DIR": "/root"
        }
        errs_new = settings_apply(new_bad)
        check("settings_apply rejects malformed/out-of-range values for all newly added keys",
              set(errs_new) == set(new_bad))
        new_good = {
            "DAILY_TOKEN_BUDGET": "5000000", "MAX_RUNS_PER_DAY": "100", "RUN_TIMEOUT_MAX_MIN": "60",
            "QUOTA_MODEL_FILTER": "Claude", "POLL_SEC": "5", "OPENAI_API_BASE": "https://api.openai.com/v1",
            "DIFF_MAX": "50000", "MAX_GOAL_ROUNDS": "5", "LESSONS_MAX": "50", "LOOP_SEC": "15",
            "LOCAL_TZ": "Europe/Prague", "SHARED_FILES": "reg.jsonl,notes.md", "VISION_FILE": "docs/vision.md", "PROJECT_DIR": "web"
        }
        errs_good = settings_apply(new_good)
        fc2, lc2 = open(os.path.join(st_dir, "etc", "foreman.conf")).read(), open(os.path.join(st_dir, "etc", "limits.conf")).read()
        check("settings_apply accepts valid values for all newly added keys and writes them",
              not errs_good and "DIFF_MAX=50000" in fc2 and "DAILY_TOKEN_BUDGET=5000000" in lc2)
    finally:
        ctx.ROOT, ctx.FD = old_root, old_fd
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
        ctx.REPO, ctx.FD = os.path.join(d, "repo"), os.path.join(d, "fd")
        os.makedirs(ctx.REPO); os.makedirs(ctx.FD)
        open(os.path.join(ctx.REPO, "store.py"), "w").write('"""Contact storage: add/list contacts in a JSON file."""\n\ndef add(name: str) -> dict:\n'
                                                         '    """Add a contact."""\n    return {"name": name}\n')
        open(os.path.join(ctx.REPO, "birthdays.py"), "w").write('"""Birthday reminders."""\nimport store\n\ndef upcoming(days=7):\n    return []\n')
        open(os.path.join(ctx.REPO, "test_birthdays.py"), "w").write('import birthdays\n\ndef test_up():\n    assert birthdays.upcoming() == []\n')
        open(os.path.join(ctx.REPO, "other.py"), "w").write("def unrelated():\n    pass\n")
        open(os.path.join(ctx.REPO, "util.js"), "w").write("function render() {\n  return 1;\n}\nfunction other() {\n  return 2;\n}\n")
        open(os.path.join(ctx.REPO, "reports.py"), "w").write("import store\n\ndef daily():\n    return store.list_contacts()\n")
        open(os.path.join(ctx.REPO, "test_reports.py"), "w").write("import store\n" + "".join(f"def test_{i}():\n    assert store.list_contacts() == []\n" for i in range(6)))
        open(os.path.join(ctx.REPO, "store.py"), "a").write("\ndef list_contacts():\n    return []\n\ndef purge_all():\n    return None\n")
        for c in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
            sh(["git"] + c, cwd=ctx.REPO)
        sh(["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=ctx.REPO)
        ctx.C.update(PROJECT_DIR="", CONTEXT_PACK_CHARS=60000)
        cp = context_pack(dict(id="0003-b", files=["birthdays.py", "test_birthdays.py"], spec="Use store.add() for new contacts.", acceptance=[]))
        check("context pack: own files in full, imported module's interface, one-line map of the rest, no bodies of others",
              "def upcoming(days=7):" in cp and "def add(name: str) -> dict  # Add a contact." in cp and "`other.py`" in cp
              and 'return {"name": name}' not in cp and "NO MODULE DOCSTRING" in cp)
        cp2 = context_pack(dict(id="0003-b", files=["birthdays.py"], spec="", acceptance=[], attempts=1))
        check("context pack: a rework does not resend main's version of the worker's own files, and says why",
              "def upcoming(days=7):\n" not in cp2 and "Your branch already has earlier work" in cp2)
        sh(["git", "checkout", "-qb", "test/0003-b"], cwd=ctx.REPO)
        open(os.path.join(ctx.REPO, "birthdays.py"), "a").write("\ndef extra():\n    return 42\n")
        sh(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "branch work"], cwd=ctx.REPO)
        sh(["git", "update-ref", "refs/remotes/origin/test/0003-b", "HEAD"], cwd=ctx.REPO)
        sh(["git", "checkout", "-q", "-"], cwd=ctx.REPO)
        t3 = dict(id="0003-b", branch="test/0003-b", files=["birthdays.py"], spec="", acceptance=[], attempts=1)
        bd = branch_diff(t3, 20000)
        cp3 = context_pack(t3)
        check("context pack: a rework gets its branch's earlier work as a diff against main (and no diff when the branch is unknown)",
              "+def extra():" in bd and "Your branch's earlier work" in cp3 and "+def extra():" in cp3
              and branch_diff(dict(branch="test/none"), 20000) == "" and branch_diff(dict(), 20000) == "")
        ctx.C.update(PACK_EXTRAS="off")
        cp3off = context_pack(t3)
        check("PACK_EXTRAS=off: no spans, no usage block, no branch diff",
              "[L" not in cp3off and "Who uses what you change" not in cp3off and "+def extra():" not in cp3off
              and "[L" not in context_pack(dict(id="0003-c", files=["store.py"], spec="", acceptance=[])))
        ctx.C.update(PACK_EXTRAS="on")
        cm_ = codemap_main()
        rend = codemap.render(cm_, ["store.py", "util.js"])
        check("codemap: every definition carries its line span (python by ast, other languages up to the next definition)",
              "def add(name: str) -> dict  # Add a contact.  [L3-5]" in rend and "function render()  [L1-3]" in rend
              and "function other()  [L4-6]" in rend)
        plain = codemap.render(cm_, ["store.py"], with_lines=False, sizes=False)
        check("codemap: with_lines=False / sizes=False give the stable text (no spans, no line counts)",
              "[L" not in plain and "lines)" not in plain and "def add(name: str) -> dict  # Add a contact." in plain)
        check("codemap.public_names: top-level definitions only", codemap.public_names(cm_["store.py"]) == ["add", "list_contacts", "purge_all"])
        ub = usage_block(dict(id="0003-d", files=["store.py"], spec="", acceptance=[]), cm_, 10000)
        check("usage block: callers outside the task's files with path:line; names nothing uses are free to change; short names skipped",
              "`list_contacts` (store.py): `reports.py:4`" in ub and "free to change" in ub and "`purge_all`" in ub.split("free to change")[1]
              and "`add`" not in ub and "`store.py:" not in ub)
        check("usage block: hits are grouped per file (non-test files first, at most 3 lines each) with a count of the rest",
              "`list_contacts` (store.py): `reports.py:4`, `test_reports.py:3,5,7` (+3 more places)" in ub)
        ub2 = usage_block(dict(id="0003-d", files=["store.py", "reports.py"], spec="", acceptance=[]), cm_, 10000)
        check("usage block: a caller that is itself one of the task's files is not listed", "reports.py:4" not in ub2)
        stable, task_part = context_pack(dict(id="0003-b", files=["birthdays.py"], spec="", acceptance=[]), parts=True)
        stable2, _ = context_pack(dict(id="0004-z", files=["store.py"], spec="Use birthdays.upcoming().", acceptance=[]), parts=True)
        check("context pack parts: the stable half is the same for different tasks and holds the file list; the task half holds the task's files",
              stable == stable2 and "## All files in the project" in stable and "`other.py`" in stable and "[L" not in stable.split("## All files")[1] and "lines)" not in stable
              and "def upcoming(days=7):" in task_part and "## All files in the project" not in task_part)
        ctx.C.update(CONTEXT_PACK_CHARS=400)
        check("context pack respects CONTEXT_PACK_CHARS", len(context_pack(dict(id="0003-b", files=["birthdays.py"], spec="", acceptance=[]))) < 1100)
    finally:
        ctx.C.update(saved)
        ctx.REPO, ctx.FD = old_repo, old_fd
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
    nt_full = norm_tasks({"tasks": [{"key": "c", "acceptance": ["all tests in the repo pass", "existing full test suite still passes unmodified", "specific component behaves"]}]}, 20)
    check("norm_tasks: full test suite acceptance criteria sanitized to scoped tests",
          nt_full[0][1]["acceptance"] == ["relevant tests for touched files/domain pass (merge gate runs full suite)",
                                          "relevant tests for touched files/domain pass (merge gate runs full suite)",
                                          "specific component behaves"])
    # kill switch streak: neutral outcomes neither count nor reset; ok resets; other projects are ignored
    L = [dict(reason=r, project=p) for r, p in (("ok", ""), ("failed", ""), ("killswitch", ""), ("token-cap", ""), ("failed", "other"),
                                                 ("quota", ""), ("failed", ""))]
    check("consecutive_failures: skips killswitch/token-cap/quota, ignores other projects, stops at ok",
          sbx.consecutive_failures(L, "") == 2 and sbx.consecutive_failures(L + [dict(reason="ok", project="")], "") == 0
          and sbx.consecutive_failures(L, "other") == 1)
    check("no_nul: a NUL byte is spelled out, so it can reach an argv",
          sbx.no_nul("a\x00b") == "a\\x00b" and sbx.no_nul("plain") == "plain")
    # finite goals: an open-ended goal is held and the owner's answer replaces it
    from .planning import hold_open_ended
    from .owner import answer_question
    from .prompts import finite_rule
    old_fd2, old_cfg2, tmp2 = ctx.FD, ctx.CFG_DIR, tempfile.mkdtemp(prefix="foreman-finite-")
    try:
        ctx.FD = ctx.CFG_DIR = tmp2; os.makedirs(os.path.join(tmp2, "goals"))
        gx = dict(id="G20261014-000001", text="Add more runtimes", status="new", created=sbx.now()); save_goal(gx)
        hold_open_ended(gx, {"open_ended": True, "question": "Which runtimes?", "finite_goal": "Add exactly Foo and Bar."})
        gh = jload(os.path.join(tmp2, "goals", "G20261014-000001.json")); qs = jload(os.path.join(tmp2, "owner_questions.json"), [])
        check("open-ended goal: not planned, blocked, and the question names the goal and the proposed finite version",
              gh["status"] == "blocked" and len(qs) == 1 and qs[0].startswith("[G20261014-000001] Which runtimes?") and "exactly Foo and Bar" in qs[0])
        answer_question(1, "Add exactly Foo and Bar runtimes, each with a doctor check; done when tests/check.sh passes.")
        gh = jload(os.path.join(tmp2, "goals", "G20261014-000001.json"))
        check("open-ended goal: the owner's answer becomes the goal text and it is planned again",
              gh["status"] == "new" and gh["text"].startswith("Add exactly Foo and Bar") and not jload(os.path.join(tmp2, "owner_questions.json"), []))
        check("finite_rule: tells the planner how to refuse an open-ended goal", '"open_ended": true' in finite_rule() and "END STATE" in finite_rule())
    finally:
        ctx.FD, ctx.CFG_DIR = old_fd2, old_cfg2; shutil.rmtree(tmp2, ignore_errors=True)   # decisions.md is written into CFG_DIR: never the live one
    # notifications payloads
    b, h = sbx.notify_payload("ntfy", "goal_done", "Cieľ hotový", "text")
    b2, _ = sbx.notify_payload("slack", "goal_done", "T", "m")
    check("notify_payload: ntfy encodes a non-ASCII title (RFC 2047), slack sends text",
          b == b"text" and h["Title"].startswith("=?UTF-8?B?") and json.loads(b2)["text"] == "*T*\nm")
    ob_runs, ob_conf, ob_dir = sbx.RUNS, sbx.project_conf, tempfile.mkdtemp(prefix="forge-outbox-")
    try:
        sbx.RUNS = ob_dir
        sbx.project_conf = lambda project=None: {"NOTIFY_URL": "outbox", "NOTIFY_EVENTS": "goal_done"}
        sbx.notify = real_notify
        sent = sbx.notify("goal_done", "Cieľ hotový", "x" * 5000), sbx.notify("task_blocked", "T", "m")
        rows = [json.loads(l) for l in open(sbx.notify_outbox_path(), encoding="utf-8")]
    finally:
        sbx.notify = lambda *a, **k: False
        sbx.RUNS, sbx.project_conf = ob_runs, ob_conf
        shutil.rmtree(ob_dir, ignore_errors=True)
    check("notify: NOTIFY_URL=outbox appends filtered events to runs/notify-outbox.jsonl",
          sent == (True, False) and len(rows) == 1 and rows[0]["title"] == "Cieľ hotový" and len(rows[0]["message"]) == 3500)
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
    for group in GROUPS:
        group(check)
    print("foreman selftest: " + ("ALL OK" if not bad else f"{bad} FAILED"))
    return 1 if bad else 0


def test_runtimes_egress_budgets(check):
    """The owner's model server (compat / aider), its egress exception, the owner's host decisions, and the API money caps."""
    import egress, shutil
    argv, stdin_text = runtimes.command("compat", "P", "qwen3-coder:30b", None, 10)
    check("runtimes: compat is Codex through af-agent, prompt on stdin, no URL in argv",
          argv[:3] == ["af-agent", "compat", "exec"] and stdin_text == "P" and "-m" in argv and not any("http" in a for a in argv))
    argv, _ = runtimes.command("aider", "P", "qwen3-coder", "aider:feat_x", 10)
    check("runtimes: aider gets its quiet flags, the openai/ model prefix and restores the branch's chat on a rework",
          argv[:2] == ["af-agent", "aider"] and "--analytics-disable" in argv and "openai/qwen3-coder" in argv and "--restore-chat-history" in argv)
    ev = ['{"type": "aider.init", "conversation_id": "aider:feat_0001-x"}', '{"type": "aider.line", "text": "Applied edit to hello.txt"}',
          '{"type": "aider.line", "text": "Done, the tests pass."}', '{"type": "aider.usage", "sent": 1200, "received": 40, "cost": 0.0}',
          '{"type": "aider.usage", "sent": 800, "received": 60, "cost": 0.0}', '{"type": "aider.result", "status": "SUCCESS", "exit_code": 0}']
    a = runtimes.scan("aider", ev, "1,0.1,10")
    check("runtimes: aider events - conversation, edits as tool calls, tokens summed, cost from PRICE_COMPAT",
          a["conversation_id"] == "aider:feat_0001-x" and a["tools"] == 1 and a["tokens"] == 2100 and a["result"]["status"] == "SUCCESS"
          and abs(a["result"]["cost_usd"] - (2000 * 1 + 100 * 10) / 1e6) < 1e-9 and "tests pass" in a["result"]["response"])
    check("runtimes: a local server at price 0 costs 0, not 'unknown'",
          runtimes.scan("aider", ev, "0,0,0")["result"]["cost_usd"] == 0.0 and runtimes.scan("compat", [], "0,0,0")["result"] is None)
    check("runtimes: endpoint URLs - only plain http(s), the host is what egress must allow",
          runtimes.hosts("compat", "http://192.168.1.20:11434/v1") == ["192.168.1.20"] and runtimes.hosts("aider", "") == []
          and runtimes.endpoint_target("http://x:1/v1'; rm -rf /") is None and runtimes.endpoint_target("ftp://x/v1") is None)
    check("egress: a public HTTPS server is an allow-list entry; a private address or plain HTTP is an exact address:port exception",
          egress.endpoint_rule("https://openrouter.ai/api/v1", resolve=False) == ("allowlist", "openrouter.ai")
          and egress.endpoint_rule("http://192.168.1.20:11434/v1", resolve=False) == ("local", "192.168.1.20", 11434)
          and egress.endpoint_rule("https://10.0.0.5/v1", resolve=False) == ("local", "10.0.0.5", 443)
          and egress.endpoint_rule("http://ollama.example.com/v1", resolve=False) == ("local", "ollama.example.com", 80)
          and egress.endpoint_rule("not a url") is None)
    conf = egress.local_endpoint_conf("192.168.1.20", 11434)
    check("egress: the squid snippet allows exactly that address and port, and refuses anything that is not a host name / IP",
          "acl af_model_server dst 192.168.1.20" in conf and "acl af_model_port port 11434" in conf
          and "http_access allow workers af_model_server af_model_port" in conf and _raises(lambda: egress.local_endpoint_conf("a b", 80))
          and _raises(lambda: egress.local_endpoint_conf("x\nhttp_access allow all", 80)))
    evs = [dict(ts=10.0, client="1.1.1.1", result="TCP_MISS", code="200", method="POST", url="http://192.168.1.20:11434/v1/responses",
                host="192.168.1.20", port="11434", private=True),
           dict(ts=11.0, client="1.1.1.1", result="TCP_DENIED", code="403", method="CONNECT", url="192.168.1.9:22", host="192.168.1.9",
                port="22", private=True)]
    old_le = egress.local_endpoint
    egress.local_endpoint = lambda: ("192.168.1.20", 11434)
    try:
        r = egress.analyze(events=evs, ack=False)
    finally:
        egress.local_endpoint = old_le
    check("egress: only exact public host names can be allowed or ignored",
          not egress.check_host("pypi.org") and not egress.check_host("files.pythonhosted.org") and egress.check_host("*.example.com")
          and egress.check_host("10.0.0.1") and egress.check_host("printer.local") and egress.check_host("x.org; reboot") and egress.check_host(""))
    import tempfile as _tf
    tdir = _tf.mkdtemp(prefix="forge-egress-")
    old_paths = (egress.ALLOWLIST, egress.ALLOWLIST_LOCAL, egress.BASELINE, egress.reload_proxy)
    try:
        egress.ALLOWLIST, egress.ALLOWLIST_LOCAL, egress.BASELINE = (os.path.join(tdir, n) for n in ("a.txt", "local.txt", "base.txt"))
        open(egress.ALLOWLIST, "w").write("api.example.com\n")
        egress.reload_proxy = lambda: ["reloaded"]
        m1 = egress.allow_host("PyPI.org"); m2 = egress.allow_host("pypi.org")
        allowed_then = "pypi.org" in egress.allowlist()
        m3 = egress.unallow_host("pypi.org"); m4 = egress.unallow_host("api.example.com")
        egress.ignore_host("telemetry.example.net")
        check("egress: allow adds an exact host once and reloads; remove takes only hosts added here; ignore keeps it blocked",
              allowed_then and "reloaded" in m1 and "already" in m2[0] and "pypi.org" not in egress.allowlist() and "reloaded" in m3
              and "not on the local" in m4[0] and "api.example.com" in egress.allowlist() and "telemetry.example.net" in egress.baseline()
              and "telemetry.example.net" not in egress.allowlist() and _raises(lambda: egress.allow_host("192.168.1.9")))
    finally:
        egress.ALLOWLIST, egress.ALLOWLIST_LOCAL, egress.BASELINE, egress.reload_proxy = old_paths
        shutil.rmtree(tdir, ignore_errors=True)
    check("egress: requests to the model server are allowed traffic, anything else private still raises an alarm",
          r["allowed"].get("192.168.1.20") == 1 and [p["target"] for p in r["private"]] == ["192.168.1.9:22"])
    today, month = sbx.today(), sbx.today()[:7]
    led = [dict(date=today, cost_usd=3.0, project="", worker="worker-5"), dict(date=today, cost_usd=1.0, project="shop", worker="worker-6"),
           dict(date=month + "-01" if today[8:] != "01" else month + "-02", cost_usd=20.0, project="", worker="worker-5"),
           dict(date="2000-01-01", cost_usd=99.0, project="", worker="worker-5"), dict(date=today, cost_usd=50.0, test=True)]
    check("budgets: spend by day / month, project and worker; test runs never count",
          sbx.api_spent(led) == 4.0 and sbx.api_spent(led, "month") in (24.0, 4.0) and sbx.api_spent(led, "day", "shop") == 1.0
          and sbx.api_spent(led, "day", worker="worker-5") == 3.0)
    check("budgets: worker caps are parsed leniently", sbx.worker_budgets("worker-5=3, worker-6=1.5,junk,worker-7=x") ==
          {"worker-5": 3.0, "worker-6": 1.5})
    lim = dict(API_RUN_BUDGET_USD=2.0, API_DAILY_BUDGET_USD=10.0, API_MONTHLY_BUDGET_USD=0.0, WORKER_DAILY_BUDGET_USD="worker-5=4")
    bc = lambda w, conf, running=(): sbx.budget_check(w, lim, conf=conf, ledger=led, running=list(running))
    check("budgets: within every cap -> ok; the worker's own daily cap stops it; the project's daily cap stops the project",
          bc("worker-6", {})[0] and not bc("worker-5", {})[0] and "worker-5" in bc("worker-5", {})[1]
          and not bc("worker-6", {"PROJECT_DAILY_BUDGET_USD": "4"})[0])
    check("budgets: running API runs count at their full cap (3 running x $2 + $4 spent + $2 > $10)",
          bc("worker-6", {}, [("worker-1", ""), ("worker-2", ""), ("worker-3", "")])[0] is False and bc("worker-6", {}, [("worker-1", "")])[0])
    lim0 = dict(lim, API_DAILY_BUDGET_USD=0.0)
    check("budgets: the install's daily cap always applies (0 = no API runs), the others only when set",
          not sbx.budget_check("worker-6", lim0, conf={}, ledger=[], running=[])[0])


def _raises(fn):
    try:
        fn()
    except Exception:
        return True
    return False


def test_owner_and_insights(check):
    """The owner's approvals and actions, why a worker is idle, the backlog forecast, the numbers behind routing and A/B."""
    import shutil, tempfile
    from .util import ready_tasks
    from .owner import goal_action, task_action
    from .planning import hold_if_risky
    from .loop import why_no_task
    from .report import forecast
    from .knowledge import doc_path
    from .stats import ab_verdict, estimate, mean_ci, pick_model, routing_table, sizes, wilson
    ts = [dict(id="0001-a", goal="G1", status="pending", files=["a.py"]), dict(id="0002-b", goal="G2", status="pending", files=["b.py"]),
          dict(id="0003-c", goal="G3", status="pending", files=["c.py"])]
    gm = {"G1": dict(id="G1"), "G2": dict(id="G2", priority=2), "G3": dict(id="G3", paused=True)}
    check("ready_tasks: a paused goal's tasks wait, a prioritized goal's tasks go first",
          [t["id"] for t in ready_tasks(ts, set(), gm)] == ["0002-b", "0001-a"])
    t = dict(status="pending", risk="risky")
    hold_if_risky(t, {"risky"})
    t2 = dict(status="pending", risk="routine")
    hold_if_risky(t2, {"risky"})
    check("APPROVE=risky holds a risky task (and only that) for the owner", t == dict(status="approval", risk="risky", awaiting="risky")
          and t2["status"] == "pending")
    old_fd, d = ctx.FD, tempfile.mkdtemp(prefix="forge-owner-")
    try:
        ctx.FD = d
        os.makedirs(os.path.join(d, "tasks")); os.makedirs(os.path.join(d, "goals"))
        w = lambda kind, x: json.dump(x, open(os.path.join(d, kind, x["id"] + ".json"), "w"))
        r = lambda kind, i: json.load(open(os.path.join(d, kind, i + ".json")))
        w("goals", dict(id="G20260101-000000", text="g", status="approval", rounds=1))
        w("tasks", dict(id="0001-plan", goal="G20260101-000000", status="approval", awaiting="plan", after="pending", risk="risky", attempts=0, history=[]))
        w("tasks", dict(id="0002-plan", goal="G20260101-000000", status="approval", awaiting="plan", after="splitting", attempts=0, history=[]))
        w("tasks", dict(id="0003-merge", goal="G20260101-000000", status="approval", awaiting="merge", attempts=0, history=[]))
        w("tasks", dict(id="0004-run", goal="G20260101-000000", status="running", run=os.path.join(d, "run1"), attempts=0, history=[]))
        w("tasks", dict(id="0005-blk", goal="G20260101-000000", status="blocked", attempts=4, history=[], blocked_why="x"))
        w("tasks", dict(id="0006-new", goal="G20260101-000000", status="pending", attempts=0, history=[]))
        os.makedirs(os.path.join(d, "run1"))
        old_ap = ctx.C["APPROVE"]
        ctx.C["APPROVE"] = "plan,risky,merge"
        try:
            ok1 = goal_action("G20260101-000000", "approve")[0]
        finally:
            ctx.C["APPROVE"] = old_ap
        check("approving a plan releases its tasks (a split goes on splitting, a risky one waits again as risky), the goal runs",
              ok1 and r("goals", "G20260101-000000")["status"] == "active" and r("tasks", "0002-plan")["status"] == "splitting"
              and r("tasks", "0001-plan")["status"] == "approval" and r("tasks", "0001-plan")["awaiting"] == "risky")
        check("approving the risky task starts it; approving a merge marks it merge-ready (and remembers the approval)",
              task_action("0001-plan", "approve")[0] and r("tasks", "0001-plan")["status"] == "pending"
              and task_action("0003-merge", "approve")[0] and r("tasks", "0003-merge")["status"] == "merge-ready"
              and r("tasks", "0003-merge")["merge_approved"] is True)
        check("cancelling a running task asks its supervisor to stop (stop file) and keeps it running until the run ends",
              task_action("0004-run", "cancel")[0] and os.path.exists(os.path.join(d, "run1", "stop"))
              and r("tasks", "0004-run")["status"] == "running" and r("tasks", "0004-run")["cancel_requested"])
        ok_fb, _ = task_action("0005-blk", "feedback", text="Use the existing parser.")
        t5 = r("tasks", "0005-blk")
        check("feedback on a blocked task sends it back with the owner's words and fresh attempts",
              ok_fb and t5["status"] == "pending" and t5["attempts"] == 0 and "Use the existing parser." in t5["feedback"])
        check("feedback on a task nobody worked on goes into its spec", task_action("0006-new", "feedback", text="Prefer sqlite.")[0]
              and "Prefer sqlite." in r("tasks", "0006-new")["owner_notes"] and r("tasks", "0006-new")["status"] == "pending")
        check("assign / model: validated, and refused for a running task",
              task_action("0006-new", "assign", target="worker-3")[0] and r("tasks", "0006-new")["pin_worker"] == "worker-3"
              and not task_action("0006-new", "assign", target="worker-3; reboot")[0] and not task_action("0004-run", "model", target="x")[0]
              and not task_action("../x", "cancel")[0] and not task_action("0006-new", "explode")[0])
        check("goals: pause / prioritize / cancel; replan needs a plan waiting and a note",
              goal_action("G20260101-000000", "pause")[0] and r("goals", "G20260101-000000")["paused"]
              and goal_action("G20260101-000000", "prioritize")[0] and r("goals", "G20260101-000000")["priority"] == 1
              and not goal_action("G20260101-000000", "replan", "x")[0]
              and goal_action("G20260101-000000", "cancel")[0] and r("tasks", "0006-new")["status"] == "cancelled")
    finally:
        ctx.FD = old_fd
        shutil.rmtree(d, ignore_errors=True)
    run = [dict(id="0009-r", status="running", files=["x.py"])]
    check("why idle: the kill switch first, then MAX_PARALLEL, then the blocker of the first waiting task",
          why_no_task([], {}, "the project is stopped (kill switch): egress") .startswith("the project is stopped")
          and "MAX_PARALLEL" in why_no_task([], {}, "", 4, 4)
          and "0001-a waits for 0000-z" in why_no_task([dict(id="0001-a", goal="G", status="pending", depends_on=["0000-z"])], {})
          and "0010-s waits: 0009-r is changing the same files" in why_no_task(run + [dict(id="0010-s", goal="G", status="pending", files=["x.py"])], {})
          and "its goal is paused" in why_no_task([dict(id="0011-p", goal="G", status="pending")], {"G": dict(paused=True)})
          and "assigned to worker-2" in why_no_task([dict(id="0012-q", goal="G", status="pending", pin_worker="worker-2")], {}, worker="worker-1")
          and "wait for your approval" in why_no_task([dict(id="0013-a", goal="G", status="approval")], {})
          and "add a goal" in why_no_task([], {}))
    now = 1_800_000_000
    iso = lambda dd: __import__("datetime").datetime.fromtimestamp(now - dd * 86400, __import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fts = [dict(id=f"{i:04d}", goal=f"G{i % 3}", status="done", finished=iso(i % 6)) for i in range(14)] + \
          [dict(id="0100", goal="G9", status="pending"), dict(id="0101", goal="G9", status="running")]
    f = forecast(fts, 2, now)
    check("forecast: 14 merged in 7 days = 2/day; 2 open + 2 backlog goals x 4.7 tasks = ~11 -> ~5.7 days",
          f["per_day"] == 2.0 and f["open"] == 2 and f["remaining"] == 11 and abs(f["eta_days"] - 5.7) < 0.1)
    check("forecast: no pace yet -> no date", forecast([dict(id="1", goal="G", status="pending")], 0, now)["eta_days"] is None)
    lo, hi = wilson(8, 10)
    check("wilson: 8 of 10 -> about 49-94 %; nothing -> None", (lo, hi) == (49, 94) and wilson(0, 0) == (None, None))
    m = mean_ci([1, 2, 3, 4, 5])
    check("mean_ci: mean 3, symmetric interval; one value is not enough", m[0] == 3 and abs((m[2] - 3) - (3 - m[1])) < 1e-9 and mean_ci([1]) is None)
    A = dict(done=20, first_pass_ci=[60, 90], week_done_ci=[2.0, 1.8, 2.2], cost_done_ci=None)
    B = dict(done=20, first_pass_ci=[55, 88], week_done_ci=[1.2, 1.0, 1.4], cost_done_ci=None)
    Bq = dict(B, first_pass_ci=[10, 40])
    v1, v2, v3 = ab_verdict(A, B, "pro", "flash"), ab_verdict(A, Bq, "pro", "flash"), ab_verdict(A, dict(B, week_done_ci=[1.9, 1.5, 2.3]), "pro", "flash")
    check("A/B: cheaper with the same quality wins; cheaper but worse first time does not; overlapping intervals ask for more data",
          v1["winner"] == "flash" and v2["winner"] is None and v3["winner"] is None and v3["need_more"] and v3["need_more"] > 0)
    rts = [dict(id=f"{i:04d}-t", status="done", role="tester", files=["t.py"], history=[dict(outcome="rework")] if i == 4 else []) for i in range(8)]
    rs = {f"r{i}": dict(task=f"{i:04d}-t", model="flash" if i < 5 else "pro", tokens=100_000 * (1 + i % 2), week=0.5 if i < 5 else 1.5,
                        cost=None, reason="ok", variant="", tools=1, checks=[]) for i in range(8)}
    tbl = routing_table(rts, rs)
    check("routing: the cheaper model that has proven itself wins; too few tasks or too many reworks do not qualify",
          pick_model("tester", tbl, ["pro", "flash"], 5, 70) == "flash" and pick_model("tester", tbl, ["pro", "flash"], 6, 70) is None
          and pick_model("tester", tbl, ["pro"], 3, 70) == "pro" and pick_model("tester", tbl, ["flash"], 5, 90) is None)
    sz = sizes(rts, rs)
    e = estimate(dict(role="tester", files=["a.py"]), sz)
    check("sizes/estimate: median tokens of merged tasks of the same role (and size)", sz["n"] == 8 and e and e["tokens"] in (100_000, 150_000, 200_000)
          and "tester" in e["basis"] and estimate(dict(role="x"), None) is None)
    check("documents: only known names, a role is written to the project's own roles/ directory",
          doc_path("vision") and doc_path("role:../../etc/passwd") is None and doc_path("../x") is None
          and doc_path("role:tester", write=True).endswith(os.path.join("roles", "tester.md"))
          and doc_path("role:tester", write=True).startswith(ctx.CFG_DIR))


def test_orphan_recovery(check):
    """Orphan loop detection: 5 consecutive no-progress orphan recoveries block the task, progress resets streak, and startup grace exempts."""
    import shutil, tempfile
    from . import loop, store
    from .loop import recover_orphan

    old_fd, old_runs, old_root, old_limits = ctx.FD, sbx.RUNS, ctx.ROOT, sbx.LIMITS_FILE
    old_grace = loop.STARTUP_GRACE_DONE
    d = tempfile.mkdtemp(prefix="forge-selftest-orphan-")
    try:
        ctx.ROOT = d
        ctx.FD = os.path.join(d, "fd")
        sbx.RUNS = os.path.join(d, "runs")
        sbx.LIMITS_FILE = os.path.join(d, "etc", "limits.conf")
        os.makedirs(os.path.join(d, "etc"), exist_ok=True)
        open(sbx.LIMITS_FILE, "w").write("MAX_PARALLEL=4\n")
        os.makedirs(os.path.join(ctx.FD, "tasks"), exist_ok=True)
        os.makedirs(os.path.join(ctx.FD, "goals"), exist_ok=True)
        os.makedirs(sbx.RUNS, exist_ok=True)
        store.save_goal(dict(id="G1", status="active", text="dummy goal"))

        # (a) 5 consecutive no-progress orphan recoveries after startup grace block the task with reason and log tail
        loop.STARTUP_GRACE_DONE = True
        r1 = os.path.join(sbx.RUNS, "20261012T000001Z-w1")
        os.makedirs(r1, exist_ok=True)
        with open(os.path.join(r1, "supervisor.log"), "w") as f:
            f.write("crash line 1\ncrash line 2: fatal error\n")
        json.dump(dict(task="0121-a", worker="w1"), open(os.path.join(r1, "meta.json"), "w"))

        t1 = dict(id="0121-a", goal="G1", status="running", role="dev", files=["a.py"], history=[], orphan_streak=0)
        store.save_task(t1)
        for _ in range(5):
            t1["status"] = "running"
            t1["run"] = r1
            t1["worker"] = "w1"
            recover_orphan(t1)

        saved1 = store.jload(os.path.join(ctx.FD, "tasks", "0121-a.json"))
        check("orphan recovery: 5 consecutive no-progress recoveries block task with reason and log tail",
              saved1.get("status") == "blocked"
              and saved1.get("orphan_streak") == 5
              and str(saved1.get("blocked_why", "")).startswith("orphan limit reached: ")
              and "crash line 2: fatal error" in str(saved1.get("blocked_why", ""))
              and "crash line 2: fatal error" in str(saved1.get("orphan_log_tail", "")))

        # (b) a progress event (long-duration run or a real supervisor result) resets orphan_streak to 0
        r2 = os.path.join(sbx.RUNS, "20261012T000002Z-w1")
        os.makedirs(r2, exist_ok=True)
        json.dump(dict(task="0121-b1", worker="w1", duration_s=150), open(os.path.join(r2, "meta.json"), "w"))
        t2 = dict(id="0121-b1", goal="G1", status="running", role="dev", files=["b1.py"], history=[],
                  orphan_streak=4, orphan_log_tail="previous crash log", run=r2, worker="w1")
        store.save_task(t2)
        recover_orphan(t2)
        saved2 = store.jload(os.path.join(ctx.FD, "tasks", "0121-b1.json"))

        r3 = os.path.join(sbx.RUNS, "20261012T000003Z-w1")
        os.makedirs(r3, exist_ok=True)
        with open(os.path.join(r3, "finished"), "w") as f:
            f.write(sbx.now() + "\n")
        json.dump(dict(task="0121-b2", worker="w1", reason="ok", tokens=500), open(os.path.join(r3, "meta.json"), "w"))
        t3 = dict(id="0121-b2", goal="G1", status="running", role="dev", files=["b2.py"], history=[],
                  orphan_streak=3, orphan_log_tail="crash log", run=r3, worker="w1")
        store.save_task(t3)
        loop.advance()
        saved3 = store.jload(os.path.join(ctx.FD, "tasks", "0121-b2.json"))

        check("orphan recovery: progress event (long-duration run) resets orphan_streak to 0",
              saved2.get("status") == "pending"
              and saved2.get("orphan_streak") == 0
              and saved2.get("orphan_log_tail") == ""
              and saved3.get("status") == "gate"
              and saved3.get("orphan_streak") == 0
              and saved3.get("orphan_log_tail") == "")

        # (c) orphans found in the first scan pass after process start (STARTUP_GRACE_DONE still False) do not increment orphan_streak
        loop.STARTUP_GRACE_DONE = False
        r4 = os.path.join(sbx.RUNS, "20261012T000004Z-w1")
        os.makedirs(r4, exist_ok=True)
        json.dump(dict(task="0121-c", worker="w1"), open(os.path.join(r4, "meta.json"), "w"))
        t4 = dict(id="0121-c", goal="G1", status="running", role="dev", files=["c.py"], history=[],
                  orphan_streak=2, orphan_log_tail="tail from earlier", run=r4, worker="w1")
        store.save_task(t4)
        loop.advance()
        saved4 = store.jload(os.path.join(ctx.FD, "tasks", "0121-c.json"))

        # Subsequent orphan recovery after startup grace (STARTUP_GRACE_DONE transitioned to True) increments streak
        r5 = os.path.join(sbx.RUNS, "20261012T000005Z-w1")
        os.makedirs(r5, exist_ok=True)
        json.dump(dict(task="0121-c", worker="w1"), open(os.path.join(r5, "meta.json"), "w"))
        t5 = dict(saved4, status="running", run=r5, worker="w1")
        store.save_task(t5)
        loop.recover_orphan(t5)
        saved5 = store.jload(os.path.join(ctx.FD, "tasks", "0121-c.json"))

        check("orphan recovery: first scan pass with STARTUP_GRACE_DONE False does not increment orphan_streak",
              saved4.get("status") == "pending"
              and saved4.get("orphan_streak") == 2
              and loop.STARTUP_GRACE_DONE is True
              and saved5.get("status") == "pending"
              and saved5.get("orphan_streak") == 3)
    finally:
        loop.STARTUP_GRACE_DONE = old_grace
        ctx.ROOT, ctx.FD, sbx.RUNS, sbx.LIMITS_FILE = old_root, old_fd, old_runs, old_limits
        shutil.rmtree(d, ignore_errors=True)


GROUPS = [test_runtimes_egress_budgets, test_owner_and_insights, test_orphan_recovery]
