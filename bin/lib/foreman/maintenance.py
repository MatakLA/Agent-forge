"""LLM-free maintenance while the team is idle: the suite on main 3x (red main, flaky tests), modules without a
docstring, old transcripts compressed."""
import json, os, re, time

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
                           sbx.gate_image(), "-c", cmd], timeout=int(os.environ.get("GATE_TIMEOUT", "1500")))
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
    if any(t["status"] in ("running", "gate", "review", "splitting", "merge-ready") for t in ts):
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


_EGRESS_CHECKED = [0.0]


def maybe_rotate_egress():
    """Rotate the egress proxy's logs (only once access.log is over 5 MB), checked at most hourly and also while paused: an unrotated
    log grew to 160+ MB and made every egress report - and the dashboard's Egress tab - take half a minute."""
    if "egress:rotate" in INFLIGHT or time.time() - _EGRESS_CHECKED[0] < 3600:
        return
    _EGRESS_CHECKED[0] = time.time()
    submit("egress:rotate", step_rotate_egress)


def step_rotate_egress():
    import egress
    msg = egress.rotate()
    if not msg.startswith("no rotation needed"):
        event("egress: " + msg)
    return lambda: None


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


# ---------------------------------------------------------------- disk: what takes the space, and one-click cleanups
_UNITS = {"b": 1, "kb": 1000, "mb": 1000 ** 2, "gb": 1000 ** 3, "tb": 1000 ** 4}


def _bytes(txt):
    """'1.2GB' / '824kB' / '0B' (Docker's decimal units) -> bytes; 0 for anything unreadable."""
    m = re.match(r"^\s*([\d.]+)\s*([a-zA-Z]*)", txt or "")
    try:
        return int(float(m.group(1)) * _UNITS.get(m.group(2).lower() or "b", 1)) if m else 0
    except ValueError:
        return 0


def _human(n):
    for u in ("B", "kB", "MB", "GB", "TB"):
        if n < 1000 or u == "TB":
            return f"{n:.0f}{u}" if u == "B" else f"{n:.1f}{u}"
        n /= 1000


def docker_summary():
    """Docker's space use as rows {Type, TotalCount, Size, Reclaimable}. `docker system df` measures every container's writable layer
    and every volume, which takes minutes on a big disk, so this uses the fast listings: images and build cache are sized (images
    are summed, shared layers counted once per image, hence '~'), containers and volumes are only counted."""
    rows = []
    rc, out, _ = sh(["docker", "image", "ls", "-a", "--format", "{{.Size}}|{{.Repository}}"], timeout=20)
    if rc == 0:
        imgs = [l.split("|", 1) for l in out.splitlines() if l]
        rows.append(dict(Type="Images", TotalCount=str(len(imgs)), Size="~" + _human(sum(_bytes(a) for a, _ in imgs)),
                         Reclaimable=_human(sum(_bytes(a) for a, r in imgs if r == "<none>"))))
    rc, out, _ = sh(["docker", "builder", "du"], timeout=30)
    if rc == 0:
        m = {k: v.strip() for k, v in (l.split(":", 1) for l in out.splitlines() if re.match(r"^(Reclaimable|Total):", l))}
        n = len([l for l in out.splitlines() if re.match(r"^[a-z0-9]{20,}\s", l)])
        if m:
            rows.append(dict(Type="Build cache", TotalCount=str(n), Size=m.get("Total", "?"), Reclaimable=m.get("Reclaimable", "?")))
    for typ, cmd in (("Containers", ["docker", "ps", "-aq"]), ("Local volumes", ["docker", "volume", "ls", "-q"])):
        rc, out, _ = sh(cmd, timeout=20)
        if rc == 0:
            rows.append(dict(Type=typ, TotalCount=str(len(out.split())), Size="-", Reclaimable="-"))
    return rows


def disk_usage():
    """runs/ by kind (transcripts, compressed transcripts, the rest) and by project, how many old transcripts could be compressed,
    Docker's own accounting (images, containers, volumes, build cache) and the free space - for the dashboard's System view."""
    import shutil
    keep = int(ctx.C["RUNS_KEEP_DAYS"])
    cutoff = time.time() - keep * 86400
    tot = dict(transcripts=0, compressed=0, other=0, runs=0, old_uncompressed=0, old_bytes=0)
    by_project = {}
    for d in sbx.run_dirs():
        tot["runs"] += 1
        proj = sbx.meta(d).get("project", "") or "default"
        for f in os.listdir(d):
            p = os.path.join(d, f)
            try:
                n = os.path.getsize(p)
            except OSError:
                continue
            kind = "transcripts" if f == "stream.jsonl" else "compressed" if f == "stream.jsonl.gz" else "other"
            tot[kind] += n
            by_project[proj] = by_project.get(proj, 0) + n
            if f == "stream.jsonl" and os.path.exists(os.path.join(d, "finished")) and os.path.getmtime(p) < cutoff:
                tot["old_uncompressed"] += 1; tot["old_bytes"] += n
    docker = docker_summary()
    du = shutil.disk_usage(sbx.RUNS if os.path.isdir(sbx.RUNS) else sbx.ROOT)
    return dict(runs=tot, by_project=by_project, keep_days=keep, docker=docker, free=du.free, total=du.total)


def cleanup(action, days=None):
    """compress: gzip transcripts of finished runs older than `days` (RUNS_KEEP_DAYS) - nothing is lost.
    transcripts: delete transcripts (plain and gzip) of finished runs older than `days` - the numbers stay (meta.json, ledger).
    docker: remove dangling images and the build cache (never a tagged image, container or volume). Returns a message."""
    days = int(days if days is not None else ctx.C["RUNS_KEEP_DAYS"])
    if action == "compress":
        n, saved = compress_old_runs(days)
        msg = f"compressed the transcripts of {n} run(s) older than {days} days ({saved / 2**20:.0f} MB saved)"
    elif action == "transcripts":
        if days < 1:
            return "refused: keep at least 1 day"
        cutoff, n, freed = time.time() - days * 86400, 0, 0
        for d in sbx.run_dirs():
            if not os.path.exists(os.path.join(d, "finished")) or os.path.getmtime(os.path.join(d, "finished")) > cutoff:
                continue
            for f in ("stream.jsonl", "stream.jsonl.gz"):
                p = os.path.join(d, f)
                if os.path.exists(p):
                    freed += os.path.getsize(p); os.remove(p); n += 1
        msg = f"deleted {n} transcript(s) of runs older than {days} days ({freed / 2**20:.0f} MB freed; the run numbers are kept)"
    elif action == "docker":
        r1 = sh(["docker", "image", "prune", "-f"], timeout=600)
        r2 = sh(["docker", "builder", "prune", "-f"], timeout=600)
        rec = [l for l in (r1[1] + r2[1]).splitlines() if "reclaimed" in l.lower()]
        msg = "docker: " + ("; ".join(rec) or "nothing to remove") if r1[0] == 0 else f"docker prune failed: {(r1[2] or r1[1])[-200:]}"
    else:
        return "unknown cleanup (compress | transcripts | docker)"
    event("cleanup: " + msg)
    return msg
