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
# Several projects can share one Agent-Forge install (each with its own foreman, repository and worker pool: bin/foreman --project).
# FORGE_PROJECT is set by bin/foreman for everything it starts; "" = the default project (legacy single-project layout).
PROJECT = os.environ.get("FORGE_PROJECT", "")
if PROJECT and not __import__("re").fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", PROJECT):
    raise SystemExit(f"invalid FORGE_PROJECT {PROJECT!r} (lowercase letters, digits, dashes)")
WORKER_CLONE = f"/home/worker/work/{PROJECT}" if PROJECT else "/home/worker/playground"
SAFE_ARG = __import__("re").compile(r"[A-Za-z0-9._/:-]{1,200}")
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
        f.write(f"{now()} {'[' + PROJECT + '] ' if PROJECT else ''}{msg}\n")


# Kill switches: one per project (the default project keeps the original runs/KILLSWITCH) plus one for ALL projects
# (bin/killswitch on --all). A project is stopped when its own or the global one is on.
def ks_path(project=None):
    p = PROJECT if project is None else project
    return os.path.join(RUNS, "projects", p, "KILLSWITCH") if p else os.path.join(RUNS, "KILLSWITCH")


def ks_all_path():
    return os.path.join(RUNS, "KILLSWITCH-ALL")


def _ks_read(path):
    try:
        return json.load(open(path))
    except FileNotFoundError:
        return None
    except Exception:
        return {"reason": "unreadable kill switch file (treated as ON)", "at": "?", "by": "?"}


def killswitch_active():
    ks = _ks_read(ks_all_path())
    if ks:
        return dict(ks, scope="all")
    ks = _ks_read(ks_path())
    return dict(ks, scope="project") if ks else None


def project_pool(project=None):
    """The worker containers a project may use (its WORKERS setting), or None for 'every worker'."""
    p = PROJECT if project is None else project
    conf = _kv(os.path.join(ROOT, "projects", p, "project.conf")) if p else _kv(os.path.join(ROOT, "etc", "foreman.conf"))
    pool = {w.strip() for w in conf.get("WORKERS", "").split(",") if w.strip()}
    return pool or None


