"""Shared helpers for bin/dispatch, bin/run-supervisor, bin/killswitch and bin/status (V7).
Provides path construction under runs and secrets, kill switch management, ledger tracking,
safe regex validation (PROJECT_RE, ROLE_RE, WORKER_RE, MODEL_RE, SAFE_ARG, TEST_CMD_RE, IMAGE_RE),
and safe test_cmd / gate_image retrieval with injection guards."""
import fcntl, json, os, re, subprocess, time
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
PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
PROJECT_NAME_RE = PROJECT_RE
ROLE_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")
ROLE_SLUG = ROLE_RE
WORKER_RE = re.compile(r"worker-\d+")
MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,99}")
MODEL_NAME_RE = MODEL_RE
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
SECRET_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
SAFE_ARG = re.compile(r"[A-Za-z0-9._/:-]{1,200}")

PROJECT = os.environ.get("FORGE_PROJECT", "")
if PROJECT and not PROJECT_RE.fullmatch(PROJECT):
    raise SystemExit(f"invalid FORGE_PROJECT {PROJECT!r} (lowercase letters, digits, dashes)")
WORKER_CLONE = f"/home/worker/work/{PROJECT}" if PROJECT else "/home/worker/playground"
SECRETS = os.path.expanduser(os.environ.get("SANDBOX_SECRETS") or "~/.config/sandbox/secrets")


def _check_project(project):
    p = PROJECT if project is None else project
    if p:
        if not PROJECT_RE.fullmatch(p) or ".." in p or "/" in p or "\\" in p:
            raise ValueError(f"invalid project {p!r}")
    return p


def sh(*args, **kw):
    """Run a subprocess with args as a list (never shell=True)."""
    cmd = list(args[0]) if len(args) == 1 and isinstance(args[0], (list, tuple)) else list(args)
    return subprocess.run(cmd, capture_output=kw.pop("capture_output", True), text=kw.pop("text", True), **kw)
DEFAULTS = dict(RUN_TIMEOUT_MIN=30, RUN_TIMEOUT_MAX_MIN=45, RUN_TOKEN_CAP=1500000, DAILY_TOKEN_BUDGET=3000000,
                MAX_RUNS_PER_DAY=6, MAX_PARALLEL=2, QUOTA_MODEL_FILTER="Gemini", QUOTA_MIN_5H_PCT=25,
                QUOTA_MIN_WEEK_PCT=15, CONSEC_FAIL_TRIP=3, POLL_SEC=2, WORKER_MODEL="gemini-3.8-flash-high",
                # API-key runtimes (claude / codex / gemini) cost real money: a hard cap per run (Claude Code enforces it
                # itself with --max-budget-usd; codex/gemini are stopped by the token cap and RUN_MAX_TOOL_CALLS) and per day
                API_RUN_BUDGET_USD=2.0, API_DAILY_BUDGET_USD=10.0, RUN_MAX_TOOL_CALLS=250,
                # USD per million tokens "input,cached input,output" - for the cost estimate of codex / gemini runs (their CLIs
                # report tokens, not money). Set them to your model's current prices.
                PRICE_CODEX="1.25,0.125,10", PRICE_GEMINI="1.25,0.31,10",
                # more caps (0 / empty = none): per calendar month for the whole install, per day per worker ("worker-5=3,worker-6=1.5");
                # per project: PROJECT_DAILY_BUDGET_USD / PROJECT_MONTHLY_BUDGET_USD in its own config
                API_MONTHLY_BUDGET_USD=0.0, WORKER_DAILY_BUDGET_USD="",
                # the owner's own model server for the compat (Codex) and aider runtimes: its OpenAI-compatible base URL, e.g.
                # http://192.168.1.20:11434/v1 (Ollama) or https://openrouter.ai/api/v1; prices "0,0,0" = a local model costs nothing
                COMPAT_BASE_URL="", PRICE_COMPAT="0,0,0")


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
    p = _check_project(project)
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
    p = _check_project(project)
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
TEST_CMD_RE = re.compile(r"[A-Za-z0-9 ._/=:,+@%-]{1,200}")       # a plain command line: no shell syntax
IMAGE_RE = re.compile(r"[a-z0-9][a-z0-9._/:@-]{0,200}")


