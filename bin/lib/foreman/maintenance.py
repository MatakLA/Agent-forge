"""LLM-free maintenance while the team is idle: the suite on main 3x (red main, flaky tests), modules without a
docstring, old transcripts compressed."""
import os, time

import sbx

from . import ctx
from .util import sh
from .store import event, goals, gstate, jload, jsave, locked, new_goal_id, save_goal, state_tx, tasks
from .jobs import INFLIGHT, PHASE, submit
from .pool import pool_quota_out
from .repo import REPO_LOCK, codemap_main, refresh_repo


MAINT_PREFIX = "[maintenance]"


def run_suite_on_main():
    """The full test suite on main exactly like the gate runs it (fresh offline container, read-only source, the project's
    TEST_CMD in its GATE_IMAGE). (rc, summary line, failing tests). pytest's "no tests" (5) is reported as 0."""
    cmd = sbx.test_cmd()
    with REPO_LOCK:
        rc, out, err = sh(["docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,exec,size=256m", "--cap-drop", "ALL",
                           "--security-opt", "no-new-privileges", "--memory", "1g", "--cpus", "1", "--pids-limit", "256",
                           "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "HOME=/tmp", "-v", f"{ctx.REPO}:/src:ro", "-w", "/src", "--entrypoint", "sh",
                           sbx.gate_image(), "-c", cmd], timeout=600)
    lines = (out + err).strip().splitlines()
    fails = [l for l in lines if l.startswith(("FAILED ", "ERROR "))]
    if rc == 5 and sbx.is_pytest(cmd):
        rc = 0
    return rc, (lines[-1] if lines else "(no output)")[:200], fails[:10]


def maybe_maintenance():
    """MAINTENANCE: while the team is idle (nobody running, every worker out of quota or nothing to do), check main once per new
    main commit and at most every 6 h - without any LLM: the full suite 3x (red main / flaky tests) and the code map (modules
    without a docstring). Findings become goals/backlog entries, so the team starts on them when quota is back."""
    if not ctx.C["MAINTENANCE"] or "maint:main" in INFLIGHT:
        return
    ts = tasks()
    if any(t["status"] in ("running", "gate", "review", "splitting") for t in ts):
        return
    st = gstate()
    if time.time() - st.get("maint_at", 0) < 6 * 3600:
        return
    rc, sha, _ = sh(["git", "rev-parse", "origin/main"], cwd=ctx.REPO)
    if not sha.strip() or sha.strip() == st.get("maint_sha"):
        return
    if any(t["status"] == "pending" for t in ts) and not pool_quota_out():
        return
    PHASE["main"] = "maintenance: tests on main 3x, code map (no AI)"
    submit("maint:main", step_maintenance)


def compress_old_runs(days):
    """gzip the transcripts (stream.jsonl) of finished runs older than `days`; meta.json, the ledger and run-stats keep all
    the numbers the dashboard needs. Transcripts are most of runs/ (several MB per run). Returns (runs, bytes saved)."""
    import gzip, shutil
    cutoff, n, saved = time.time() - days * 86400, 0, 0
    for d in sbx.run_dirs():
        s = os.path.join(d, "stream.jsonl")
        if not os.path.exists(os.path.join(d, "finished")) or not os.path.exists(s) or os.path.getmtime(s) > cutoff:
            continue
        try:
            size = os.path.getsize(s)
            with open(s, "rb") as fi, gzip.open(s + ".gz", "wb") as fo:
                shutil.copyfileobj(fi, fo)
            saved += size - os.path.getsize(s + ".gz")
            os.remove(s)
            n += 1
        except OSError:
            pass
    return n, saved


def step_maintenance():
    try:
        n, saved = compress_old_runs(int(ctx.C["RUNS_KEEP_DAYS"]))
        if n:
            event(f"maintenance: compressed the transcripts of {n} run(s) older than {ctx.C['RUNS_KEEP_DAYS']} days ({saved / 2**20:.0f} MB saved)")
    except Exception as e:
        event(f"maintenance: compressing old runs failed: {e}")
    refresh_repo()
    rc, sha, _ = sh(["git", "rev-parse", "origin/main"], cwd=ctx.REPO)
    runs = [run_suite_on_main() for _ in range(3)]
    cm = codemap_main()
    nodoc = [p for p, e in sorted(cm.items()) if not e["test"] and p.endswith(".py") and not e["purpose"]]

    def apply():
        with state_tx() as st:
            st.update(maint_at=time.time(), maint_sha=sha.strip(),
                      maint=dict(at=sbx.now(), sha=sha.strip()[:10], suite=[dict(rc=r[0], summary=r[1]) for r in runs], nodoc=len(nodoc)))
        with locked():
            _maintenance_findings(runs, nodoc, sha)
    return apply


def _maintenance_findings(runs, nodoc, sha):
    """Turn the maintenance results into work (call under locked()). Pure enough to selftest with fake runs."""
    bl = jload(os.path.join(ctx.FD, "backlog.json"), [])
    open_goals = " ".join(g["text"] for g in goals() if g["status"] in ("new", "active"))
    found = []
    # pytest exit codes: 0 passed, 1 tests failed, 2 collection/usage error. 5 = no tests at all (a new project) and 125-127 =
    # docker/image trouble say nothing about main, so they never count as red and never as "flaky".
    real = [r for r in runs if r[0] in (0, 1, 2)]
    red = [r for r in real if r[0] != 0]
    if real and len(red) == len(runs) and f"{MAINT_PREFIX} main is red" not in open_goals:
        fails = sorted({f for r in runs for f in r[2]})
        gid = new_goal_id("-m")
        save_goal(dict(id=gid, text=f"{MAINT_PREFIX} main is red: the full test suite fails on main ({runs[0][1]}). Make it green "
                       f"again without weakening tests. Failing: {'; '.join(fails)[:800] or 'see the test output'}",
                       status="new", created=sbx.now(), source="maintenance"))
        found.append("main is red - goal " + gid)
    elif red and len(red) < len(real):
        flaky = sorted({f for r in runs for f in r[2]})
        text = f"{MAINT_PREFIX} flaky tests: the same main passed and failed in 3 runs. Make them deterministic: {'; '.join(flaky)[:600]}"
        if not any(b.startswith(f"{MAINT_PREFIX} flaky tests") for b in bl):
            bl.insert(0, text); found.append("flaky tests")
    if len(real) < len(runs):
        found.append(f"{len(runs) - len(real)} suite run(s) could not run (exit {', '.join(str(r[0]) for r in runs if r not in real)})")
    if nodoc and not any(b.startswith(f"{MAINT_PREFIX} module docstrings") for b in bl):
        bl.append(f"{MAINT_PREFIX} module docstrings: give every module a docstring (purpose, public interface, invariants; 3-8 lines, "
                  f"no code changes) - the team's code map and context packs are built from them. Modules: {', '.join(nodoc[:30])}")
        found.append(f"{len(nodoc)} module(s) without a docstring")
    jsave(os.path.join(ctx.FD, "backlog.json"), bl)
    event("maintenance on main " + sha.strip()[:10] + ": suite " + " / ".join(r[1] for r in runs)[:200] +
          (" - " + "; ".join(found) if found else " - nothing to do"))
    return found