def killswitch_on(reason, by="orchestrator", hard=False, all_projects=False):
    ensure_runs()
    path = ks_all_path() if all_projects else ks_path()
    if not _ks_read(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump({"at": now(), "reason": reason, "by": by, "hard": hard, "project": "ALL" if all_projects else (PROJECT or "default")},
                  open(path, "w"))
        alert(f"KILLSWITCH ON{' (all projects)' if all_projects else ''} by {by}: {reason}")
        if by not in ("killswitch-cli", "dashboard"):           # automatic trips only - the owner knows about their own
            notify("killswitch","Kill switch ON" + (" (all projects)" if all_projects else ""), f"{reason}\n(by {by})")


# ---- how a project is tested (TEST_CMD / GATE_IMAGE in its config): the gate, the self-check and maintenance all use it
DEFAULT_TEST_CMD = "python3 -m pytest -q -p no:cacheprovider"
DEFAULT_GATE_IMAGE = "sandbox-worker:1.2.11"
TEST_CMD_RE = __import__("re").compile(r"[A-Za-z0-9 ._/=:,+@%-]{1,200}")       # a plain command line: no shell syntax
IMAGE_RE = __import__("re").compile(r"[a-z0-9][a-z0-9._/:@-]{0,200}")


def test_cmd(project=None):
    v = project_conf(project).get("TEST_CMD", "").strip()
    return v if v and TEST_CMD_RE.fullmatch(v) else DEFAULT_TEST_CMD


def is_pytest(cmd):
    return "pytest" in cmd


def gate_image(project=None):
    v = project_conf(project).get("GATE_IMAGE", "").strip() or os.environ.get("GATE_IMAGE", "")
    return v if v and IMAGE_RE.fullmatch(v) else DEFAULT_GATE_IMAGE


# ---- notifications (optional): NOTIFY_URL in etc/foreman.conf or projects/<name>/project.conf
NOTIFY_DEFAULT_EVENTS = "goal_done,task_blocked,killswitch,questions,quota_out"


def project_conf(project=None):
    """etc/foreman.conf with the project's project.conf on top (plain key=value, no defaults)."""
    p = PROJECT if project is None else project
    c = _kv(os.path.join(ROOT, "etc", "foreman.conf"))
    if p:
        c.update(_kv(os.path.join(ROOT, "projects", p, "project.conf")))
    return c


def notify_payload(fmt, event, title, message):
    """(body bytes, headers) for a notification in the given format: ntfy | slack | discord | json."""
    if fmt == "ntfy":
        import base64
        t = title if title.isascii() else "=?UTF-8?B?" + base64.b64encode(title.encode()).decode() + "?="
        return message.encode(), {"Title": t, "Tags": event, "Content-Type": "text/plain; charset=utf-8"}
    if fmt == "slack":
        return json.dumps({"text": f"*{title}*\n{message}"}).encode(), {"Content-Type": "application/json"}
    if fmt == "discord":
        return json.dumps({"content": f"**{title}**\n{message}"[:1900]}).encode(), {"Content-Type": "application/json"}
    return json.dumps({"event": event, "project": PROJECT or "default", "title": title, "message": message, "at": now()}).encode(), \
        {"Content-Type": "application/json"}


def notify(event, title, message):
    """Send one notification if NOTIFY_URL is set and the event is in NOTIFY_EVENTS. Never raises, never takes > ~8 s.
    An optional bearer token (ntfy access token, ...) is read from the secrets file `notify.token`."""
    try:
        c = project_conf()
        url = c.get("NOTIFY_URL", "").strip()
        if not url.startswith(("https://", "http://")):
            return False
        if event not in {e.strip() for e in c.get("NOTIFY_EVENTS", NOTIFY_DEFAULT_EVENTS).split(",")}:
            return False
        body, headers = notify_payload(c.get("NOTIFY_FORMAT", "ntfy").strip() or "ntfy", event,
                                       f"[{PROJECT or 'default'}] {title}"[:200], str(message)[:3500])
        tok = read(os.path.join(SECRETS, "notify.token"))
        if tok:
            headers["Authorization"] = "Bearer " + tok
        import urllib.request
        urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers, method="POST"), timeout=8).read()
        return True
    except Exception:
        return False


def killswitch_off(all_projects=False):
    try:
        os.remove(ks_all_path() if all_projects else ks_path())
        alert(f"KILLSWITCH OFF{' (all projects)' if all_projects else ''}")
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
    entry.setdefault("project", PROJECT)
    with open(ledger_path(), "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")


# Run outcomes that say nothing about whether the agent misbehaves: they neither count toward nor reset the kill switch's
# consecutive-failure streak. (Before: a hard stop of 4 running runs wrote 4 "killswitch" entries and the next single failure
# after "Continue" tripped the kill switch again; token caps - 22 % of all runs, productive since WIP is saved - tripped it too.)
NEUTRAL_OUTCOMES = ("killswitch", "setup-failed", "egress-not-enforced", "quota", "orphaned", "token-cap", "timeout")


def consecutive_failures(ledger, project):
    """How many of this project's runs failed in a row, newest first, skipping neutral outcomes; an ok run ends the streak."""
    n = 0
    for e in reversed([e for e in ledger if not e.get("test") and e.get("project", "") == project]):
        r = e.get("reason")
        if r in NEUTRAL_OUTCOMES:
            continue
        if r != "failed":
            break
        n += 1
    return n


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
    """The run's supervisor is alive. After a reboot a recorded pid can belong to an unrelated process, so on Linux the
    process must really be a run-supervisor (otherwise a worker would look busy forever)."""
    if os.path.exists(d + "/finished") or not os.path.exists(d + "/pid"):
        return False
    pid = read(d + "/pid")
    if not alive(pid):
        return False
    try:
        return b"run-supervisor" in open(f"/proc/{int(pid)}/cmdline", "rb").read()
    except (OSError, ValueError):
        return not os.path.isdir("/proc/self")      # no procfs (not Linux): trust the pid


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