def test_cmd(project=None):
    v = project_conf(project).get("TEST_CMD", "").strip()
    if v and not TEST_CMD_RE.fullmatch(v):
        raise ValueError(f"invalid TEST_CMD {v!r}")
    return v or DEFAULT_TEST_CMD


def is_pytest(cmd):
    return "pytest" in cmd


def gate_image(project=None):
    v = project_conf(project).get("GATE_IMAGE", "").strip() or os.environ.get("GATE_IMAGE", "")
    if v and not IMAGE_RE.fullmatch(v):
        raise ValueError(f"invalid GATE_IMAGE {v!r}")
    return v or DEFAULT_GATE_IMAGE


# ---- notifications (optional): NOTIFY_URL in etc/foreman.conf or projects/<name>/project.conf
NOTIFY_DEFAULT_EVENTS = "goal_done,task_blocked,killswitch,questions,quota_out,auth,approval"


def project_conf(project=None):
    """etc/foreman.conf with the project's project.conf on top (plain key=value, no defaults)."""
    p = _check_project(project)
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


def no_nul(text):
    """A NUL cannot be part of an argv; test output that quotes a hostile payload reaches the next attempt's prompt, so it is spelled out."""
    return str(text).replace("\x00", "\\x00")


def notify_outbox_path():
    return os.path.join(RUNS, "notify-outbox.jsonl")


def notify(event, title, message):
    """Send one notification if NOTIFY_URL is set and the event is in NOTIFY_EVENTS. Never raises, never takes > ~8 s.
    An optional bearer token (ntfy access token, ...) is read from the secrets file `notify.token`."""
    try:
        c = project_conf()
        url = c.get("NOTIFY_URL", "").strip()
        if url != "outbox" and not url.startswith(("https://", "http://")):
            return False
        if event not in {e.strip() for e in c.get("NOTIFY_EVENTS", NOTIFY_DEFAULT_EVENTS).split(",")}:
            return False
        if url == "outbox":                     # a relay outside this machine picks them up (when it cannot reach your server)
            ensure_runs()
            with open(notify_outbox_path(), "a") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                f.write(json.dumps({"at": now(), "event": event, "project": PROJECT or "default", "title": str(title)[:200],
                                    "message": str(message)[:3500]}, ensure_ascii=False, separators=(",", ":")) + "\n")
            return True
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


def ledger_path(project=None):
    if project:
        if not PROJECT_RE.fullmatch(project) or ".." in project or "/" in project or "\\" in project:
            raise ValueError(f"invalid project {project!r}")
        return os.path.join(RUNS, "projects", project, "ledger.jsonl")
    return os.path.join(RUNS, "ledger.jsonl")


def run_path(run_id):
    if not run_id or not RUN_ID_RE.fullmatch(run_id) or ".." in run_id or "/" in run_id or "\\" in run_id:
        raise ValueError(f"invalid run_id {run_id!r}")
    return os.path.join(RUNS, run_id)


def run_dir(run_id):
    return run_path(run_id)


def secret_path(name):
    if not name or not SECRET_NAME_RE.fullmatch(name) or ".." in name or "/" in name or "\\" in name:
        raise ValueError(f"invalid secret name {name!r}")
    return os.path.join(SECRETS, name)


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
NEUTRAL_OUTCOMES = ("killswitch", "setup-failed", "egress-not-enforced", "quota", "auth", "orphaned", "token-cap", "timeout", "stopped")


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


def worker_runtime(worker):
    """The runtime of a worker container (label agent-forge.runtime set by bin/worker-up; none = agy, older workers)."""
    if not WORKER_RE.fullmatch(worker):
        raise ValueError(f"invalid worker {worker!r}")
    import runtimes
    r = subprocess.run(["docker", "inspect", "-f", '{{index .Config.Labels "agent-forge.runtime"}}', worker], capture_output=True, text=True)
    v = r.stdout.strip()
    return v if r.returncode == 0 and v in runtimes.RUNTIMES else "agy"


