"""Shared helpers for bin/dispatch, bin/run-supervisor, bin/killswitch and bin/status (V7)."""
import fcntl, json, os, subprocess, time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Test hooks are honoured only when SANDBOX_TEST=1 (used to test limits without spending quota).
TEST_MODE = os.environ.get("SANDBOX_TEST") == "1"
RUNS = os.environ.get("SANDBOX_RUNS_DIR") if TEST_MODE and os.environ.get("SANDBOX_RUNS_DIR") else os.path.join(ROOT, "runs")
LIMITS_FILE = os.environ.get("LIMITS_FILE") if TEST_MODE and os.environ.get("LIMITS_FILE") else os.path.join(ROOT, "etc", "limits.conf")

def _kv(path):
    out = {}
    try:
        for line in open(path):
            line = line.split("#", 1)[0].strip()
            if "=" in line:
                k, v = (x.strip() for x in line.split("=", 1))
                out[k] = v.strip('"')
    except FileNotFoundError:
        pass
    return out


# Where the git forge lives and which repository the team works on (etc/forge.conf or the environment; see docs/SETUP.md).
_FORGE = _kv(os.path.join(ROOT, "etc", "forge.conf"))
FORGE_URL = os.environ.get("FORGE_URL") or _FORGE.get("FORGE_URL", "http://127.0.0.1:3000")
FORGE_REPO = os.environ.get("FORGE_REPO") or _FORGE.get("FORGE_REPO", "sandbox/playground")
FORGE_ORG = FORGE_REPO.split("/")[0]
WORKER_CLONE = "/home/worker/playground"
SECRETS = os.path.expanduser(os.environ.get("SANDBOX_SECRETS") or "~/.config/sandbox/secrets")
DEFAULTS = dict(RUN_TIMEOUT_MIN=30, RUN_TIMEOUT_MAX_MIN=45, RUN_TOKEN_CAP=1500000, DAILY_TOKEN_BUDGET=3000000,
                MAX_RUNS_PER_DAY=6, MAX_PARALLEL=2, QUOTA_MODEL_FILTER="Gemini", QUOTA_MIN_5H_PCT=25,
                QUOTA_MIN_WEEK_PCT=15, CONSEC_FAIL_TRIP=3, POLL_SEC=2, WORKER_MODEL="gemini-3.8-flash-high")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_limits():
    lim = dict(DEFAULTS)
    try:
        for line in open(LIMITS_FILE):
            line = line.split("#", 1)[0].strip()
            if "=" not in line:
                continue
            k, v = (x.strip() for x in line.split("=", 1))
            if k not in DEFAULTS:
                raise SystemExit(f"unknown key in {LIMITS_FILE}: {k}")
            lim[k] = v if isinstance(DEFAULTS[k], str) else float(v)
    except FileNotFoundError:
        raise SystemExit(f"limits file missing: {LIMITS_FILE} (refusing to run without limits)")
    for k in ("RUN_TOKEN_CAP", "DAILY_TOKEN_BUDGET", "MAX_RUNS_PER_DAY", "MAX_PARALLEL", "CONSEC_FAIL_TRIP"):
        lim[k] = int(lim[k])
    return lim


def ensure_runs():
    os.makedirs(RUNS, exist_ok=True)


def lock():
    ensure_runs()
    f = open(os.path.join(RUNS, ".lock"), "w")
    fcntl.flock(f, fcntl.LOCK_EX)
    return f


def alert(msg):
    ensure_runs()
    with open(os.path.join(RUNS, "ALERT"), "a") as f:
        f.write(f"{now()} {msg}\n")


def ks_path():
    return os.path.join(RUNS, "KILLSWITCH")


def killswitch_active():
    try:
        return json.load(open(ks_path()))
    except FileNotFoundError:
        return None
    except Exception:
        return {"reason": "unreadable kill switch file (treated as ON)", "at": "?", "by": "?"}


def killswitch_on(reason, by="orchestrator", hard=False):
    ensure_runs()
    if not killswitch_active():
        json.dump({"at": now(), "reason": reason, "by": by, "hard": hard}, open(ks_path(), "w"))
        alert(f"KILLSWITCH ON by {by}: {reason}")


def killswitch_off():
    try:
        os.remove(ks_path())
        alert("KILLSWITCH OFF")
        return True
    except FileNotFoundError:
        return False


def ledger_path():
    return os.path.join(RUNS, "ledger.jsonl")


