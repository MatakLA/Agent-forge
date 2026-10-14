"""The foreman's state as plain JSON files under runs/[projects/<name>/]foreman/: goals, tasks, state.json, events.jsonl.
Read-modify-write goes through locked() / state_tx() (thread lock + flock), so the loop and CLI commands never lose updates."""
import contextlib, fcntl, json, os, re, sys, threading, time

import sbx

from . import ctx


# ---------------------------------------------------------------- state (plain JSON files)
def jload(p, default=None):
    try:
        return json.load(open(p))
    except Exception:
        return default


def jsave(p, d):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    json.dump(d, open(tmp, "w"), indent=1, ensure_ascii=False)
    os.replace(tmp, p)


def event(msg, **kw):
    os.makedirs(ctx.FD, exist_ok=True)
    with open(os.path.join(ctx.FD, "events.jsonl"), "a") as f:
        f.write(json.dumps(dict(at=sbx.now(), msg=msg, **kw), ensure_ascii=False) + "\n")
    print(f"{sbx.now()} {msg}", file=sys.stderr, flush=True)     # stderr: the journal gets it, a command's JSON output stays clean


def gstate():
    return jload(os.path.join(ctx.FD, "state.json"), {})


def gsave(s):
    jsave(os.path.join(ctx.FD, "state.json"), s)


STATE_LOCK = threading.RLock()


@contextlib.contextmanager
def locked():
    """Exclusive access to this project's mutable shared files (state.json, backlog.json, owner_questions.json) for a
    read-modify-write: across the loop's threads (RLock) and across processes - the daemon and a CLI command such as
    `bin/foreman backlog add` (flock). Without it, two writers silently lost each other's update."""
    with STATE_LOCK:
        os.makedirs(ctx.FD, exist_ok=True)
        with open(os.path.join(ctx.FD, ".state.lock"), "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            yield


@contextlib.contextmanager
def state_tx():
    """`with state_tx() as st:` - read state.json, change it, saved on exit (under locked())."""
    with locked():
        st = gstate()
        yield st
        gsave(st)


def new_goal_id(suffix=""):
    """G<UTC timestamp>[suffix], unique even when several goals are created in the same second."""
    base = time.strftime("G%Y%m%d-%H%M%S", time.gmtime()) + suffix
    gid, n = base, 1
    while os.path.exists(os.path.join(ctx.FD, "goals", gid + ".json")):
        gid, n = f"{base}-{n}", n + 1
    return gid


def goals():
    d = os.path.join(ctx.FD, "goals")
    return [jload(os.path.join(d, f)) for f in sorted(os.listdir(d))] if os.path.isdir(d) else []


def save_goal(g):
    jsave(os.path.join(ctx.FD, "goals", g["id"] + ".json"), g)


def tasks():
    """Only bare `<task-id>.json` files are tasks - `bin/foreman` also writes `<id>.mutations.json`/`<id>.pr.md`/`<id>.md`
    (spec/PR-body/mutation-spec) into the same directory; skip anything else, and skip a task file that fails to parse
    as a dict instead of crashing the whole loop on one bad file."""
    d = os.path.join(ctx.FD, "tasks")
    if not os.path.isdir(d):
        return []
    out = []
    for f in sorted(os.listdir(d)):
        if not re.fullmatch(r"\d{4}-[\w-]+\.json", f):
            continue
        t = jload(os.path.join(d, f))
        if isinstance(t, dict):
            out.append(t)
    return out


def save_task(t):
    t["updated"] = sbx.now()
    jsave(os.path.join(ctx.FD, "tasks", t["id"] + ".json"), t)


def next_task_number():
    n = 0
    legacy = os.path.join(ctx.ROOT, "tasks")             # hand-written task files of older installs; absent on a fresh checkout
    for name in (os.listdir(legacy) if os.path.isdir(legacy) else []) + [t["id"] for t in tasks()]:
        m = re.match(r"^(\d{4})-", name)
        if m:
            n = max(n, int(m.group(1)))
    return n + 1


def notify(kind, obj=None, text=""):
    """Owner notifications (NOTIFY_URL, see etc/foreman.example.conf) - sent in the background, never blocks the loop."""
    obj = obj or {}
    if kind == "goal_done":
        title, msg = "Goal done", f"{str(obj.get('text', ''))[:300]}\n\n{obj.get('summary', '')}"
    elif kind == "task_blocked":
        title, msg = f"Task blocked: {obj.get('id')}", f"{obj.get('title', '')}\n\n{str(obj.get('blocked_why', ''))[:800]}"
    elif kind == "questions":
        title, msg = "The planner has questions for you", text
    elif kind == "quota_out":
        title, msg = "All workers are out of quota", text
    elif kind == "auth":
        title, msg = "An API key was rejected", text
    elif kind == "project_done":
        title, msg = "Project done", text
    elif kind == "approval":
        title, msg = "Waiting for your approval", text
    else:
        title, msg = kind, text
    threading.Thread(target=sbx.notify, args=(kind, title, msg), daemon=True).start()