def api_spent(ledger=None, period="day", project=None, worker=None):
    """USD spent by API runs today or this calendar month (UTC) - all projects unless `project` is given ("" = the default
    project), all workers unless `worker` is given."""
    key = today() if period == "day" else today()[:7]
    tot = 0.0
    for e in (ledger if ledger is not None else read_ledger()):
        if e.get("test") or not str(e.get("date", "")).startswith(key):
            continue
        if (project is not None and e.get("project", "") != project) or (worker is not None and e.get("worker") != worker):
            continue
        tot += e.get("cost_usd") or 0
    return round(tot, 4)


def api_spent_today(ledger=None):
    """USD spent today (UTC) by API runs, all projects (the daily budget is per install, like the other daily limits)."""
    return api_spent(ledger)


def worker_budgets(spec):
    """'worker-5=3,worker-6=1.5' -> {'worker-5': 3.0, 'worker-6': 1.5}; malformed parts are ignored."""
    out = {}
    for part in str(spec or "").split(","):
        w, _, v = part.strip().partition("=")
        try:
            if __import__("re").fullmatch(r"worker-\d+", w.strip()):
                out[w.strip()] = float(v)
        except ValueError:
            pass
    return out


def _usd(v):
    try:
        return float(v or 0)
    except ValueError:
        return 0.0


def running_api_runs():
    """[(worker, project)] of API runs that are running now - each may still cost up to its run cap."""
    out = []
    for d in run_dirs():
        if is_running(d):
            m = meta(d)
            if m.get("runtime", "agy") != "agy" and not m.get("test"):
                out.append((m.get("worker"), m.get("project", "")))
    return out


def budget_check(worker, lim, conf=None, ledger=None, running=None):
    """Can one more API run start on `worker` ("" = any) for this project? Returns (ok, message). The worst case is assumed: the
    new run and every API run still running may each cost API_RUN_BUDGET_USD. Caps: per day (always) and per month for the whole
    install, per day and per month for this project (PROJECT_DAILY_BUDGET_USD / PROJECT_MONTHLY_BUDGET_USD in its config), per day
    for this worker (WORKER_DAILY_BUDGET_USD). 0 / empty = no cap, except the install's daily budget."""
    led = ledger if ledger is not None else read_ledger()
    run = _usd(lim.get("API_RUN_BUDGET_USD"))
    conf = conf if conf is not None else project_conf()
    running = running_api_runs() if running is None else running
    rn = lambda proj=None, w=None: sum(1 for rw, rp in running if (proj is None or rp == proj) and (w is None or rw == w)) * run
    name = PROJECT or "default"
    checks = [("today, all projects", api_spent(led) + rn(), _usd(lim.get("API_DAILY_BUDGET_USD")), "API_DAILY_BUDGET_USD", True),
              ("this month, all projects", api_spent(led, "month") + rn(), _usd(lim.get("API_MONTHLY_BUDGET_USD")), "API_MONTHLY_BUDGET_USD", False),
              (f"today, project {name}", api_spent(led, "day", PROJECT) + rn(PROJECT), _usd(conf.get("PROJECT_DAILY_BUDGET_USD")),
               "PROJECT_DAILY_BUDGET_USD", False),
              (f"this month, project {name}", api_spent(led, "month", PROJECT) + rn(PROJECT), _usd(conf.get("PROJECT_MONTHLY_BUDGET_USD")),
               "PROJECT_MONTHLY_BUDGET_USD", False)]
    if worker:
        checks.append((f"today, {worker}", api_spent(led, "day", worker=worker) + rn(w=worker),
                       worker_budgets(lim.get("WORKER_DAILY_BUDGET_USD")).get(worker, 0.0), "WORKER_DAILY_BUDGET_USD", False))
    for what, spent, cap, key, always in checks:
        if (cap > 0 or always) and spent + run > cap + 1e-9:
            return False, f"API budget {what} used: ${spent:.2f} of ${cap:.2f} (a run may cost up to ${run:.2f}; {key})"
    return True, ""


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
    if not WORKER_RE.fullmatch(worker):
        raise ValueError(f"invalid worker {worker!r}")
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
