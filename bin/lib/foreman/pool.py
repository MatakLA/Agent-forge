"""The worker pool: which worker containers this project may use, their runtime, their remaining quota and cooldowns."""
import datetime as dt, os, re, time

import sbx, runtimes

from . import ctx
from .util import sh


WORKER_RT = {}           # worker -> runtime (agy | claude | codex | gemini), refreshed by workers()


def workers():
    """This project's worker pool: the running worker containers, limited to WORKERS when the project sets one."""
    rc, out, _ = sh(["docker", "ps", "--format", '{{.Names}}|{{.Label "agent-forge.runtime"}}'], timeout=30)
    pool = {w.strip() for w in str(ctx.C["WORKERS"]).split(",") if w.strip()}
    found = []
    for line in out.splitlines():
        name, _, rt = line.partition("|")
        if re.fullmatch(r"worker-\d+", name) and (not pool or name in pool):
            WORKER_RT[name] = rt if rt in runtimes.RUNTIMES else "agy"
            found.append(name)
    return sorted(found)


def runtime_of(w):
    return WORKER_RT.get(w) or "agy"


def api_budget_left(w=""):
    """One more API run fits every money cap that applies (install, this project, and the worker `w` if given)."""
    return sbx.budget_check(w, sbx.load_limits(), conf=sbx.project_conf())[0]


def worker_busy(w):
    return any(d.endswith("-" + w) and sbx.is_running(d) for d in sbx.run_dirs())


RESERVED = set()          # idle workers lent to an agy LLM call (kept for the dashboard; busy workers are used without reserving)


QUOTA_CACHE = {}          # worker -> (epoch, rows from `agy /usage`)


def quota_rows(w, max_age=300):
    at, rows = QUOTA_CACHE.get(w, (0, None))
    if rows is None or time.time() - at > max_age:
        try:
            rows = sbx.read_quota(w)
        except Exception:
            rows = []
        QUOTA_CACHE[w] = (time.time(), rows)
    return rows


def quota_pct(rows, family, label="five hour"):
    """Lowest remaining % of a model family (e.g. 'gemini', 'claude') for a window; -1 when unknown."""
    vals = [r["pct"] for r in rows if family.lower() in r.get("model", "").lower() and label in r.get("label", "").lower()]
    return min(vals) if vals else -1


def has_quota(rows, family, floor=2.0):
    """False when `agy /usage` says this account's model family is (nearly) exhausted in either window - skip it right away instead
    of waiting ~2 minutes for agy to answer "Individual quota reached". Unknown (no rows) counts as available."""
    for label in ("five hour", "week"):
        p = quota_pct(rows, family, label)
        if 0 <= p <= floor:
            return False
    fam_rows = [r for r in rows if family.lower() in r.get("model", "").lower()]
    return not (fam_rows and not any("five hour" in r.get("label", "").lower() for r in fam_rows)
                and any(r["pct"] <= floor for r in fam_rows))


def pool_quota_out():
    """True when no worker of this project can start a run: every one is below the dispatch quota floor (or paused for it)."""
    ws, lim, now = workers(), sbx.load_limits(), time.time()
    if not ws:
        return False
    if any(runtimes.is_api(runtime_of(w)) for w in ws) and api_budget_left():
        return False
    for w in [w for w in ws if not runtimes.is_api(runtime_of(w))]:
        until, why = COOLDOWN.get(w, (0, ""))
        if until > now and why.startswith("quota low"):
            continue
        if sbx.quota_verdict(quota_rows(w, 900), lim)[0]:
            return False
    return True


def order_by_quota(ws, pct_of, last_used):
    """Most remaining quota first; on a tie the least recently used. Pure, selftested."""
    return sorted(ws, key=lambda w: (-pct_of(w), last_used.get(w, 0), w))


# ---------------------------------------------------------------- the loop
def init_worker_dispatch_history():
    d = {}
    for r in sbx.run_dirs():
        m = re.match(r"^(\d{8}T\d{6}Z)-(worker-\d+)$", os.path.basename(r))
        if m:
            ts_str, w = m.groups()
            try:
                t = dt.datetime.strptime(ts_str, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc).timestamp()
                if t > d.get(w, 0):
                    d[w] = t
            except Exception:
                pass
    return d


LAST_DISPATCHED = init_worker_dispatch_history()


COOLDOWN = {}             # worker -> (epoch second, human-readable reason)


LAST_QUOTA_MSG = {}       # worker -> the last "quota low" message logged (events only when it changes)