def read_ledger():
    out = []
    try:
        for line in open(ledger_path()):
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return out


def append_ledger(entry):
    ensure_runs()
    with open(ledger_path(), "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def read(p, default=""):
    try:
        return open(p).read().strip()
    except Exception:
        return default


def run_dirs():
    try:
        return sorted(os.path.join(RUNS, d) for d in os.listdir(RUNS) if os.path.isdir(os.path.join(RUNS, d)) and "-worker-" in d)
    except FileNotFoundError:
        return []


def is_running(d):
    return not os.path.exists(d + "/finished") and os.path.exists(d + "/pid") and alive(read(d + "/pid"))


def meta(d):
    try:
        return json.load(open(d + "/meta.json"))
    except Exception:
        return {}


def stream_tokens(path):
    """Sum of per-step usage of DONE steps == result.usage.total_tokens (validated on real runs)."""
    tot = 0
    try:
        for line in open(path):
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("event") == "step_update":
                s = e["step_update"]
                if s.get("state") == "DONE":
                    tot += (s.get("usage") or {}).get("total_tokens", 0)
    except FileNotFoundError:
        pass
    return tot


def parse_usage(text):
    rows = []
    for line in text.splitlines():
        p = [x.strip() for x in line.split("\t")]
        if len(p) >= 3 and p[2].endswith("%"):
            try:
                rows.append(dict(model=p[0], label=p[1], pct=float(p[2][:-1]), reset=p[3] if len(p) > 3 else ""))
            except ValueError:
                pass
    return rows


def read_quota(worker):
    r = subprocess.run(["docker", "exec", worker, "agy-run", "-p", "/usage", "--print-timeout", "60s"],
                       capture_output=True, text=True, timeout=120)
    return parse_usage(r.stdout)


def quota_verdict(rows, lim):
    """Returns (ok, message). Fail closed when nothing relevant could be parsed."""
    flt = str(lim["QUOTA_MODEL_FILTER"]).lower()
    seen5 = seenw = False
    for r in rows:
        if flt not in r["model"].lower():
            continue
        lab = r["label"].lower()
        if "five hour" in lab:
            seen5 = True
            if r["pct"] < lim["QUOTA_MIN_5H_PCT"]:
                return False, f"quota low: {r['model']} 5-hour window {r['pct']:.0f}% < {lim['QUOTA_MIN_5H_PCT']:.0f}% (resets {r['reset']})"
        elif "week" in lab:
            seenw = True
            if r["pct"] < lim["QUOTA_MIN_WEEK_PCT"]:
                return False, f"quota low: {r['model']} weekly {r['pct']:.0f}% < {lim['QUOTA_MIN_WEEK_PCT']:.0f}% (resets {r['reset']})"
    if not (seen5 and seenw):
        return False, "cannot determine quota (/usage unreadable or no matching rows) - refusing (fail closed)"
    return True, "quota ok"


AUDIT_FILES = ("ledger.jsonl", "ALERT", os.path.join("audit", "orchestrator-actions.jsonl"))


def audit_integrity(update=True):
    """Audit logs may only grow (or shrink through bin/audit-rotate). Compares sizes with the recorded high-water marks;
    returns a list of problems. With update=True the marks follow legitimate growth."""
    hwp = os.path.join(RUNS, "audit", "highwater.json")
    try:
        hw = json.load(open(hwp))
    except Exception:
        hw = {}
    problems = []
    for n in AUDIT_FILES:
        p = os.path.join(RUNS, n)
        size = os.path.getsize(p) if os.path.exists(p) else 0
        prev = hw.get(n, 0)
        if size < prev:
            problems.append(f"audit log {n} shrank from {prev} to {size} bytes without a recorded rotation (manual truncation or deletion?)")
        elif update and size != prev:
            hw[n] = size
    if update:
        os.makedirs(os.path.dirname(hwp), exist_ok=True)
        json.dump(hw, open(hwp, "w"))
    return problems


def audit_highwater_reset(name):
    """Called by bin/audit-rotate after a legitimate rotation."""
    hwp = os.path.join(RUNS, "audit", "highwater.json")
    try:
        hw = json.load(open(hwp))
    except Exception:
        hw = {}
    p = os.path.join(RUNS, name)
    hw[name] = os.path.getsize(p) if os.path.exists(p) else 0
    os.makedirs(os.path.dirname(hwp), exist_ok=True)
    json.dump(hw, open(hwp, "w"))
