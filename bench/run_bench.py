#!/usr/bin/env python3
"""Worker token benchmark: the same task (scenario.task.md on the `tasklog` repo) given to every Agent-Forge version's worker prompt.
Usage:  run_bench.py static                  prompt size per version (offline, free) -> results/static.json
        run_bench.py run <version> [reps]    real runs on the lent worker (costs model quota) -> results/runs.jsonl
        run_bench.py chart                   results/chart.html from static.json + runs.jsonl
Invariants: a version is a git commit of agent-forge checked out as a worktree; its own worker_spec() renders the prompt; only the
prompt differs between versions (same dispatch, model, worker, repo, task)."""
import json, os, re, shlex, statistics, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
AF = os.path.dirname(HERE)
WORK, RES = os.path.join(HERE, ".work"), os.path.join(HERE, "results")
VERSIONS = json.load(open(os.path.join(HERE, "versions.json")))
# Command that reaches the machine Agent-Forge runs on, e.g. BENCH_SSH="ssh -i ~/.ssh/forge_key dev@forge-vm". Only `run` needs it.
SSH = shlex.split(os.environ.get("BENCH_SSH", ""))
PROJECT, WORKER, MODEL = "bench", os.environ.get("BENCH_WORKER", "worker-8"), os.environ.get("BENCH_MODEL", "gemini-3.8-flash-high")
MINUTES = int(os.environ.get("BENCH_MINUTES", "75"))


def run(*cmd, cwd=None, inp=None, check=True):
    p = subprocess.run(cmd, cwd=cwd, input=inp, capture_output=True, text=True)
    if check and p.returncode:
        sys.exit(f"{' '.join(cmd)[:120]} failed: {p.stderr[-400:]}")
    return p.stdout


def task_dict(branch):
    md = open(os.path.join(HERE, "scenario.task.md")).read()
    spec = md.split("## The task", 1)[1].split("## Acceptance criteria", 1)[0].strip()
    acc = [l[2:].strip() for l in md.split("## Acceptance criteria", 1)[1].splitlines() if l.startswith("- ")]
    return dict(id="0001-total", title="Add `tasklog total`", goal="G-bench", branch=branch, role="backend-developer", risk="routine",
                files=["cli.py", "tests/test_total.py"], read=["clock.py", "storage.py"], spec=spec, acceptance=acc, attempts=0)


def clone():
    d = os.path.join(WORK, "clone")
    if not os.path.isdir(d):
        os.makedirs(WORK, exist_ok=True)
        run("cp", "-r", os.path.join(HERE, "scenario"), d)
        run("git", "init", "-q", "-b", "main", cwd=d)
        run("git", "add", "-A", cwd=d)
        run("git", "-c", "user.name=b", "-c", "user.email=b@b", "commit", "-qm", "scenario", cwd=d)
        run("git", "update-ref", "refs/remotes/origin/main", "HEAD", cwd=d)
    return d


def worktree(v):
    sha = run("git", "rev-parse", v["commit"], cwd=AF).strip()
    d = os.path.join(WORK, "wt-" + sha[:9])
    if not os.path.isdir(d):
        run("git", "worktree", "add", "-q", "--detach", d, sha, cwd=AF)
    pc = os.path.join(d, "projects", PROJECT)
    if not os.path.isdir(pc):
        os.makedirs(pc)
        open(os.path.join(pc, "project.conf"), "w").write("FORGE_REPO=sandbox/bench\nPROJECT_DIR=\n")
    return d


def render(v, branch):
    os.makedirs(WORK, exist_ok=True)
    tf = os.path.join(WORK, "task-" + v["id"] + ".json")
    json.dump(task_dict(branch), open(tf, "w"))
    env = dict(os.environ, FORGE_PROJECT=PROJECT, **v["env"])
    p = subprocess.run([sys.executable, os.path.join(HERE, "render_prompt.py"), clone(), tf], cwd=worktree(v), env=env, capture_output=True, text=True)
    if p.returncode:
        sys.exit(f"render {v['id']} failed: {p.stderr[-500:]}")
    return p.stdout


def static():
    os.makedirs(RES, exist_ok=True)
    out = []
    for v in VERSIONS:
        text = render(v, "bench_" + v["id"])
        out.append(dict(id=v["id"], label=v["label"], date=v["date"], chars=len(text), est_tokens=len(text) // 4))
        print(f"{v['id']}  {len(text):>7} chars  ~{len(text) // 4:>6} tokens  {v['label']}")
    json.dump(out, open(os.path.join(RES, "static.json"), "w"), indent=1)


def stream_stats(raw):
    tools, reads, tokens, kinds = 0, 0, 0, {}
    for line in raw.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        s = e.get("step_update") or {}
        if s.get("step_type") == "tool" and s.get("state") == "DONE":
            tools += 1
        blob = json.dumps(e)
        if re.search(r'"(cat|sed|head|tail|view_file|read_file)\b', blob) and s.get("step_type") == "tool" and s.get("state") == "DONE":
            reads += 1
        tokens = max(tokens, (s.get("usage") or {}).get("total_tokens", 0))
    return tools, reads


def weekly(rows):
    for r in rows or []:
        if r.get("model", "").startswith("Gemini") and "Weekly" in r.get("label", ""):
            return r.get("pct")
    return None


def real(vid, reps):
    v = next(x for x in VERSIONS if x["id"] == vid)
    os.makedirs(RES, exist_ok=True)
    for i in range(reps):
        branch = f"bench_{vid}_{int(time.time())}"
        prompt = render(v, branch)
        remote = f"/tmp/{branch}.md"
        run(*SSH, f"cat > {remote}", inp=prompt)
        env = f"FORGE_PROJECT={PROJECT} FORGE_REPO=sandbox/bench DISPATCH_BRANCH={branch} DISPATCH_MODEL={MODEL} DISPATCH_VARIANT=bench-{vid}"
        for _ in range(180):                      # the lent worker may still be finishing its last foreman task
            if "busy" not in run(*SSH, f"cd ~/sandbox && {env} bin/dispatch --check {WORKER} 2>&1", check=False):
                break
            time.sleep(20)
        out = run(*SSH, f"cd ~/sandbox && {env} bin/dispatch {WORKER} {remote} {MINUTES} 2>&1", check=False)
        m = re.search(r"-> (\S+) \(", out)
        if not m:
            sys.exit("dispatch refused: " + out.strip()[-300:])
        rundir = m.group(1)
        t0 = time.time()
        while time.time() - t0 < (MINUTES + 15) * 60:
            meta = json.loads(run(*SSH, f"cat {rundir}/meta.json"))
            if meta.get("finished"):
                break
            time.sleep(20)
        raw = run(*SSH, f"cat {rundir}/stream.jsonl", check=False)
        tools, reads = stream_stats(raw)
        chk = run(*SSH, f"cd ~/sandbox && FORGE_PROJECT={PROJECT} FORGE_REPO=sandbox/bench python3 bench/collect.py {branch}", check=False).strip().splitlines()
        check = json.loads(chk[-1]) if chk and chk[-1].startswith("{") else {}
        row = dict(version=vid, run=os.path.basename(rundir), branch=branch, tokens=meta.get("tokens", 0), tool_calls=meta.get("tool_calls", tools),
                   read_calls=reads, duration_s=meta.get("duration_s", 0), reason=meta.get("reason"), prompt_tokens_est=len(prompt) // 4,
                   quota_week_before=weekly(meta.get("quota_before")), quota_week_after=weekly(meta.get("quota_after")), **check)
        with open(os.path.join(RES, "runs.jsonl"), "a") as f:
            f.write(json.dumps(row) + "\n")
        os.makedirs(os.path.join(RES, "raw"), exist_ok=True)
        open(os.path.join(RES, "raw", row["run"] + ".jsonl"), "w").write(raw)
        print(json.dumps(row))
        if meta.get("reason") == "quota":
            sys.exit("quota exhausted - stopping")


def chart():
    st = {r["id"]: r for r in json.load(open(os.path.join(RES, "static.json")))} if os.path.exists(os.path.join(RES, "static.json")) else {}
    runs = [json.loads(l) for l in open(os.path.join(RES, "runs.jsonl"))] if os.path.exists(os.path.join(RES, "runs.jsonl")) else []
    rows = []
    for v in VERSIONS:
        rs = [r for r in runs if r["version"] == v["id"] and r.get("reason") in ("ok", None, "")]
        rows.append(dict(v=v, static=st.get(v["id"]), tokens=[r["tokens"] for r in rs], tools=[r["tool_calls"] for r in rs],
                         good=sum(1 for r in rs if r.get("tests_pass") and r.get("output_ok") and r.get("wired")), n=len(rs)))
    html = open(os.path.join(HERE, "chart.template.html")).read().replace("/*DATA*/", "const DATA = " + json.dumps(rows) + ";")
    open(os.path.join(RES, "chart.html"), "w").write(html)
    print("results/chart.html")


if __name__ == "__main__":
    cmd = sys.argv[1:2]
    if cmd == ["static"]:
        static()
    elif cmd == ["run"]:
        for vid in sys.argv[2].split(","):
            real(vid, int(sys.argv[3]) if len(sys.argv) > 3 else 1)
    elif cmd == ["chart"]:
        chart()
    else:
        print(__doc__); sys.exit(2)
