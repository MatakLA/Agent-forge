#!/usr/bin/env python3
"""Agent-Forge web dashboard and admin console: the team live (orchestrator, every worker), goals with their acceptance criteria,
the task board, timeline, dependency graphs, diffs, metrics, events; and the owner's controls - new goals and backlog, answers to
the planner's questions, kill switch per project or for all, settings (models, providers, worker pools, limits), new workers with
their Antigravity login in a browser terminal, and new projects.

Python standard library only, one file. Runs next to bin/foreman (LOCAL mode, default) or on another machine reading it over ssh
(REMOTE mode: set VM_IP, VM_USER, VM_KEY, VM_KNOWN_HOSTS, and FORGE_DIR if Agent-Forge is not in ~/sandbox there). Binds 127.0.0.1
only - publish it with Tailscale serve or a reverse proxy (then list its host name in DASHBOARD_HOSTS) and keep it private: it
is an admin console. Every write goes through bin/foreman / bin/killswitch / bin/worker-add, which validate their input.

Config (environment): BIND_PORT (8171), FORGE_DIR, VM_IP / VM_USER / VM_KEY / VM_KNOWN_HOSTS for remote mode, DASHBOARD_HOSTS,
DASHBOARD_TOKEN (optional sign-in), FOREMAN_SERVICE / FOREMAN_SERVICE_PROJECT (systemd unit names), CLAUDE_PROJECT.
"""
import atexit
import collections
import json
import os
import re
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BIND_PORT = int(os.environ.get("BIND_PORT", "8171"))
DISCOVER_INTERVAL = 5           # seconds between "what's running" scans
FINISHED_GRACE = 90             # seconds a finished worker's live pane stays visible after its run ends
LINE_BUFFER = 400               # rendered lines kept per pane
SSH_TIMEOUT = 6

# Two modes: LOCAL (default) - the dashboard runs on the same machine as bin/foreman and reads it directly; REMOTE - set VM_IP (+
# VM_USER, VM_KEY, VM_KNOWN_HOSTS) and it reads the Agent-Forge machine over ssh, e.g. from a host that runs Agent-Forge in a VM.
VM_IP = os.environ.get("VM_IP", "")
FORGE_DIR = os.environ.get("FORGE_DIR", "~/sandbox" if VM_IP else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if VM_IP:
    VM_USER, VM_KEY, VM_KNOWN_HOSTS = os.environ["VM_USER"], os.environ["VM_KEY"], os.environ["VM_KNOWN_HOSTS"]
    SSH_BASE = [
        "ssh", "-i", VM_KEY,
        "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=15",
        "-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=" + VM_KNOWN_HOSTS,
        "-o", "LogLevel=ERROR", f"{VM_USER}@{VM_IP}",
    ]
else:
    SSH_BASE = ["bash", "-c"]
# Claude Code keeps a project's transcripts under ~/.claude/projects/<the project path with / replaced by ->
if VM_IP:
    _remote_dir = FORGE_DIR.replace("~", f"/home/{VM_USER}", 1) if FORGE_DIR.startswith("~") else FORGE_DIR
    CLAUDE_PROJECT = os.environ.get("CLAUDE_PROJECT", "-" + _remote_dir.strip("/").replace("/", "-"))
else:
    CLAUDE_PROJECT = "-" + os.path.expanduser(FORGE_DIR).strip("/").replace("/", "-")


LAST_ERR = {"msg": ""}           # why the last ssh_run failed, shown on the page instead of a vague "unavailable"


def ssh_run(remote_cmd, timeout=SSH_TIMEOUT):
    try:
        r = subprocess.run(SSH_BASE + [remote_cmd], capture_output=True, text=True, timeout=timeout)
        if r.returncode:
            LAST_ERR["msg"] = f"exit {r.returncode}: {(r.stderr or '').strip()[-200:]}"
        return r.stdout if r.returncode == 0 else ""
    except Exception as e:
        LAST_ERR["msg"] = f"{type(e).__name__}: {e}"[:300]
        return ""


def open_fds():
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        return -1


ASK_LOCK = threading.Lock()
FOREMAN_SERVICE = os.environ.get("FOREMAN_SERVICE", "agent-forge-foreman")     # the default project's foreman unit
# a named project's foreman unit, as scripts/install-services --project names it
FOREMAN_SERVICE_PROJECT = os.environ.get("FOREMAN_SERVICE_PROJECT", "agent-forge-foreman-{project}")
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\r")
JOBS = {}


def start_job(kind, fn):
    for k in [k for k, j in JOBS.items() if j["state"] != "running" and time.time() - j["started"] > 86400]:
        del JOBS[k]                                  # finished jobs are kept a day for the page, then dropped
    jid = f"{kind}-{int(time.time() * 1000)}"
    JOBS[jid] = {"id": jid, "kind": kind, "state": "running", "log": "", "started": time.time()}
    threading.Thread(target=fn, args=(JOBS[jid],), daemon=True).start()
    return JOBS[jid]


def add_worker_job(job):
    """Next free worker-N: Forgejo account + token, keyring password, HOME volume, container (bin/worker-add). The agy login is a
    separate interactive step (LOGIN) because it needs the owner to open a URL with that worker's Google account."""
    try:
        out = ssh_run("docker ps -a --format '{{.Names}}'", timeout=20)
        nums = [int(x.split("-")[1]) for x in out.split() if re.fullmatch(r"worker-\d+", x)]
        n = max(nums or [0]) + 1
        job["worker"] = f"worker-{n}"
        job["log"] += f"creating worker-{n}...\n"
        rc, o, e = ssh_run_full(f"cd {FORGE_DIR} && bin/worker-add {n} 2>&1", timeout=1500)
        job["log"] += ANSI.sub("", o + e)[-4000:]
        job["state"] = "done" if rc == 0 else "failed"
    except Exception as ex:
        job["log"] += f"\nerror: {ex}"; job["state"] = "failed"


def create_project_job(job, name, repo, workers_, lang, vision):
    """A new project end to end: projects/<name>/ (bin/foreman init-project), its repository + access on Forgejo
    (scripts/forge-bootstrap with FORGE_REPO), its own foreman service (scripts/install-services --project, needs sudo), and -
    if the default project still uses every worker - the default project's pool shrinks to the workers not given away."""
    def step(label, cmd, timeout=600, inp=None):
        job["log"] += f"==> {label}\n"
        rc, o, e = ssh_run_full(cmd, timeout=timeout, inp=inp)
        job["log"] += ANSI.sub("", o + e)[-3000:] + "\n"
        return rc == 0
    try:
        wf = f" --workers {','.join(workers_)}" if workers_ else ""
        if not step("project files", f"cd {FORGE_DIR} && bin/foreman init-project {name} --repo {repo}{wf} --lang '{lang}' --vision -",
                    timeout=30, inp=vision):
            job["state"] = "failed"; return
        if not step("repository on Forgejo", f"cd {FORGE_DIR} && FORGE_REPO={repo} scripts/forge-bootstrap 2>&1"):
            job["state"] = "failed"; return
        if workers_:
            rc, out, _ = ssh_run_full(f"cd {FORGE_DIR} && bin/foreman settings", timeout=60)
            try:
                cur = json.loads(out)
                if not cur["values"].get("WORKERS"):
                    rest = [w for w in cur.get("workers", []) if w not in workers_]
                    step("default project keeps the other workers", f"cd {FORGE_DIR} && bin/foreman settings apply", timeout=60,
                         inp=json.dumps({"WORKERS": ",".join(rest)}))
            except Exception:
                job["log"] += "(could not read the default project's settings; check its worker pool in Settings)\n"
        if step("foreman service", f"cd {FORGE_DIR} && sudo -n scripts/install-services --project {name} 2>&1", timeout=120):
            job["state"] = "done"
        else:
            job["log"] += (f"\nThe service needs root. Run on the Agent-Forge machine:\n  cd {FORGE_DIR} && "
                           f"sudo scripts/install-services --project {name}\n")
            job["state"] = "needs-sudo"
        PROJECTS["at"] = 0          # refresh the project list on the next loop
    except Exception as ex:
        job["log"] += f"\nerror: {ex}"; job["state"] = "failed"


class LoginSessions:
    """`docker exec -it worker-N agy-run` with a real terminal (ssh -tt / a local pty), so the owner can do agy's interactive
    login from the browser: the page shows the output (the login URL) and sends the code back."""
    def __init__(self):
        self.s = {}
        self.lock = threading.Lock()

    def start(self, w):
        with self.lock:
            old = self.s.get(w)
            if old and old["proc"].poll() is None:
                return {"ok": True, "note": "already running"}
            cmd = f"docker exec -it {w} agy-run"
            if VM_IP:
                argv = SSH_BASE[:1] + ["-tt"] + SSH_BASE[1:] + [cmd]
                proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
                rfd, wf = proc.stdout.fileno(), proc.stdin
            else:
                import pty
                mfd, sfd = pty.openpty()
                proc = subprocess.Popen(["docker", "exec", "-it", w, "agy-run"], stdin=sfd, stdout=sfd, stderr=sfd, start_new_session=True)
                os.close(sfd)
                rfd, wf = mfd, None
            sess = {"proc": proc, "rfd": rfd, "wf": wf, "buf": "", "started": time.time(), "pty": not VM_IP}
            self.s[w] = sess
            ALL_PROCS.add(proc)
            threading.Thread(target=self._pump, args=(sess,), daemon=True).start()
            return {"ok": True}

    def _pump(self, sess):
        while True:
            try:
                chunk = os.read(sess["rfd"], 4096)
            except OSError:
                break
            if not chunk:
                break
            sess["buf"] = (sess["buf"] + ANSI.sub("", chunk.decode(errors="replace")))[-20000:]
        if sess.get("pty"):
            try:
                os.close(sess["rfd"])
            except OSError:
                pass
        reap(sess["proc"])

    def send(self, w, text):
        sess = self.s.get(w)
        if not sess or sess["proc"].poll() is not None:
            return {"error": "no running login session"}
        data = (text + "\r").encode()
        if sess["wf"]:
            sess["wf"].write(data); sess["wf"].flush()
        else:
            os.write(sess["rfd"], data)
        return {"ok": True}

    def read(self, w):
        sess = self.s.get(w)
        if not sess:
            return {"running": False, "output": ""}
        out = sess["buf"]
        urls = re.findall(r"https://[^\s'\"<>]+", out)
        return {"running": sess["proc"].poll() is None, "output": out[-6000:], "url": urls[-1] if urls else None}

    def stop(self, w):
        sess = self.s.pop(w, None)
        if sess and sess["proc"].poll() is None:
            try:
                os.killpg(sess["proc"].pid, signal.SIGTERM)
            except Exception:
                sess["proc"].terminate()
        ok = "Five Hour" in ssh_run(f"docker exec {w} agy-run -p /usage --print-timeout 60s 2>/dev/null", timeout=90)
        return {"ok": True, "logged_in": ok}


LOGIN = LoginSessions()


def ssh_run_full(remote_cmd, timeout=30, inp=None):
    """Like ssh_run but returns (returncode, stdout, stderr) - used by the one write action this dashboard has
    (/api/resume), so a failure can be reported instead of silently swallowed."""
    try:
        r = subprocess.run(SSH_BASE + [remote_cmd], capture_output=True, text=True, timeout=timeout, input=inp)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 1, "", str(e)


# ---- rendering: turn one raw JSON line into zero or more (kind, text) pairs -----------------------------------
# kind is one of: user, assistant, tool, tool_done, result — used for stats and for coloring lines in the UI.
def trunc(s, n=4000):
    s = (s or "").strip().replace("\r", "")
    return s if len(s) <= n else s[: n - 1] + "…"


def render_orchestrator_line(d, acc):
    t = d.get("type")
    if t == "assistant":
        for block in (d.get("message") or {}).get("content") or []:
            bt = block.get("type")
            if bt == "text" and block.get("text", "").strip():
                yield "assistant", trunc(block["text"])
            elif bt == "tool_use":
                yield "tool", trunc(f"{block.get('name')}({json.dumps(block.get('input', {}), ensure_ascii=False)})")
    elif t == "user":
        content = (d.get("message") or {}).get("content")
        if isinstance(content, str) and content.strip():
            yield "user", trunc(content)
        elif isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_result":
                    val = block.get("content")
                    text = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False) if val else ""
                    yield "tool_done", trunc(text)
    elif t == "last-prompt":
        text = d.get("lastPrompt")
        if text:
            yield "task", trunc(text, 140)


def render_worker_line(d, acc):
    ev = d.get("event")
    su = d.get("step_update") or {}
    idx = su.get("step_index")
    stype = su.get("step_type")
    state = su.get("state")
    if ev == "step_update" and stype == "user_input":
        text = su.get("text") or su.get("tool_info", {}).get("text") or ""
        if text:
            yield "user", trunc(text)
            yield "task", trunc(text, 140)
    elif ev == "step_update" and stype == "agent_response":
        key = ("resp", idx)
        acc[key] = acc.get(key, "") + (su.get("text_delta") or "")
        if state == "DONE" and acc[key].strip():
            yield "assistant", trunc(acc[key])
    elif ev == "step_update" and stype == "tool":
        info = su.get("tool_info") or {}
        name = su.get("tool_name") or info.get("name") or "tool"
        params = info.get("parameters") or {}
        cmd = params.get("CommandLine") or params.get("command") or json.dumps(params, ensure_ascii=False)
        if state == "ACTIVE":
            yield "tool", trunc(f"{name}: {cmd}")
        elif state == "DONE":
            dur = su.get("duration_seconds")
            yield "tool_done", "done" + (f" ({dur:.1f}s)" if isinstance(dur, (int, float)) else "")
    elif ev == "result":
        res = d.get("result") or {}
        status = res.get("status", "?")
        yield "result", f"{status}: " + trunc(res.get("response", ""))


ICONS = {"user": "\U0001f9d1", "assistant": "\U0001f916", "tool": "\U0001f527", "tool_done": "  ✓", "result": None}


# ---- roster: persistent summary per known AI (orchestrator + every worker ever seen), survives pane removal ---
class Roster:
    def __init__(self):
        self.lock = threading.Lock()
        self.entries = {}   # id -> dict

    def touch(self, pane_id, **fields):
        with self.lock:
            e = self.entries.setdefault(pane_id, {})
            e.update(fields)
            e["last_active_at"] = time.time()

    def snapshot(self):
        with self.lock:
            return {k: dict(v) for k, v in self.entries.items()}


ROSTER = Roster()


# ---- pane state (live tail; only exists while running or briefly after finishing) ------------------------------
class Pane:
    def __init__(self, pane_id, title, kind, remote_path, renderer, tail_argv):
        self.id, self.title, self.kind, self.remote_path, self.renderer = pane_id, title, kind, remote_path, renderer
        self.tail_argv = tail_argv
        self.lines = collections.deque(maxlen=LINE_BUFFER)
        self.state = "running"
        self.finished_at = None
        self.tool_calls = 0
        self.task_label = None
        self._acc = {}
        self._proc = None
        self._thread = None
        self._stop = threading.Event()
        ROSTER.touch(pane_id, title=title, kind=kind, state=self.state, tool_calls=0)

    def start(self):
        self._thread = threading.Thread(target=self._tail, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass

    def _tail(self):
        try:
            self._proc = subprocess.Popen(self.tail_argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                           bufsize=1, start_new_session=True)
        except Exception:
            return
        ALL_PROCS.add(self._proc)
        try:
            self._read_lines()
        finally:
            # close the pipe and reap the process: a finished tail that kept its pipe open leaked one fd per worker run
            # until the process hit its open-files limit (1024) and could not start ssh any more - the "stale data" bug
            reap(self._proc)

    def _read_lines(self):
        for raw in self._proc.stdout:
            if self._stop.is_set():
                break
            raw = raw.strip()
            if not raw:
                continue
            try:
                d = json.loads(raw)
            except Exception:
                continue
            try:
                for kind, text in self.renderer(d, self._acc):
                    if kind == "task":
                        self.task_label = text
                        continue
                    self.lines.append({"kind": kind, "text": text})
                    if kind == "tool":
                        self.tool_calls += 1
            except Exception:
                continue
            if self.kind == "worker" and d.get("event") == "result":
                self.state = "finished"
                self.finished_at = time.time()
            ROSTER.touch(self.id, title=self.title, kind=self.kind, state=self.state,
                         tool_calls=self.tool_calls, task=self.task_label,
                         last_line=(self.lines[-1] if self.lines else None))


ALL_PROCS = set()   # live ssh subprocesses, for a clean shutdown (removed again by reap())


def reap(proc):
    for f in (proc.stdin, proc.stdout, proc.stderr):
        try:
            if f:
                f.close()
        except Exception:
            pass
    try:
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill(); proc.wait(timeout=5)
        except Exception:
            pass
    ALL_PROCS.discard(proc)
PANES = {}
PANES_LOCK = threading.Lock()
# pane id -> path of the finished run whose pane was dropped. Without it the next scan (5 s later) found the same latest run,
# started a new `tail -F` for it, saw "finished" again, dropped it 90 s later, and so on - a new ssh process per worker every
# ~95 s for as long as the worker was idle.
RETIRED = {}


def discover_devbox_orchestrator():
    """The most recently modified transcript directly under -home-dev-sandbox (not memory/, not tool-results/)."""
    out = ssh_run(f"ls -t ~/.claude/projects/{CLAUDE_PROJECT}/*.jsonl 2>/dev/null | head -1").strip()
    if not out:
        return None
    sid = os.path.basename(out).removesuffix(".jsonl")
    running = sid[:8] in ssh_run("claude agents --json 2>/dev/null")
    tail_argv = SSH_BASE + [f"tail -n 200 -F {out} 2>/dev/null"]
    return sid, out, running, tail_argv


RUN_DIR_RE = re.compile(r"^\d{8}T\d{6}Z-(.+)$")   # bin/dispatch's own naming: <UTC timestamp>-<worker-name>


def discover_workers():
    """Latest run directory per worker under ~/sandbox/runs/, and the full set of worker names ever seen.
    Older, differently-named directories (e.g. task-numbered dirs from early V4 runs like 0001-wordcount) are
    not dispatch runs and are skipped — matched strictly against bin/dispatch's own <timestamp>-<worker> format."""
    out = ssh_run(f"ls -1dt {FORGE_DIR}/runs/*/ 2>/dev/null | grep -vE '/(audit|unattended)/$'")
    latest_per_worker, all_workers = {}, set()
    for line in out.splitlines():
        base = os.path.basename(line.strip().rstrip("/"))
        m = RUN_DIR_RE.match(base)
        if not m:
            continue
        worker = m.group(1)
        all_workers.add(worker)
        if worker not in latest_per_worker:
            latest_per_worker[worker] = (base, line.strip().rstrip("/"))
    return latest_per_worker, all_workers


QUOTA_POLL_INTERVAL = 60   # seconds; agy /usage is metadata, not a model call, but no need to hammer it


def parse_usage(text):
    """Exact copy of bin/lib/sbx.py's parse_usage on devbox: tab-separated model / label / pct% / reset."""
    rows = []
    for line in text.splitlines():
        p = [x.strip() for x in line.split("\t")]
        if len(p) >= 3 and p[2].endswith("%"):
            try:
                rows.append({"model": p[0], "label": p[1], "pct": float(p[2][:-1]), "reset": p[3] if len(p) > 3 else ""})
            except ValueError:
                pass
    return rows


def quota_loop():
    while True:
        try:
            _, all_workers = discover_workers()
            for worker in all_workers:
                out = ssh_run(f"docker exec {worker} agy-run -p /usage --print-timeout 60s 2>/dev/null", timeout=70)
                rows = [r for r in parse_usage(out) if "5h" in r["label"].lower() or "five hour" in r["label"].lower()
                        or "week" in r["label"].lower()]
                if rows:
                    ROSTER.touch("worker:" + worker, quota=rows)
        except Exception:
            pass
        time.sleep(QUOTA_POLL_INTERVAL)


FOREMAN_INTERVAL = 4
PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
FOREMAN_BY = {}           # project name -> {"data", "at", "error"}; "default" = the project without --project
PROJECTS = {"list": [], "at": 0}


def pflag(project):
    """`--project NAME` for bin/foreman, "" for the default project; anything that is not a project name is rejected."""
    if not project or project == "default":
        return ""
    if not PROJECT_RE.fullmatch(project):
        raise ValueError("bad project")
    return f" --project {project}"


def foreman_from_old_json(d):
    """Older bin/foreman without `status --dashboard`: map `status --json` onto the same shape (fewer details)."""
    tasks = []
    for g in d.get("goals", []):
        for t in g.get("tasks", []):
            tasks.append(dict(t, goal=g["id"], max_attempts=4, depends_on=[], files=[], spec="", acceptance=[], history=[]))
    return dict(now=time.time(), alive=True, paused=d.get("paused"), killswitch=d.get("killswitch"), autopilot=False,
                claude_until=None, agy_cooldown={}, live={}, backlog=[], goals=[dict(g, tasks=None) for g in d.get("goals", [])],
                tasks=tasks, events=d.get("events", []), llm_today={}, today={}, old=True)


def poll_project(name):
    entry = FOREMAN_BY.setdefault(name, {"data": None, "at": 0, "error": "not loaded yet"})
    out = ssh_run(f"cd {FORGE_DIR} && bin/foreman{pflag(name)} status --dashboard 2>/dev/null", timeout=12)
    data = None
    try:
        data = json.loads(out)
    except Exception:
        if name == "default":
            try:
                data = foreman_from_old_json(json.loads(ssh_run(f"cd {FORGE_DIR} && bin/foreman status --json 2>/dev/null", timeout=12)))
            except Exception:
                pass
    if data is not None:
        entry.update(data=data, at=time.time(), error=None)
    else:
        entry["error"] = f"cannot read foreman status ({LAST_ERR['msg'] or 'no output'}; open files {open_fds()})"


def foreman_loop():
    while True:
        try:
            if time.time() - PROJECTS["at"] > 30:
                try:
                    lst = json.loads(ssh_run(f"cd {FORGE_DIR} && bin/foreman projects --json 2>/dev/null", timeout=15))
                    PROJECTS.update(list=[p for p in lst if PROJECT_RE.fullmatch(p.get("name", ""))], at=time.time())
                except Exception:
                    PROJECTS["at"] = time.time() - 20      # older foreman without `projects`: retry soon, default only meanwhile
            names = [p["name"] for p in PROJECTS["list"]] or ["default"]
            for n in names:
                poll_project(n)
        except Exception as e:
            # whatever the cause, never let this thread die silently and leave the dashboard frozen on stale data forever
            for e2 in FOREMAN_BY.values():
                e2["error"] = f"foreman_loop crashed: {e}"
        time.sleep(FOREMAN_INTERVAL)


def discover_loop():
    while True:
        try:
            _discover_once()
        except Exception:
            pass
        time.sleep(DISCOVER_INTERVAL)


def _discover_once():
    wanted = {}
    orch = discover_devbox_orchestrator()
    if orch:
        sid, path, running, tail_argv = orch
        wanted["orchestrator"] = ("Orchestrator (devbox)", "orchestrator", path, render_orchestrator_line, running, tail_argv)

    latest, all_workers = discover_workers()
    for worker, (run_name, run_dir) in latest.items():
        tail_argv = SSH_BASE + [f"tail -n 200 -F {run_dir}/stream.jsonl 2>/dev/null"]
        wanted["worker:" + worker] = (worker, "worker", run_dir + "/stream.jsonl", render_worker_line, True, tail_argv)
    # workers seen historically but with no run right now still get a roster entry (idle), so they never vanish
    for worker in all_workers:
        pane_id = "worker:" + worker
        if pane_id not in wanted:
            ROSTER.touch(pane_id, title=worker, kind="worker", state="idle")

    with PANES_LOCK:
        for pane_id, (title, kind, path, renderer, alive, tail_argv) in wanted.items():
            existing = PANES.get(pane_id)
            if existing is None and RETIRED.get(pane_id) == path:
                continue        # this run already finished and its pane was retired - do not start a new tail for it
            if existing is None or existing.remote_path != path:
                if existing:
                    existing.stop()
                p = Pane(pane_id, title, kind, path, renderer, tail_argv)
                if kind == "orchestrator":
                    p.state = "running" if alive else "idle"
                PANES[pane_id] = p
                p.start()
            elif kind == "orchestrator":
                PANES[pane_id].state = "running" if alive else "idle"
                ROSTER.touch(pane_id, state=PANES[pane_id].state)
        now = time.time()
        for pane_id in list(PANES):
            if pane_id not in wanted and pane_id != "orchestrator":
                PANES[pane_id].stop()
                del PANES[pane_id]
                continue
            p = PANES[pane_id]
            if p.kind == "worker" and p.state == "finished" and p.finished_at and now - p.finished_at > FINISHED_GRACE:
                ROSTER.touch(pane_id, state="idle")   # keep the roster row, drop the live pane
                RETIRED[pane_id] = p.remote_path
                p.stop()
                del PANES[pane_id]


def shutdown(*_a):
    with PANES_LOCK:
        for p in list(PANES.values()):
            p.stop()
    for proc in list(ALL_PROCS):
        try:
            proc.terminate()
        except Exception:
            pass
    os._exit(0)


# ---- HTTP -----------------------------------------------------------------------------------------------------
PAGE = r"""<!DOCTYPE html>
<html lang="sk"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agent-Forge</title>
<style>
  :root { --bg:#0f1115; --card:#1a1d24; --card2:#22262f; --text:#e8e9ec; --muted:#8b8f99; --accent:#5b8def; --ok:#3ecf8e; --idle:#5a5f6b;
          --bad:#e5566d; --tool:#c9a84c; --warn:#e59a3c; --line:#2c313b; }
  @media (prefers-color-scheme: light) { :root { --bg:#f4f5f7; --card:#ffffff; --card2:#f0f2f5; --text:#1a1d24; --muted:#6b7280; --accent:#3b6fd6;
          --ok:#1a9a63; --idle:#9aa0ab; --bad:#c23a52; --tool:#96751c; --warn:#b86e12; --line:#e2e5ea; } }
  * { box-sizing:border-box; }
  html, body { height:100%; }
  body { margin:0; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         display:flex; flex-direction:column; height:100dvh; overflow:hidden; }
  header { padding:10px 14px 0; flex:0 0 auto; }
  .top { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
  h1 { font-size:17px; margin:0 6px 0 0; }
  .pill { font-size:12px; padding:3px 9px; border-radius:999px; background:var(--card2); color:var(--muted); white-space:nowrap; display:inline-flex; align-items:center; gap:6px; }
  .pill.ok { color:var(--ok); } .pill.bad { color:var(--bad); background:color-mix(in srgb, var(--bad) 14%, transparent); } .pill.warn { color:var(--warn); }
  .projsel { background:var(--card2); color:var(--text); border:1px solid var(--line); border-radius:999px; padding:4px 10px; font-size:13px; font-weight:600; }
  .resume-btn { font-size:12.5px; font-weight:700; padding:4px 12px; border-radius:999px; border:0; background:var(--ok); color:#06130d; cursor:pointer; }
  .resume-btn:disabled { opacity:.6; cursor:default; }
  .warnbtn, .badbtn, .mini { font-size:12.5px; font-weight:700; padding:5px 12px; border-radius:999px; border:0; cursor:pointer; }
  .warnbtn { background:var(--warn); color:#1a1205; } .badbtn { background:var(--bad); color:#fff; } .mini { background:var(--card2); color:var(--text); border:1px solid var(--line); font-weight:600; }
  .btnrow { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-top:10px; }
  .btnrow.sticky { position:sticky; bottom:0; background:var(--bg); padding:10px 0; }
  .setcard { background:var(--card); border-radius:12px; padding:12px; margin-bottom:12px; }
  .setcard h3 { margin-top:0; }
  .setrow { display:grid; grid-template-columns:minmax(150px, 40%) 1fr; gap:10px; align-items:center; padding:6px 0; border-bottom:1px solid var(--line); font-size:13.5px; }
  @media (max-width:560px) { .setrow { grid-template-columns:1fr; gap:4px; } }
  .setrow input[type=text], .setrow input[type=number], .setrow select { width:100%; background:var(--card2); color:var(--text); border:1px solid var(--line); border-radius:8px; padding:6px 8px; font:inherit; }
  .seterr { color:var(--bad); font-size:12px; margin-top:3px; }
  .npta { width:100%; background:var(--card2); color:var(--text); border:1px solid var(--line); border-radius:8px; padding:6px 8px; font:inherit; resize:vertical; }
  .chk { display:inline-flex; gap:4px; align-items:center; margin:2px 10px 2px 0; }
  .prov, .poolrow { display:flex; justify-content:space-between; align-items:center; gap:8px; padding:4px 0; }
  .prov .mini { padding:2px 8px; margin-left:4px; }
  .term { background:#0b0d10; color:#d6dbe3; max-height:45dvh; overflow:auto; white-space:pre-wrap; font-size:12px; }
  #login-in { background:var(--card2); color:var(--text); border:1px solid var(--line); border-radius:8px; padding:7px 9px; font:inherit; }
  .stats { display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; }
  .stat { background:var(--card); border-radius:10px; padding:6px 10px; min-width:88px; }
  .stat b { display:block; font-size:16px; }
  .stat span { font-size:11px; color:var(--muted); }
  nav { display:flex; gap:4px; margin-top:10px; border-bottom:1px solid var(--line); }
  nav button { background:none; border:0; color:var(--muted); font-size:14px; padding:8px 12px; cursor:pointer; border-bottom:2px solid transparent; font-weight:600; }
  nav button.on { color:var(--text); border-bottom-color:var(--accent); }
  nav .cnt { font-size:11px; background:var(--card2); border-radius:999px; padding:1px 6px; margin-left:4px; color:var(--muted); }
  main { flex:1 1 auto; min-height:0; overflow-y:auto; padding:12px 14px 20px; }
  section { display:none; } section.on { display:block; }
  h3 { font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); margin:16px 0 8px; }
  h3:first-child { margin-top:0; }
  .dot { width:8px; height:8px; border-radius:50%; background:var(--idle); flex-shrink:0; display:inline-block; }
  .dot.running { background:var(--ok); animation:pulse 1.6s infinite; } .dot.think { background:var(--accent); animation:pulse 1.2s infinite; }
  .dot.bad { background:var(--bad); }
  @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:.35; } }

  /* team tree */
  .team { display:flex; flex-direction:column; align-items:center; }
  .node { background:var(--card); border-radius:12px; padding:10px 13px; box-shadow:0 1px 3px rgba(0,0,0,.15); width:100%; max-width:360px; }
  .node.orch { border:1px solid var(--accent); }
  .node-title { font-size:14px; font-weight:600; display:flex; align-items:center; gap:7px; }
  .node-sub { font-size:12.5px; color:var(--muted); margin-top:4px; }
  .node-task { font-size:13px; margin-top:5px; }
  .node-quota { font-size:11px; color:var(--accent); margin-top:4px; }
  .branch { width:1px; height:14px; background:var(--idle); }
  .workers { display:flex; flex-wrap:wrap; gap:10px; justify-content:center; padding-top:12px; border-top:1px dashed var(--idle); width:100%; }
  .workers .node { flex:1 1 240px; }
  .step { font-size:12.5px; margin-top:5px; display:flex; gap:6px; align-items:center; }

  /* goals */
  .goal { background:var(--card); border-radius:12px; padding:10px 12px; margin-bottom:8px; }
  .goal-head { display:flex; gap:8px; align-items:baseline; justify-content:space-between; }
  .goal-text { font-size:14px; font-weight:600; }
  .goal-meta { font-size:11.5px; color:var(--muted); white-space:nowrap; }
  .bar { height:6px; border-radius:3px; background:var(--card2); margin-top:8px; overflow:hidden; display:flex; }
  .bar i { display:block; height:100%; }
  .blrow { display:flex; gap:8px; align-items:flex-start; } .bltext { flex:1; } .blbtns { display:flex; gap:4px; flex-shrink:0; }
  .blbtns .mini { padding:2px 8px; }
  .qrow .btnrow input { flex:1; min-width:180px; background:var(--card2); color:var(--text); border:1px solid var(--line); border-radius:8px; padding:6px 8px; font:inherit; }
  #goal-text { min-height:64px; }
  .crit { margin-top:8px; font-size:13px; }
  .critrow { display:flex; gap:6px; align-items:flex-start; padding:2px 0; }
  .cm { width:16px; flex-shrink:0; text-align:center; font-weight:700; } .cm.ok { color:var(--ok); } .cm.no { color:var(--bad); } .cm.wait { color:var(--muted); }
  .goal-sum { font-size:12.5px; color:var(--muted); margin-top:6px; }
  .chips { display:flex; flex-wrap:wrap; gap:5px; margin-top:7px; }
  .chip { font-size:11.5px; padding:2px 7px; border-radius:6px; background:var(--card2); cursor:pointer; }

  /* kanban */
  .board { display:grid; grid-template-columns:repeat(5, minmax(220px, 1fr)); gap:10px; overflow-x:auto; padding-bottom:6px; }
  @media (max-width:760px) { .board { grid-template-columns:1fr; } }
  .col { background:var(--card2); border-radius:12px; padding:8px; min-height:60px; }
  .col h4 { margin:2px 4px 8px; font-size:13px; display:flex; justify-content:space-between; }
  .card { background:var(--card); border-radius:10px; padding:8px 10px; margin-bottom:7px; cursor:pointer; border-left:3px solid var(--idle); }
  .card:hover { outline:1px solid var(--line); }
  .card .id { font-size:11px; color:var(--muted); font-family:ui-monospace,Menlo,monospace; }
  .card .t { font-size:13.5px; font-weight:600; margin:2px 0 4px; }
  .card .m { font-size:11.5px; color:var(--muted); display:flex; flex-wrap:wrap; gap:6px; }
  .role { font-size:11px; padding:1px 6px; border-radius:5px; background:color-mix(in srgb, var(--accent) 16%, transparent); color:var(--accent); }
  .role.test { background:color-mix(in srgb, var(--warn) 16%, transparent); color:var(--warn); }
  .role.sec { background:color-mix(in srgb, var(--bad) 16%, transparent); color:var(--bad); }
  .s-running { border-left-color:var(--ok); } .s-gate { border-left-color:var(--accent); } .s-done { border-left-color:var(--idle); opacity:.85; }
  .s-blocked { border-left-color:var(--bad); } .s-pending { border-left-color:var(--tool); }

  /* detail drawer */
  .detail { position:fixed; inset:0; background:rgba(0,0,0,.45); display:none; align-items:flex-end; justify-content:center; z-index:10; }
  .detail.on { display:flex; }
  .sheet { background:var(--card); width:100%; max-width:820px; max-height:88dvh; overflow-y:auto; border-radius:16px 16px 0 0; padding:16px; }
  @media (min-width:760px) { .detail { align-items:center; } .sheet { border-radius:16px; } }
  .sheet h2 { font-size:16px; margin:0 0 4px; }
  .sheet pre { white-space:pre-wrap; word-break:break-word; background:var(--card2); border-radius:8px; padding:10px; font-size:12.5px; margin:6px 0; }
  .hist { border-left:2px solid var(--line); padding-left:10px; margin:6px 0; font-size:12.5px; }
  .close { float:right; background:var(--card2); color:var(--text); border:0; border-radius:8px; padding:5px 10px; cursor:pointer; }

  /* events */
  .ev { display:flex; gap:10px; padding:6px 2px; border-bottom:1px solid var(--line); font-size:13px; }
  .ev time { color:var(--muted); font-family:ui-monospace,Menlo,monospace; font-size:12px; flex:0 0 auto; }
  .ev.bad { color:var(--bad); } .ev.good { color:var(--ok); }

  /* live panes */
  .grid { display:flex; flex-direction:column; gap:10px; height:calc(100dvh - 210px); min-height:300px; }
  @media (min-width:760px) { .grid { flex-direction:row; } }
  .pane { flex:1 1 0; min-height:0; min-width:0; background:var(--card); border-radius:14px; padding:10px; display:flex; flex-direction:column; }
  .pane h2 { font-size:15px; margin:0 0 6px; display:flex; align-items:center; gap:8px; }
  .log { flex:1; min-height:0; font-family:ui-monospace,Menlo,Consolas,monospace; font-size:14px; line-height:1.45; overflow-y:auto; }
  .line { padding:3px 8px; margin-bottom:3px; border-left:3px solid transparent; border-radius:4px; word-break:break-word; white-space:pre-wrap; }
  .l-user { border-left-color:var(--accent); color:var(--accent); font-weight:700; background:color-mix(in srgb, var(--accent) 10%, transparent); }
  .l-assistant { border-left-color:var(--ok); }
  .l-tool { border-left-color:var(--tool); color:var(--tool); background:color-mix(in srgb, var(--tool) 8%, transparent); }
  .l-tool_done { border-left-color:var(--idle); color:var(--muted); font-size:12.5px; }
  .l-result { border-left-color:var(--bad); font-weight:800; background:color-mix(in srgb, var(--ok) 10%, transparent); }
  .empty { color:var(--muted); text-align:center; padding:20px 0; font-size:13px; }
  /* timeline, DAG, diff, metrics, ask */
  .tl { width:100%; display:block; }
  .tl text { font-size:11px; fill:var(--muted); font-family:inherit; }
  .tl .lane { font-size:12px; fill:var(--text); font-weight:600; }
  .tl rect.run { cursor:pointer; }
  .legend { display:flex; gap:12px; flex-wrap:wrap; font-size:12px; color:var(--muted); margin:6px 0 2px; }
  .legend i { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:4px; vertical-align:-1px; }
  .dag { width:100%; display:block; margin-top:8px; }
  .dag text { font-size:11px; fill:var(--text); font-family:ui-monospace,Menlo,monospace; pointer-events:none; }
  .dag g { cursor:pointer; }
  .linkbtn { background:none; border:0; color:var(--accent); cursor:pointer; font-size:12px; padding:2px 0; }
  .diff { font-family:ui-monospace,Menlo,monospace; font-size:12px; line-height:1.45; background:var(--card2); border-radius:8px; padding:8px; overflow-x:auto; max-height:60dvh; overflow-y:auto; }
  .diff div { white-space:pre; }
  .diff .a { color:var(--ok); background:color-mix(in srgb, var(--ok) 10%, transparent); }
  .diff .r { color:var(--bad); background:color-mix(in srgb, var(--bad) 10%, transparent); }
  .diff .h { color:var(--accent); font-weight:700; margin-top:6px; }
  .diff .at { color:var(--tool); }
  table.m { width:100%; border-collapse:collapse; font-size:13px; }
  table.m th, table.m td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); white-space:nowrap; }
  table.m th { color:var(--muted); font-weight:600; font-size:12px; }
  table.m td.num, table.m th.num { text-align:right; }
  .tablewrap { overflow-x:auto; background:var(--card); border-radius:12px; }
  .bars { display:flex; align-items:flex-end; gap:6px; height:120px; background:var(--card); border-radius:12px; padding:10px; }
  .bars div { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:flex-end; height:100%; font-size:11px; color:var(--muted); }
  .bars b { display:block; width:100%; background:var(--accent); border-radius:4px 4px 0 0; min-height:2px; }
  .ask { background:var(--card); border-radius:12px; padding:10px 12px; }
  .ask textarea { width:100%; min-height:56px; background:var(--card2); color:var(--text); border:1px solid var(--line); border-radius:8px; padding:8px; font:inherit; font-size:14px; resize:vertical; }
  .ask button { margin-top:6px; background:var(--accent); color:#fff; border:0; border-radius:8px; padding:7px 14px; font-weight:700; cursor:pointer; }
  .ask button:disabled { opacity:.6; cursor:default; }
  .answer { margin-top:10px; white-space:pre-wrap; font-size:13.5px; line-height:1.5; border-left:3px solid var(--accent); padding-left:10px; }
  .muted { color:var(--muted); }
</style></head>
<body>
<header>
  <div class="top"><h1>Agent-Forge</h1><select id="proj" class="projsel" style="display:none" aria-label="projekt"></select><span id="pills"></span>
    <button id="lang" class="mini" style="margin-left:auto" title="English / slovenčina"></button></div>
  <div id="jserr" style="display:none; margin-top:8px; font-size:12.5px; color:var(--bad);"></div>
  <div id="resume-result" style="display:none; margin-top:8px; font-size:12.5px;"></div>
  <div class="stats" id="stats"></div>
  <nav id="nav">
    <button data-t="overview">Prehľad</button>
    <button data-t="queue">Fronta <span class="cnt" id="c-queue">0</span></button>
    <button data-t="timeline">Časová os</button>
    <button data-t="live">Live <span class="cnt" id="c-live">0</span></button>
    <button data-t="metrics">Metriky</button>
    <button data-t="settings">Nastavenia</button>
    <button data-t="events">Udalosti</button>
  </nav>
</header>
<main>
  <section id="t-overview">
    <h3>Nový cieľ</h3>
    <div class="ask"><textarea id="goal-text" placeholder="Čo má tím urobiť? Napr.: Pridaj export výdavkov do CSV, s testami."></textarea>
      <div class="btnrow"><button id="goal-now">Začať hneď</button><button class="mini" id="goal-backlog">Do backlogu</button>
        <button class="mini" id="goal-roadmap" title="Veľkú požiadavku rozdelí plánovač na sériu menších cieľov v backlogu">Veľký projekt → roadmapa</button>
        <span id="goal-msg" class="muted" style="font-size:12.5px"></span></div></div>
    <div id="questions"></div>
    <h3>Spýtaj sa tímu</h3>
    <div class="ask"><textarea id="ask-q" placeholder="Napr. Ako funguje modul X a čo ešte chýba? (odpovedá orchestrátor z kódu v repe, stojí Claude usage)"></textarea>
      <button id="ask-btn">Spýtať sa</button><div id="ask-a"></div></div>
    <h3>Tím</h3><div id="team" class="team"><div class="empty">načítavam…</div></div>
    <h3>Ciele</h3><div id="goals"></div>
    <h3>Backlog</h3><div id="backlog"></div>
  </section>
  <section id="t-queue"><div id="board" class="board"></div></section>
  <section id="t-timeline"><div id="timeline"></div></section>
  <section id="t-live"><div id="grid" class="grid"></div></section>
  <section id="t-metrics"><div id="metrics"></div></section>
  <section id="t-settings"><div id="settings"></div></section>
  <section id="t-events"><div id="events"></div></section>
</main>
<div class="detail" id="detail"><div class="sheet" id="sheet"></div></div>
<div class="detail" id="login"><div class="sheet"><button class="close" id="login-close">Zavrieť</button><h2 id="login-title">Prihlásenie</h2>
  <div class="muted" style="font-size:12.5px;margin:6px 0">Toto je terminál workera. Otvor odkaz, prihlás sa Google účtom určeným pre tohto workera, skopíruj kód
  a pošli ho sem. Ak sa agy na niečo pýta, odpovedz tu.</div>
  <div id="login-url" style="font-size:13px;margin:6px 0"></div>
  <pre id="login-out" class="term"></pre>
  <div class="btnrow"><input type="text" id="login-in" placeholder="kód alebo odpoveď" style="flex:1"><button class="mini" id="login-send">Poslať</button></div>
  <div class="btnrow"><button class="resume-btn" id="login-done">Hotovo — overiť prihlásenie</button></div></div></div>
<div class="detail" id="newproj"><div class="sheet"><button class="close" id="np-close">Zavrieť</button><h2>Nový projekt</h2>
  <div class="muted" style="font-size:12.5px;margin:6px 0">Vytvorí projekt s vlastným orchestrátorom, repozitárom na Forgejo a skupinou workerov.
  Každý worker by mal patriť len jednému projektu.</div>
  <div id="np-form">
  <div class="setrow"><label>Názov (malé písmená, pomlčky)</label><div><input type="text" id="np-name" placeholder="napr. eshop"><div class="seterr" id="np-e-name"></div></div></div>
  <div class="setrow"><label>Repozitár (organizácia/repozitár)</label><div><input type="text" id="np-repo" placeholder="napr. acme/eshop"><div class="seterr" id="np-e-repo"></div></div></div>
  <div class="setrow"><label>Jazyk, v ktorom sa ťa tím pýta</label><div><input type="text" id="np-lang" value="Slovak"><div class="seterr" id="np-e-lang"></div></div></div>
  <div class="setrow"><label>Vízia — čo má projekt robiť (z toho plánuje autopilot)</label><textarea id="np-vision" rows="6" class="npta"></textarea></div>
  <div class="setrow"><label>Workeri pre tento projekt</label><div><div id="np-workers" class="muted">načítavam…</div><div class="seterr" id="np-e-workers"></div></div></div>
  <div class="btnrow"><button class="resume-btn" id="np-create">Vytvoriť projekt</button></div></div>
  <pre id="np-log" class="term" style="display:none"></pre></div></div>
<script>
// every write carries X-Agent-Forge: the server refuses POSTs without it (a cross-site page cannot add it without CORS consent)
const _fetch = window.fetch.bind(window);
window.fetch = (u, o) => { o = o || {}; if ((o.method || 'GET').toUpperCase() === 'POST') o.headers = Object.assign({'X-Agent-Forge': '1'}, o.headers || {}); return _fetch(u, o); };

// ---- language: the page is written in Slovak; in English every rendered text node is translated through EN / EN_RX (exact
// phrases first, then patterns for text with numbers and names). Content is never translated: goals, task titles, specs,
// transcripts, diffs, events, answers (SKIP). Choice: the EN/SK button, else the browser language.
const LANG = (() => { try { const v = localStorage.getItem('af-lang'); if (v === 'en' || v === 'sk') return v; } catch (e) {}
  return /^sk\b/i.test(navigator.language || '') ? 'sk' : 'en'; })();
const EN = {
 'Prehľad': 'Overview', 'Fronta': 'Queue', 'Časová os': 'Timeline', 'Metriky': 'Metrics', 'Nastavenia': 'Settings', 'Udalosti': 'Events',
 'Nový cieľ': 'New goal', 'Začať hneď': 'Start now', 'Do backlogu': 'Add to backlog', 'Veľký projekt → roadmapa': 'Big project → roadmap',
 'Spýtaj sa tímu': 'Ask the team', 'Spýtať sa': 'Ask', 'Tím': 'Team', 'Ciele': 'Goals', 'načítavam…': 'loading…', 'načítavam': 'loading',
 'Čo má tím urobiť? Napr.: Pridaj export výdavkov do CSV, s testami.': 'What should the team do? E.g.: Add a CSV export of expenses, with tests.',
 'Veľkú požiadavku rozdelí plánovač na sériu menších cieľov v backlogu': 'The planner splits a big request into a series of smaller goals in the backlog',
 'Napr. Ako funguje modul X a čo ešte chýba? (odpovedá orchestrátor z kódu v repe, stojí Claude usage)':
   'E.g. How does module X work and what is missing? (the orchestrator answers from the code; uses Claude usage)',
 'Zavrieť': 'Close', 'Prihlásenie': 'Sign-in', 'Poslať': 'Send', 'Hotovo — overiť prihlásenie': 'Done — check the sign-in', 'kód alebo odpoveď': 'code or answer',
 'Toto je terminál workera. Otvor odkaz, prihlás sa Google účtom určeným pre tohto workera, skopíruj kód a pošli ho sem. Ak sa agy na niečo pýta, odpovedz tu.':
   "This is the worker's terminal. Open the link, sign in with the Google account meant for this worker, copy the code and send it here. If agy asks anything, answer here.",
 'Nový projekt': 'New project', 'Vytvoriť projekt': 'Create project', '+ Nový projekt…': '+ New project…',
 'Vytvorí projekt s vlastným orchestrátorom, repozitárom na Forgejo a skupinou workerov. Každý worker by mal patriť len jednému projektu.':
   'Creates a project with its own orchestrator, a repository on Forgejo and a group of workers. Each worker should belong to one project only.',
 'Názov (malé písmená, pomlčky)': 'Name (lowercase letters, dashes)', 'Repozitár (organizácia/repozitár)': 'Repository (organisation/repository)',
 'Jazyk, v ktorom sa ťa tím pýta': 'Language the team uses with you', 'Vízia — čo má projekt robiť (z toho plánuje autopilot)': 'Vision — what the project should do (the autopilot plans from it)',
 'Workeri pre tento projekt': 'Workers for this project', 'napr. eshop': 'e.g. shop', 'napr. acme/eshop': 'e.g. acme/shop',
 'default — všetci': 'default — all', 'žiadni workeri — pridaj ich v Nastaveniach': 'no workers — add them in Settings',
 'Delí sa': 'Splitting', 'Čaká': 'Waiting', 'Beží': 'Running', 'Kontrola': 'Checking', 'Hotovo': 'Done', 'Zaseknuté': 'Blocked', 'Zrušené': 'Cancelled',
 'Kontrola (testy + review)': 'Checking (tests + review)', 'Zaseknuté / zrušené': 'Blocked / cancelled',
 'odosielam…': 'sending…', 'Cieľ zaradený — plánovač ho rozdelí na úlohy do pol minúty.': 'Goal queued — the planner splits it into tasks within half a minute.',
 'Pridané do backlogu.': 'Added to the backlog.', 'Plánovač pripravuje roadmapu — ciele sa objavia v backlogu.': 'The planner is preparing a roadmap — its goals will appear in the backlog.',
 'Zahodiť tento cieľ z backlogu?': 'Remove this goal from the backlog?', 'Kill switch je v tvrdom režime. Spustiť workerov znova a pokračovať?': 'The kill switch is in hard mode. Start the workers again and continue?',
 'zastavujem…': 'stopping…', 'pokračujem…': 'continuing…',
 'Kto plánuje (poradie)': 'Who plans (in order)', 'Kto robí review (poradie)': 'Who reviews (in order)', 'Antigravity model pre plánovanie': 'Antigravity model for planning',
 'Antigravity model pre review': 'Antigravity model for reviews', 'Antigravity záložný (Gemini)': 'Antigravity fallback (Gemini)', 'Model workerov': 'Worker model',
 'Model workerov pre rizikové úlohy': 'Worker model for risky tasks', 'Anthropic API model (platené)': 'Anthropic API model (paid)', 'OpenAI-compatible model (platené)': 'OpenAI-compatible model (paid)',
 'Gemini API model (platené)': 'Gemini API model (paid)', 'Jazyk pre teba': 'Your language', 'Autopilot (sám vyberá ďalšie ciele)': 'Autopilot (picks the next goals itself)',
 'Automatický merge': 'Automatic merge', 'Pokračovať v konverzácii pri oprave': 'Continue the conversation on a rework', 'Max. vrátení na úlohu': 'Max. reworks per task',
 'Max. aktívnych cieľov': 'Max. active goals', 'Rozdeliť úlohu po N stropoch tokenov': 'Split a task after N token caps', 'Cieľová veľkosť úlohy (tokeny)': 'Target task size (tokens)',
 'Max. súbežných behov': 'Max. parallel runs', 'Strop tokenov na beh': 'Token cap per run', 'Časový limit behu (min)': 'Run time limit (min)',
 'Minimálna 5h kvóta workera (%)': "Worker's minimum 5-hour quota (%)", 'Minimálna týždenná kvóta (%)': 'Minimum weekly quota (%)', 'Kill switch po N zlyhaniach za sebou': 'Kill switch after N failures in a row',
 'Balík kontextu v zadaní (on / off / ab = polovica úloh, na meranie)': 'Context pack in each task (on / off / ab = half the tasks, to measure)',
 'Max. veľkosť balíka (znaky)': 'Max. pack size (characters)', 'Oprava testov v tom istom behu (max. kôl)': 'Fix failing tests in the same run (max. rounds)',
 'Max. súborov na úlohu (viac = rozdeliť vopred)': 'Max. files per task (more = split up front)', 'Model pre ľahké roly': 'Model for light roles', 'Ľahké roly (čiarkou)': 'Light roles (comma-separated)',
 'A/B test: alternatívny model workerov': 'A/B test: alternative worker model', 'A/B test: podiel úloh na ňom (%)': 'A/B test: share of tasks on it (%)',
 'Plánovať dopredu, keď workeri čakajú na kvótu': 'Plan ahead while the workers wait for quota', 'Údržba main bez AI, keď tím stojí (testy 3×, docstringy)': 'LLM-free maintenance of main while idle (tests 3×, docstrings)',
 'Testovací príkaz (prázdne = pytest)': 'Test command (empty = pytest)', 'Image pre testy (prázdne = image workera)': 'Test image (empty = the worker image)',
 'Notifikácie: URL (ntfy téma, Slack/Discord webhook)': 'Notifications: URL (ntfy topic, Slack/Discord webhook)', 'Notifikácie: formát': 'Notifications: format',
 'Notifikácie: udalosti': 'Notifications: events', 'Komprimovať prepisy behov po (dňoch)': 'Compress run transcripts after (days)', 'Projekt': 'Project',
 'Ako sa projekt testuje a kam ti tím píše.': 'How the project is tested and where the team notifies you.',
 'Claude Code (predplatné)': 'Claude Code (subscription)', 'Claude cez Antigravity': 'Claude via Antigravity', 'Gemini cez Antigravity': 'Gemini via Antigravity',
 'Anthropic API (platené)': 'Anthropic API (paid)', 'OpenAI API (platené)': 'OpenAI API (paid)', 'Gemini API (platené)': 'Gemini API (paid)',
 'vypnutý — projekt beží': 'off — the project is running', 'Pokračovať': 'Continue', 'Zastaviť projekt': 'Stop project', 'Zastaviť natvrdo': 'Stop hard',
 'Zastaviť všetky projekty': 'Stop all projects', 'Workeri tohto projektu': "This project's workers", 'prihlásiť do Antigravity': 'sign in to Antigravity',
 '+ Pridať workera': '+ Add worker', 'Orchestrátor': 'Orchestrator', 'Platené API (len ak sú vyššie zaškrtnuté a máš kľúč)': 'Paid APIs (only if ticked above and you have a key)',
 'rovnaký ako bežné úlohy': 'same as routine tasks', 'bez A/B testu': 'no A/B test', 'obnoviť zoznam modelov z Antigravity': 'refresh the model list from Antigravity',
 'Workeri — modely': 'Workers — models', 'Efektivita': 'Efficiency', 'Ako tím šetrí kvótu. Výsledky meraní sú v záložke Metriky.': 'How the team saves quota. The measurements are in the Metrics tab.',
 'Správanie': 'Behaviour', 'Limity': 'Limits', 'platia pre VŠETKY projekty': 'apply to ALL projects', 'Uložiť zmeny': 'Save changes', 'nič sa nezmenilo': 'nothing changed',
 'Vytvoriť nového workera (Forgejo účet, kontajner)? Potom ho prihlásiš jeho vlastným Google účtom.': 'Create a new worker (Forgejo account, container)? Then you sign it in with its own Google account.',
 'spúšťam…': 'starting…', '(čakám na výstup…)': '(waiting for output…)', 'Otvor a prihlás sa': 'Open it and sign in with', 'overujem prihlásenie…': 'checking the sign-in…',
 'foreman nebeží': 'foreman not running', 'všetky projekty': 'all projects', 'natvrdo — workeri zastavení': 'hard — workers stopped', 'foreman pozastavený': 'foreman paused',
 'foreman beží': 'foreman running', 'orchestrátor: Claude Code': 'orchestrator: Claude Code', 'stará verzia foremana — menej detailov': 'old foreman version — fewer details',
 'autopilot zap': 'autopilot on', 'autopilot vyp': 'autopilot off',
 'beží': 'running', 'čaká': 'waiting', 'kontrola': 'checking', 'hotovo dnes': 'done today', 'zaseknuté': 'blocked', 'tokeny dnes': 'tokens today', 'orchestrátor dnes': 'orchestrator today',
 'Orchestrátor (šéf)': 'Orchestrator (lead)', 'nič nerieši — čaká na workerov': 'idle — waiting for the workers', 'voľný': 'free', 'neznámy dôvod': 'unknown reason',
 'požičaný orchestrátorovi (Claude cez Antigravity)': 'lent to the orchestrator (Claude via Antigravity)',
 'ešte nekontrolované': 'not checked yet', 'skryť graf závislostí': 'hide dependency graph', 'graf závislostí': 'dependency graph',
 'Žiadne ciele — zadaj prvý hore v „Nový cieľ“.': 'No goals yet — enter the first one above under “New goal”.', 'Nič nebeží.': 'Nothing is running.',
 'Cieľ': 'Goal', 'Prečo zaseknuté': 'Why blocked', 'Posledná spätná väzba workerovi': 'Latest feedback to the worker', 'História': 'History',
 'Akceptačné kritériá': 'Acceptance criteria', 'Súbory': 'Files', 'Mutačné testy': 'Mutation tests', 'Zmeny (diff)': 'Changes (diff)', 'zobraziť diff': 'show diff', 'Zadanie': 'Spec',
 'Zatiaľ nič.': 'Nothing yet.', 'zatiaľ nič': 'nothing yet', 'Žiadne AI momentálne nebežia — prepis sa zjaví, keď niečo pobeží.': 'No AI is running right now — the transcript appears when one runs.',
 'Časová os potrebuje novšiu verziu foremana.': 'The timeline needs a newer foreman.', 'Za posledných 24 h žiadne behy.': 'No runs in the last 24 h.',
 'hotové': 'done', 'strop tokenov / čas': 'token cap / time', 'zlyhalo': 'failed', 'času pracoval': 'of the time working', 'Posledných 24 hodín': 'Last 24 hours',
 'Medzery v pruhu = worker stál. Klik na blok otvorí úlohu.': 'Gaps = the worker was idle. Click a block to open its task.',
 'Metriky potrebujú novšiu verziu foremana.': 'Metrics need a newer foreman.', 'hotových úloh': 'tasks done', 'schválené na 1. pokus': 'approved first time',
 'vrátení na opravu': 'sent back', 'zaseknutých': 'blocked', 'tokenov na hotovú úlohu': 'tokens per done task', 'Hotové úlohy za deň': 'Tasks done per day', 'Podľa roly': 'By role',
 'rola': 'role', 'úloh': 'tasks', 'hotovo': 'done', '1. pokus': '1st try', 'vrátení': 'sent back', 'tokeny/úloha': 'tokens/task', 'riadky': 'lines',
 'behov': 'runs', 'strop': 'cap', 'kvóta/beh': 'quota/run', 'kvóta/hotová úloha': 'quota/done task', 'nástroje/beh': 'tools/run', 'tokeny/beh': 'tokens/run',
 'Efektivita podľa modelu workerov': 'Efficiency by worker model', 'kvóta = % týždenného limitu jedného účtu (agy /usage pred a po behu); nižšie je lepšie':
   "quota = % of one account's weekly limit (agy /usage before and after the run); lower is better", 'Balík kontextu — A/B': 'Context pack — A/B',
 'pri CONTEXT_PACK=ab dostane balík polovica úloh; porovnaj kvótu na hotovú úlohu a počet nástrojov na beh': 'with CONTEXT_PACK=ab half the tasks get the pack; compare quota per done task and tools per run',
 'Kontrola testov v behu': 'Self-check in the run', 'behov s kontrolou': 'runs with a self-check', 'zelené hneď': 'green at once', 'opravené v tom istom behu': 'fixed in the same run',
 'ostali červené': 'stayed red', 'Údržba main (bez AI)': 'Maintenance of main (no AI)', 'Orchestrátor za 7 dní (kto plánoval a robil review)': 'Orchestrator, last 7 days (who planned and reviewed)',
 'poskytovateľ': 'provider', 'volaní': 'calls', 'priemer': 'average', 'Orchestrátor premýšľa… (aj 1–2 min)': 'The orchestrator is thinking… (1–2 min)', '(bez odpovede)': '(no answer)',
 'vyššie': 'up', 'nižšie': 'down', 'zahodiť': 'remove', 'Prázdny.': 'Empty.', 'Prázdny — keď bude worker voľný, autopilot vyberie ďalší cieľ z vízie.': 'Empty — when a worker is free, the autopilot picks the next goal from the vision.',
 'tvoja odpoveď (plánovač sa ňou bude riadiť)': 'your answer (the planner will follow it)', 'Odpovedať': 'Answer', 'Zahodiť': 'Dismiss', 'otázka nie je dôležitá': 'the question does not matter',
 '… pracujem': '… working', '✔ Projekt je pripravený — vyber ho hore a zadaj prvý goal.': '✔ The project is ready — pick it at the top and enter its first goal.',
 '⚠ Projekt je vytvorený, len jeho služba potrebuje root — spusti príkaz vyššie.': '⚠ The project exists; only its service needs root — run the command above.',
 '✘ Zlyhalo — pozri výpis vyššie.': '✘ Failed — see the output above.', 'neznáma': 'unknown', 'projekt už existuje': 'the project already exists',
 "malé písmená, číslice a pomlčky (nie 'default')": "lowercase letters, digits and dashes (not 'default')", 'organizácia/repozitár, napr. acme/shop': 'organisation/repository, e.g. acme/shop',
 'neplatný worker': 'invalid worker', 'názov jazyka, napr. Slovak': 'a language name, e.g. English',
 'plánovanie': 'planning', 'plánovanie úloh': 'planning tasks', 'kontrola, či je cieľ splnený alebo treba opravu': 'checking whether the goal is met or needs a fix',
 'rebase na main': 'rebase onto main', 'testy v čistom kontajneri': 'tests in a clean container', 'review orchestrátorom': 'review by the orchestrator', 'roadmapa projektu': 'project roadmap',
 'autopilot vyberá ďalší cieľ z vízie': 'autopilot picks the next goal from the vision', 'údržba: testy na main 3x, mapa kódu (bez AI)': 'maintenance: tests on main 3×, code map (no AI)',
};
const PHASE_RX = '(plánovanie úloh|plánovanie|kontrola, či je cieľ splnený alebo treba opravu|rebase na main|testy v čistom kontajneri|review orchestrátorom|mutačné testy \\(\\d+\\)|merge|roadmapa projektu|autopilot vyberá ďalší cieľ z vízie|údržba: testy na main 3x, mapa kódu \\(bez AI\\)|kontrola)';
const tw = s => EN[s] != null ? EN[s] : s.replace(/^mutačné testy \((\d+)\)$/, 'mutation tests ($1)');
const STATUS_EN = {'Delí sa': 'Splitting', 'Čaká': 'Waiting', 'Beží': 'Running', 'Kontrola': 'Checking', 'Hotovo': 'Done', 'Zaseknuté': 'Blocked', 'Zrušené': 'Cancelled'};
const EN_RX = [
 [/^(Delí sa|Čaká|Beží|Kontrola|Hotovo|Zaseknuté|Zrušené) · (.*)$/, (m, a, b) => STATUS_EN[a] + ' · ' + b],
 [new RegExp('^' + PHASE_RX + '( — .*)?$'), (m, a, b) => tw(a) + (b || '')],
 [/^Nepodarilo sa: (.*)$/s, 'Failed: $1'],
 [/^Zastaviť (VŠETKY projekty|projekt „(.*)“)( NATVRDO \(zastaví aj kontajnery workerov\))?\?$/, (m, a, p, h) => (p != null ? 'Stop project “' + p + '”' : 'Stop ALL projects') + (h ? ' HARD (also stops the worker containers)' : '') + '?'],
 [/^Nastavenia nie sú dostupné: (.*)$/s, 'Settings are not available: $1'],
 [/^ZAPNUTÝ( pre všetky projekty| pre tento projekt)( \(natvrdo\))? — (.*)$/s, (m, a, h, r) => 'ON ' + (a.includes('všetky') ? 'for all projects' : 'for this project') + (h ? ' (hard)' : '') + ' — ' + r],
 [/^Zaškrtnutí patria projektu „(.*)“\. Nikto zaškrtnutý = všetci\. Každý worker je samostatný Antigravity účet\.$/, 'Ticked workers belong to project “$1”. None ticked = all. Each worker is its own Antigravity account.'],
 [/^predvolený \((.*)\)$/, 'default ($1)'],
 [/^Workerov je viac \((\d+)\) ako MAX_PARALLEL — niektorí budú stáť\.$/, 'There are more workers ($1) than MAX_PARALLEL — some will idle.'],
 [/^uložené: (.*) — platí od najbližšieho kola \(do 20 s\)$/, 'saved: $1 — applies from the next round (within 20 s)'],
 [/^neuložené — oprav označené polia(.*)$/s, 'not saved — fix the marked fields$1'],
 [/^vytváram (.*)… \(prvý raz aj pár minút\)$/, 'creating $1… (the first time takes a few minutes)'],
 [/^(.*) vytvorený — teraz ho prihlás$/, '$1 created — now sign it in'], [/^zlyhalo: (.*)$/s, 'failed: $1'],
 [/^Prihlásenie (\S+) do Antigravity$/, 'Sign $1 in to Antigravity'], [/^účtom pre (.*)$/, 'the account for $1'],
 [/^(.*) je prihlásený a vidí svoju kvótu\. Zaškrtni ho v projekte a ulož\.$/, '$1 is signed in and sees its quota. Tick it in the project and save.'],
 [/^(.*) zatiaľ nie je prihlásený — skús to znova\.$/, '$1 is not signed in yet — try again.'],
 [/^foreman: načítavam$/, 'foreman: loading'],
 [/^dashboard nemá čerstvé dáta \((\d+) min staré\) - nedôveruj stavu nižšie(?: · príčina: (.*))?$/s, (m, n, c) => 'the dashboard has no fresh data (' + n + ' min old) - do not trust the state below' + (c ? ' · cause: ' + c : '')],
 [/^kill switch ZAP — (.*)$/s, 'kill switch ON — $1'],
 [/^orchestrátor: Claude cez Antigravity \(Claude Code späť (.*)\)$/, 'orchestrator: Claude via Antigravity (Claude Code back $1)'],
 [/^(\d+) worker\(y\) dočasne bez náhradného orchestrátora$/, '$1 worker(s) temporarily without a substitute orchestrator'],
 [/^(.*) · plánuje, kontroluje, merguje$/, (m, a) => (a === 'Claude cez Antigravity (náhradník)' ? 'Claude via Antigravity (substitute)' : a) + ' · plans, reviews, merges'],
 [/^čaká: (.*)$/s, 'waiting: $1'], [/^čaká na (.*)$/, 'waits for $1'], [/^pokus (\d+\/\d+)$/, 'attempt $1'],
 [/^(\S+) · pokus (\d+\/\d+) · beží (.+?)(?: · (\d+) nástrojov)?$/, (m, id, a, t, n) => id + ' · attempt ' + a + ' · running ' + t + (n ? ' · ' + n + ' tools' : '')],
 [/^pauza do ([\d:]+): (.*)$/s, (m, t, r) => 'paused until ' + t + ': ' + (r === 'neznámy dôvod' ? 'unknown reason' : r)],
 [/^kontrola: (.*)$/, 'checked: $1'], [/^Hotové ciele \((\d+)\)$/, 'Finished goals ($1)'], [/^Otázky plánovača \((\d+)\)$/, "The planner's questions ($1)"],
 [/^\(nepodarilo sa načítať: (.*)\)$/s, '(could not load: $1)'], [/^\(chyba spojenia: (.*)\)$/s, '(connection error: $1)'],
 [/^(.*) · (\S+) · (\d+) min · (\d+)k tokenov$/, '$1 · $2 · $3 min · $4k tokens'], [/^Chyba stránky: (.*)$/s, 'Page error: $1'],
];
// text inside these elements is content, not interface - it stays as written
const SKIP = '#grid, .term, pre, .diff, .answer, .goal-text, .bltext, .qtext, .goal-sum, .hist, .ev, .node-task, .card .t, .sheet > h2, .critrow > span:not(.cm), textarea';
// interface text that mixes words with numbers inside one text node: word-level replacements by context
const CTX = [
 ['.goal-meta', [[/^plánuje sa/, 'planning'], [/^beží/, 'running'], [/^hotový/, 'done'], [/^zaseknutý/, 'blocked'], [/ · úlohy /, ' · tasks '], [/ · kritériá /, ' · criteria '], [/ · kolo /, ' · round '], [/ riadkov/, ' lines']]],
 ['.sheet > .muted', [[/ · pokus /, ' · attempt '], [/riziko: /, 'risk: '], [/k tokenov/, 'k tokens'], [/ riadkov/, ' lines'], [/súbor\(y\) spolu/, 'file(s) total'],
                       [/ · (Delí sa|Čaká|Beží|Kontrola|Hotovo|Zaseknuté|Zrušené) · /, (m, a) => ' · ' + STATUS_EN[a] + ' · ']]],
 ['.maint', [[/testy: /, 'tests: '], [/modulov bez docstringu: /, 'modules without a docstring: ']]],
];
function trText(s, el) {
  if (LANG === 'sk' || !s || !/[A-Za-zÀ-ž]/.test(s)) return s;
  const k = s.trim().replace(/\s+/g, ' ');
  if (!k) return s;
  if (EN[k] != null) return s.replace(s.trim(), EN[k]);
  for (const [rx, f] of EN_RX) if (rx.test(k)) return s.replace(s.trim(), k.replace(rx, f));
  if (el) for (const [sel, reps] of CTX) if (el.closest(sel)) { let v = s; for (const [rx, f] of reps) v = v.replace(rx, f); return v; }
  return s;
}
const T = s => trText(String(s), null);
function translate(root) {
  if (LANG === 'sk' || !root) return;
  if (root.nodeType === 3) { const p = root.parentElement; if (p && !p.closest(SKIP)) { const v = trText(root.nodeValue, p); if (v !== root.nodeValue) root.nodeValue = v; } return; }
  if (root.nodeType !== 1 || root.closest(SKIP)) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const n of nodes) { const p = n.parentElement; if (!p || p.closest(SKIP)) continue; const v = trText(n.nodeValue, p); if (v !== n.nodeValue) n.nodeValue = v; }
  for (const el of [root, ...root.querySelectorAll('[placeholder],[title]')]) for (const a of ['placeholder', 'title']) {
    const v = el.getAttribute && el.getAttribute(a); if (v) { const t = trText(v, el); if (t !== v) el.setAttribute(a, t); } }
}
if (LANG !== 'sk') {
  const _confirm = window.confirm.bind(window), _alert = window.alert.bind(window);
  window.confirm = m => _confirm(T(m)); window.alert = m => _alert(T(m));
}
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const tfmt = ts => { const d = typeof ts === 'number' ? new Date(ts*1000) : new Date(ts); return isNaN(d) ? '' : d.toLocaleTimeString('sk-SK', {hour:'2-digit', minute:'2-digit'}); };
const dfmt = ts => { const d = new Date(ts); if (isNaN(d)) return ''; const today = new Date().toDateString() === d.toDateString();
  return today ? d.toLocaleTimeString('sk-SK', {hour:'2-digit', minute:'2-digit', second:'2-digit'}) : d.toLocaleString('sk-SK', {day:'numeric', month:'numeric', hour:'2-digit', minute:'2-digit'}); };
const ago = ts => { if (!ts) return ''; const s = Math.max(0, Math.round(Date.now()/1000 - (typeof ts === 'number' ? ts : Date.parse(ts)/1000)));
  return s < 60 ? s + ' s' : s < 3600 ? Math.round(s/60) + ' min' : Math.round(s/3600) + ' h'; };
const STATUS = {splitting:'Delí sa', pending:'Čaká', running:'Beží', gate:'Kontrola', review:'Kontrola', done:'Hotovo', blocked:'Zaseknuté', cancelled:'Zrušené'};
const roleCls = r => /secur/i.test(r) ? 'sec' : (/test/i.test(r) && !/develop/i.test(r)) ? 'test' : '';
let tab = 'overview', F = null, P = {roster:{}, panes:[]}, openTask = null, project = 'default';
try { project = localStorage.getItem('af-project') || 'default'; } catch (e) {}
const inPool = w => !F || !(F.pool || []).length || F.pool.includes(w);
try { tab = localStorage.getItem('nexus-tab') || 'overview'; } catch (e) {}

function setTab(t) {
  tab = t;
  for (const k in lastHTML) delete lastHTML[k];
  try { localStorage.setItem('nexus-tab', t); } catch (e) {}
  document.querySelectorAll('nav button').forEach(b => b.classList.toggle('on', b.dataset.t === t));
  document.querySelectorAll('main section').forEach(s => s.classList.toggle('on', s.id === 't-' + t));
  render();
}
document.getElementById('nav').addEventListener('click', e => { const b = e.target.closest('button'); if (b) setTab(b.dataset.t); });
document.getElementById('detail').addEventListener('click', e => { if (e.target.id === 'detail' || e.target.classList.contains('close')) { openTask = null; lastDetailHtml = ''; e.currentTarget.classList.remove('on'); } });
document.addEventListener('click', e => {
  const df = e.target.closest('[data-diff]');
  if (df) { loadDiff(df.dataset.diff); return; }
  const dg = e.target.closest('[data-dag]');
  if (dg) { const id = dg.dataset.dag; openDags.has(id) ? openDags.delete(id) : openDags.add(id); render(true); return; }
  const c = e.target.closest('[data-task]');
  if (c) { openTask = c.dataset.task; renderDetail(); }
});
document.getElementById('ask-btn').addEventListener('click', askTeam);

// ---- the owner's input: new goals, backlog order, answers to the planner's questions
const post = async (url, body) => { try { return await (await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify(Object.assign({project}, body))})).json(); } catch (e) { return {ok: false, message: String(e)}; } };
async function addGoal(mode) {
  const ta = document.getElementById('goal-text'), msg = document.getElementById('goal-msg'), text = ta.value.trim();
  if (!text) { ta.focus(); return; }
  msg.textContent = 'odosielam…';
  const r = await post('/api/goal', {text, mode});
  msg.textContent = r.ok ? {now: 'Cieľ zaradený — plánovač ho rozdelí na úlohy do pol minúty.', backlog: 'Pridané do backlogu.',
    roadmap: 'Plánovač pripravuje roadmapu — ciele sa objavia v backlogu.'}[mode] : 'Nepodarilo sa: ' + (r.message || r.error || '');
  if (r.ok) ta.value = '';
  setTimeout(poll, 1500);
}
document.getElementById('goal-now').addEventListener('click', () => addGoal('now'));
document.getElementById('goal-backlog').addEventListener('click', () => addGoal('backlog'));
document.getElementById('goal-roadmap').addEventListener('click', () => addGoal('roadmap'));
document.addEventListener('click', async e => {
  const b = e.target.closest('[data-bl]');
  if (b) { if (b.dataset.bl === 'drop' && !confirm('Zahodiť tento cieľ z backlogu?')) return;
    await post('/api/backlog', {action: b.dataset.bl, n: b.dataset.n, to: b.dataset.to || ''}); setTimeout(poll, 800); return; }
  const qa = e.target.closest('[data-qa]'), qd = e.target.closest('[data-qd]');
  if (qa || qd) {
    const n = (qa || qd).dataset[qa ? 'qa' : 'qd'], inp = document.querySelector('.qin[data-qn="' + n + '"]');
    if (qa && !inp.value.trim()) { inp.focus(); return; }
    await post('/api/answer', {n, answer: qa ? inp.value.trim() : ''});
    document.activeElement && document.activeElement.blur(); setTimeout(poll, 800);
  }
});

// ---- kill switch (per project; "all" = every project)
let ksBusy = false;
async function killswitch(action, opts) {
  opts = opts || {};
  if (ksBusy) return;
  const hard = !!opts.hard, all = !!opts.all;
  if (action === 'on' && !confirm((all ? 'Zastaviť VŠETKY projekty' : 'Zastaviť projekt „' + project + '“') + (hard ? ' NATVRDO (zastaví aj kontajnery workerov)' : '') + '?')) return;
  if (action === 'off' && F && F.killswitch && F.killswitch.hard && !confirm('Kill switch je v tvrdom režime. Spustiť workerov znova a pokračovať?')) return;
  ksBusy = true;
  const box = document.getElementById('resume-result');
  box.style.display = 'block'; box.innerHTML = '<div class="ev">' + (action === 'on' ? 'zastavujem…' : 'pokračujem…') + '</div>';
  let res;
  try { res = await (await fetch('/api/killswitch', {method: 'POST', headers: {'Content-Type': 'application/json'},
                       body: JSON.stringify({project, action, hard, all})})).json(); }
  catch (e) { res = {ok: false, steps: [{label: 'spojenie', ok: false, output: String(e)}]}; }
  ksBusy = false;
  box.innerHTML = (res.steps || []).map(s => '<div class="ev ' + (s.ok ? 'good' : 'bad') + '">' + (s.ok ? '✓ ' : '✗ ') +
    esc(s.label) + (s.output ? ': ' + esc(s.output) : '') + '</div>').join('') || esc(res.error || '');
  poll();
}
function resumeDev() { killswitch('off', {all: !!(F && F.killswitch && F.killswitch.scope === 'all')}); }

// ---- settings tab
let S = null, sLoadedFor = null, sErr = {}, sMsg = '';
const LABEL = {PLAN_PROVIDERS: 'Kto plánuje (poradie)', REVIEW_PROVIDERS: 'Kto robí review (poradie)', CLAUDE_MODEL: 'Claude Code model',
  AGY_PLAN_MODEL: 'Antigravity model pre plánovanie', AGY_REVIEW_MODEL: 'Antigravity model pre review', AGY_FALLBACK_MODEL: 'Antigravity záložný (Gemini)',
  WORKER_MODEL: 'Model workerov', WORKER_MODEL_RISKY: 'Model workerov pre rizikové úlohy', ANTHROPIC_MODEL: 'Anthropic API model (platené)',
  OPENAI_MODEL: 'OpenAI-compatible model (platené)', GEMINI_MODEL: 'Gemini API model (platené)', OWNER_LANG: 'Jazyk pre teba',
  AUTOPILOT: 'Autopilot (sám vyberá ďalšie ciele)', AUTO_MERGE: 'Automatický merge', CONTINUE_CONVERSATIONS: 'Pokračovať v konverzácii pri oprave',
  MAX_REWORK: 'Max. vrátení na úlohu', MAX_ACTIVE_GOALS: 'Max. aktívnych cieľov', SPLIT_AFTER_CAPS: 'Rozdeliť úlohu po N stropoch tokenov',
  TASK_TOKEN_BUDGET: 'Cieľová veľkosť úlohy (tokeny)', MAX_PARALLEL: 'Max. súbežných behov', RUN_TOKEN_CAP: 'Strop tokenov na beh',
  RUN_TIMEOUT_MIN: 'Časový limit behu (min)', QUOTA_MIN_5H_PCT: 'Minimálna 5h kvóta workera (%)', QUOTA_MIN_WEEK_PCT: 'Minimálna týždenná kvóta (%)',
  CONSEC_FAIL_TRIP: 'Kill switch po N zlyhaniach za sebou',
  CONTEXT_PACK: 'Balík kontextu v zadaní (on / off / ab = polovica úloh, na meranie)', CONTEXT_PACK_CHARS: 'Max. veľkosť balíka (znaky)',
  SELF_CHECK_ROUNDS: 'Oprava testov v tom istom behu (max. kôl)', MAX_TASK_FILES: 'Max. súborov na úlohu (viac = rozdeliť vopred)',
  WORKER_MODEL_LIGHT: 'Model pre ľahké roly', LIGHT_ROLES: 'Ľahké roly (čiarkou)', AB_MODEL: 'A/B test: alternatívny model workerov',
  AB_SHARE: 'A/B test: podiel úloh na ňom (%)', PLAN_AHEAD: 'Plánovať dopredu, keď workeri čakajú na kvótu',
  MAINTENANCE: 'Údržba main bez AI, keď tím stojí (testy 3×, docstringy)', TEST_CMD: 'Testovací príkaz (prázdne = pytest)',
  GATE_IMAGE: 'Image pre testy (prázdne = image workera)', NOTIFY_URL: 'Notifikácie: URL (ntfy téma, Slack/Discord webhook)',
  NOTIFY_FORMAT: 'Notifikácie: formát', NOTIFY_EVENTS: 'Notifikácie: udalosti', RUNS_KEEP_DAYS: 'Komprimovať prepisy behov po (dňoch)'};
const PROVLABEL = {'claude-cli': 'Claude Code (predplatné)', 'agy-claude': 'Claude cez Antigravity', 'agy-gemini': 'Gemini cez Antigravity',
  'anthropic-api': 'Anthropic API (platené)', 'openai-api': 'OpenAI API (platené)', 'gemini-api': 'Gemini API (platené)'};
async function loadSettings(force) {
  if (!force && sLoadedFor === project && S) return;
  sLoadedFor = project;
  try { S = await (await fetch('/api/settings?project=' + encodeURIComponent(project))).json(); } catch (e) { S = {error: String(e)}; }
  render(true);
}
function sval(k) { const el = document.getElementById('set-' + k); if (!el) return null;
  if (el.dataset.kind === 'prov') return [...el.querySelectorAll('.prov')].filter(r => r.querySelector('input').checked).map(r => r.dataset.p).join(',');
  if (el.dataset.kind === 'pool') return [...el.querySelectorAll('input:checked')].map(i => i.value).join(',');
  if (el.type === 'checkbox') return el.checked ? '1' : '0';
  return el.value.trim(); }
function provEditor(k, v) {
  const on = String(v || '').split(',').filter(Boolean), all = on.concat((S.providers || []).filter(p => !on.includes(p)));
  return '<div class="provlist" id="set-' + k + '" data-kind="prov">' + all.map(p => '<div class="prov" data-p="' + esc(p) + '"><label><input type="checkbox"' +
    (on.includes(p) ? ' checked' : '') + '> ' + esc(PROVLABEL[p] || p) + '</label><span><button class="mini" data-mv="-1">↑</button><button class="mini" data-mv="1">↓</button></span></div>').join('') + '</div>';
}
function modelSel(k, v, allowEmpty, emptyLabel) {
  const ms = (S.models || []).map(m => m.id), opts = (allowEmpty ? [''] : []).concat(ms.includes(v) || !v ? ms : [v].concat(ms));
  return '<select id="set-' + k + '">' + opts.map(m => '<option value="' + esc(m) + '"' + (m === v ? ' selected' : '') + '>' +
    esc(m ? ((S.models || []).find(x => x.id === m) || {name: m}).name + ' (' + m + ')' : emptyLabel) + '</option>').join('') + '</select>';
}
function field(k, inner) { return '<div class="setrow"><label for="set-' + k + '">' + esc(LABEL[k] || k) + '</label><div>' + inner +
  (sErr[k] ? '<div class="seterr">' + esc(sErr[k]) + '</div>' : '') + '</div></div>'; }
function settingsHtml() {
  if (!S) { loadSettings(); return '<div class="empty">načítavam…</div>'; }
  if (S.error) return '<div class="empty">Nastavenia nie sú dostupné: ' + esc(S.error) + '</div>';
  const V = S.values || {}, num = k => '<input type="number" id="set-' + k + '" value="' + esc(V[k]) + '">',
        txt = k => '<input type="text" id="set-' + k + '" value="' + esc(V[k]) + '">', tog = k => '<input type="checkbox" id="set-' + k + '"' + (String(V[k]) === '1' ? ' checked' : '') + '>';
  const ks = F && F.killswitch, pool = String(V.WORKERS || '').split(',').filter(Boolean);
  let h = '<div class="setcard"><h3>Kill switch</h3><div class="muted" style="font-size:12.5px;margin-bottom:8px">' +
    (ks ? 'ZAPNUTÝ' + (ks.scope === 'all' ? ' pre všetky projekty' : ' pre tento projekt') + (ks.hard ? ' (natvrdo)' : '') + ' — ' + esc(ks.reason || '') : 'vypnutý — projekt beží') + '</div>' +
    '<div class="btnrow">' + (ks ? '<button class="resume-btn" onclick="resumeDev()">Pokračovať</button>' :
      '<button class="warnbtn" onclick="killswitch(\'on\')">Zastaviť projekt</button><button class="badbtn" onclick="killswitch(\'on\',{hard:true})">Zastaviť natvrdo</button>') +
    '<button class="badbtn" onclick="killswitch(\'on\',{all:true})">Zastaviť všetky projekty</button></div></div>';
  h += '<div class="setcard"><h3>Workeri tohto projektu</h3><div class="muted" style="font-size:12.5px">Zaškrtnutí patria projektu „' + esc(project) +
    '“. Nikto zaškrtnutý = všetci. Každý worker je samostatný Antigravity účet.</div><div id="set-WORKERS" data-kind="pool" class="pool">' +
    (S.workers || []).map(w => '<div class="poolrow"><label><input type="checkbox" value="' + esc(w.name) + '"' + (pool.includes(w.name) ? ' checked' : '') + '> ' + esc(w.name) +
      ' <span class="muted">(' + esc(w.state) + ')</span></label><button class="mini" data-login="' + esc(w.name) + '">prihlásiť do Antigravity</button></div>').join('') +
    '</div>' + (sErr.WORKERS ? '<div class="seterr">' + esc(sErr.WORKERS) + '</div>' : '') +
    '<div class="btnrow"><button class="mini" id="add-worker">+ Pridať workera</button><span id="addw-status" class="muted" style="font-size:12.5px"></span></div></div>';
  h += '<div class="setcard"><h3>Orchestrátor</h3>' + field('PLAN_PROVIDERS', provEditor('PLAN_PROVIDERS', V.PLAN_PROVIDERS)) +
    field('REVIEW_PROVIDERS', provEditor('REVIEW_PROVIDERS', V.REVIEW_PROVIDERS)) +
    field('CLAUDE_MODEL', '<select id="set-CLAUDE_MODEL">' + ['sonnet', 'opus', 'haiku', 'fable'].concat(['sonnet', 'opus', 'haiku', 'fable'].includes(V.CLAUDE_MODEL) ? [] : [V.CLAUDE_MODEL])
      .map(m => '<option' + (m === V.CLAUDE_MODEL ? ' selected' : '') + '>' + esc(m) + '</option>').join('') + '</select>') +
    field('AGY_PLAN_MODEL', modelSel('AGY_PLAN_MODEL', V.AGY_PLAN_MODEL)) + field('AGY_REVIEW_MODEL', modelSel('AGY_REVIEW_MODEL', V.AGY_REVIEW_MODEL)) +
    field('AGY_FALLBACK_MODEL', modelSel('AGY_FALLBACK_MODEL', V.AGY_FALLBACK_MODEL)) +
    '<details><summary class="muted">Platené API (len ak sú vyššie zaškrtnuté a máš kľúč)</summary>' + field('ANTHROPIC_MODEL', txt('ANTHROPIC_MODEL')) +
    field('OPENAI_MODEL', txt('OPENAI_MODEL')) + field('GEMINI_MODEL', txt('GEMINI_MODEL')) + '</details></div>';
  h += '<div class="setcard"><h3>Workeri — modely</h3>' + field('WORKER_MODEL', modelSel('WORKER_MODEL', V.WORKER_MODEL, true, 'predvolený (' + (S.limits_worker_model || '') + ')')) +
    field('WORKER_MODEL_RISKY', modelSel('WORKER_MODEL_RISKY', V.WORKER_MODEL_RISKY, true, 'rovnaký ako bežné úlohy')) +
    '<div class="btnrow"><button class="mini" id="refresh-models">obnoviť zoznam modelov z Antigravity</button></div></div>';
  h += '<div class="setcard"><h3>Projekt</h3><div class="muted" style="font-size:12.5px;margin-bottom:6px">Ako sa projekt testuje a kam ti tím píše.</div>' +
    field('TEST_CMD', txt('TEST_CMD')) + field('GATE_IMAGE', txt('GATE_IMAGE')) + field('NOTIFY_URL', txt('NOTIFY_URL')) +
    field('NOTIFY_FORMAT', '<select id="set-NOTIFY_FORMAT">' + ['ntfy', 'slack', 'discord', 'json'].map(x => '<option' + (x === V.NOTIFY_FORMAT ? ' selected' : '') + '>' + x + '</option>').join('') + '</select>') +
    field('NOTIFY_EVENTS', txt('NOTIFY_EVENTS')) + field('RUNS_KEEP_DAYS', num('RUNS_KEEP_DAYS')) + '</div>';
  h += '<div class="setcard"><h3>Efektivita</h3><div class="muted" style="font-size:12.5px;margin-bottom:6px">Ako tím šetrí kvótu. Výsledky meraní sú v záložke Metriky.</div>' +
    field('CONTEXT_PACK', '<select id="set-CONTEXT_PACK">' + ['on', 'ab', 'off'].map(x => '<option' + (x === V.CONTEXT_PACK ? ' selected' : '') + '>' + x + '</option>').join('') + '</select>') +
    field('CONTEXT_PACK_CHARS', num('CONTEXT_PACK_CHARS')) + field('SELF_CHECK_ROUNDS', num('SELF_CHECK_ROUNDS')) + field('MAX_TASK_FILES', num('MAX_TASK_FILES')) +
    field('WORKER_MODEL_LIGHT', modelSel('WORKER_MODEL_LIGHT', V.WORKER_MODEL_LIGHT, true, 'rovnaký ako bežné úlohy')) + field('LIGHT_ROLES', txt('LIGHT_ROLES')) +
    field('AB_MODEL', modelSel('AB_MODEL', V.AB_MODEL, true, 'bez A/B testu')) + field('AB_SHARE', num('AB_SHARE')) +
    field('PLAN_AHEAD', tog('PLAN_AHEAD')) + field('MAINTENANCE', tog('MAINTENANCE')) + '</div>';
  h += '<div class="setcard"><h3>Správanie</h3>' + field('AUTOPILOT', tog('AUTOPILOT')) + field('AUTO_MERGE', tog('AUTO_MERGE')) +
    field('CONTINUE_CONVERSATIONS', tog('CONTINUE_CONVERSATIONS')) + field('OWNER_LANG', txt('OWNER_LANG')) + field('MAX_REWORK', num('MAX_REWORK')) +
    field('MAX_ACTIVE_GOALS', num('MAX_ACTIVE_GOALS')) + field('SPLIT_AFTER_CAPS', num('SPLIT_AFTER_CAPS')) + field('TASK_TOKEN_BUDGET', num('TASK_TOKEN_BUDGET')) + '</div>';
  h += '<div class="setcard"><h3>Limity <span class="pill warn">platia pre VŠETKY projekty</span></h3>' +
    ['MAX_PARALLEL', 'RUN_TOKEN_CAP', 'RUN_TIMEOUT_MIN', 'QUOTA_MIN_5H_PCT', 'QUOTA_MIN_WEEK_PCT', 'CONSEC_FAIL_TRIP'].map(k => field(k, num(k))).join('') +
    ((S.workers || []).length > Number(V.MAX_PARALLEL) ? '<div class="seterr">Workerov je viac (' + S.workers.length + ') ako MAX_PARALLEL — niektorí budú stáť.</div>' : '') + '</div>';
  h += '<div class="btnrow sticky"><button class="resume-btn" id="save-settings">Uložiť zmeny</button><span class="muted" style="font-size:12.5px">' + esc(sMsg) + '</span></div>';
  return h;
}
async function saveSettings() {
  const V = S.values || {}, changes = {};
  for (const k of Object.keys(V)) { const v = sval(k); if (v !== null && String(v) !== String(V[k])) changes[k] = v; }
  if (!Object.keys(changes).length) { sMsg = 'nič sa nezmenilo'; render(true); return; }
  let r;
  try { r = await (await fetch('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({project, changes})})).json(); }
  catch (e) { r = {ok: false, errors: {_: String(e)}}; }
  sErr = r.errors || {};
  sMsg = r.ok ? 'uložené: ' + Object.keys(changes).join(', ') + ' — platí od najbližšieho kola (do 20 s)' : 'neuložené — oprav označené polia' + (sErr._ ? ': ' + sErr._ : '');
  if (r.ok) { await loadSettings(true); return; }
  // keep everything the owner typed: only mark the bad fields
  document.querySelectorAll('#settings .seterr.live').forEach(x => x.remove());
  for (const [k, msg] of Object.entries(sErr)) {
    const el = document.getElementById('set-' + k);
    if (el) el.insertAdjacentHTML('afterend', '<div class="seterr live">' + esc(msg) + '</div>');
  }
  const st = document.querySelector('#settings .btnrow.sticky .muted'); if (st) st.textContent = sMsg;
  sErr = {};
}
document.addEventListener('click', async e => {
  const mv = e.target.closest('[data-mv]');
  if (mv) { const row = mv.closest('.prov'), d = Number(mv.dataset.mv);
    if (d < 0 && row.previousElementSibling) row.parentNode.insertBefore(row, row.previousElementSibling);
    if (d > 0 && row.nextElementSibling) row.parentNode.insertBefore(row.nextElementSibling, row); return; }
  if (e.target.id === 'save-settings') { saveSettings(); return; }
  if (e.target.id === 'refresh-models') { e.target.textContent = 'načítavam…';
    try { await fetch('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({project, refresh_models: true})}); } catch (err) {}
    loadSettings(true); return; }
  if (e.target.id === 'add-worker') { addWorker(); return; }
  if (e.target.id === 'np-create') { createProject(); return; }
  if (e.target.id === 'np-close') { document.getElementById('newproj').classList.remove('on'); projAt = 0; loadProjects().then(() => {
    const sel = document.getElementById('proj'); if (sel.value !== project) { sel.value = project; sel.dispatchEvent(new Event('change')); } }); return; }
  const lg = e.target.closest('[data-login]'); if (lg) { openLogin(lg.dataset.login); return; }
  if (e.target.id === 'login-send') { const i = document.getElementById('login-in');
    await fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({worker: loginW, action: 'input', text: i.value})}); i.value = ''; return; }
  if (e.target.id === 'login-done' || e.target.id === 'login-close') { closeLogin(e.target.id === 'login-done'); return; }
});

// ---- add a worker, then log it in
async function addWorker() {
  if (!confirm('Vytvoriť nového workera (Forgejo účet, kontajner)? Potom ho prihlásiš jeho vlastným Google účtom.')) return;
  const st = document.getElementById('addw-status');
  let j = await (await fetch('/api/workers', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({project, action: 'add'})})).json();
  while (j.state === 'running') { st.textContent = 'vytváram ' + (j.worker || '') + '… (prvý raz aj pár minút)'; await new Promise(r => setTimeout(r, 3000));
    j = await (await fetch('/api/jobs/' + encodeURIComponent(j.id))).json(); }
  st.textContent = j.state === 'done' ? j.worker + ' vytvorený — teraz ho prihlás' : 'zlyhalo: ' + (j.log || '').slice(-300);
  await loadSettings(true);
  if (j.state === 'done') openLogin(j.worker);
}
let loginW = null, loginTimer = null;
async function openLogin(w) {
  loginW = w;
  const m = document.getElementById('login'); m.classList.add('on');
  document.getElementById('login-title').textContent = 'Prihlásenie ' + w + ' do Antigravity';
  document.getElementById('login-out').textContent = 'spúšťam…';
  await fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({worker: w, action: 'start'})});
  clearInterval(loginTimer);
  loginTimer = setInterval(async () => {
    let r; try { r = await (await fetch('/api/login?worker=' + encodeURIComponent(w))).json(); } catch (e) { return; }
    const o = document.getElementById('login-out'), atBottom = o.scrollHeight - o.scrollTop - o.clientHeight < 30;
    o.textContent = r.output || '(čakám na výstup…)'; if (atBottom) o.scrollTop = o.scrollHeight;
    const u = document.getElementById('login-url');
    u.innerHTML = r.url ? 'Otvor a prihlás sa <b>účtom pre ' + esc(w) + '</b>: <a href="' + esc(r.url) + '" target="_blank" rel="noopener">' + esc(r.url.slice(0, 60)) + '…</a>' : '';
  }, 1500);
}
async function closeLogin(check) {
  clearInterval(loginTimer);
  const st = document.getElementById('login-url');
  if (check) st.textContent = 'overujem prihlásenie…';
  let r = {}; try { r = await (await fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({worker: loginW, action: 'stop'})})).json(); } catch (e) {}
  if (check) { alert(r.logged_in ? loginW + ' je prihlásený a vidí svoju kvótu. Zaškrtni ho v projekte a ulož.' : loginW + ' zatiaľ nie je prihlásený — skús to znova.'); }
  document.getElementById('login').classList.remove('on');
  loadSettings(true);
}

function pills() {
  if (!F) return '<span class="pill bad">foreman: ' + esc(window._ferr || 'načítavam') + '</span>';
  const p = [];
  const staleFor = foremanFetchedAt ? (Date.now()/1000 - foremanFetchedAt) : 0;
  if (staleFor > 30) {
    p.push('<span class="pill bad">dashboard nemá čerstvé dáta (' + Math.round(staleFor/60) + ' min staré) - nedôveruj stavu nižšie' +
           (window._ferr ? ' · príčina: ' + esc(window._ferr) : '') + '</span>');
    return p.join(' ');
  }
  if (!F.alive) p.push('<span class="pill bad"><span class="dot bad"></span>foreman nebeží</span>');
  else if (F.killswitch) {
    p.push('<span class="pill bad">kill switch ZAP — ' + esc(F.killswitch.reason || '') + '</span>');
    p.push((F.killswitch.scope === 'all' ? '<span class="pill bad">všetky projekty</span>' : '') +
           (F.killswitch.hard ? '<span class="pill bad">natvrdo — workeri zastavení</span>' : '') +
           '<button class="resume-btn" onclick="resumeDev()" id="resume-btn">Pokračovať</button>');
  }
  else if (F.paused) p.push('<span class="pill warn">foreman pozastavený</span>');
  else p.push('<span class="pill ok"><span class="dot running"></span>foreman beží</span>');
  if (F.claude_until) p.push('<span class="pill warn">orchestrátor: Claude cez Antigravity (Claude Code späť ' + tfmt(F.claude_until) + ')</span>');
  else p.push('<span class="pill ok">orchestrátor: Claude Code</span>');
  const agyDown = Object.keys(F.agy_cooldown || {}).length;
  if (agyDown) p.push('<span class="pill warn">' + agyDown + ' worker(y) dočasne bez náhradného orchestrátora</span>');
  p.push('<span class="pill">autopilot ' + (F.autopilot ? 'zap' : 'vyp') + '</span>');
  if (F.old) p.push('<span class="pill warn">stará verzia foremana — menej detailov</span>');
  return p.join(' ');
}

function stats() {
  if (!F) return '';
  const t = F.today || {}, l = F.llm_today || {}, ts = F.tasks || [];
  const n = s => ts.filter(x => x.status === s).length;
  const box = (v, lab) => '<div class="stat"><b>' + v + '</b><span>' + lab + '</span></div>';
  const blocked = n('blocked');
  return box(n('running'), 'beží') + box(n('pending'), 'čaká') + box(n('gate') + n('review'), 'kontrola') +
         box(t.merged != null ? t.merged : n('done'), 'hotovo dnes') +
         (blocked ? box('<span style="color:var(--bad)">' + blocked + '</span>', 'zaseknuté') : '') +
         (t.tokens ? box((t.tokens/1e6).toFixed(1) + 'M', 'tokeny dnes') : '') +
         (l.cost_usd ? box('$' + l.cost_usd.toFixed(2), 'orchestrátor dnes') : '');
}

function team() {
  const R = P.roster || {}, ts = (F && F.tasks) || [], live = (F && F.live) || {};
  const steps = (live.steps || []).map(s => {
    const [kind, id] = s.key.split(':');
    const t = ts.find(x => x.id === id), g = F && (F.goals || []).find(x => x.id === id);
    const what = s.phase || (kind === 'goal' ? 'plánovanie' : kind === 'task' ? 'kontrola' : kind);
    const obj = t ? esc(t.id) : g ? '„' + esc(g.text.slice(0, 60)) + '“' : '';
    return '<div class="step"><span class="dot think"></span>' + esc(what) + (obj ? ' — ' + obj : '') + ' <span class="muted">(' + ago(s.since) + ')</span></div>';
  }).join('');
  let orch = '<div class="node orch"><div class="node-title"><span class="dot ' + (steps ? 'think' : '') + '"></span>Orchestrátor (šéf)</div>' +
    '<div class="node-sub">' + (F && F.claude_until ? 'Claude cez Antigravity (náhradník)' : 'Claude Code Sonnet') + ' · plánuje, kontroluje, merguje</div>' +
    (steps || '<div class="node-sub">nič nerieši — čaká na workerov</div>') +
    (live.unavailable ? '<div class="step" style="color:var(--warn)">čaká: ' + esc(live.unavailable) + '</div>' : '') + '</div>';
  const names = new Set(Object.keys(R).filter(k => k.startsWith('worker:')).map(k => k.slice(7)).filter(inPool));
  ts.forEach(t => t.worker && inPool(t.worker) && names.add(t.worker));
  const ws = [...names].sort().map(w => {
    const e = R['worker:' + w] || {}, t = ts.find(x => x.worker === w && x.status === 'running');
    const cool = live.cooldown && live.cooldown[w];
    const state = t ? 'running' : '';
    const q5h = (e.quota || []).filter(q => /five hour/i.test(q.label));
    const shortModel = m => /claude/i.test(m) ? 'Claude' : /gemini/i.test(m) ? 'Gemini' : m;
    const quota = q5h.length ? '<div class="node-quota">' + q5h.map(q => shortModel(q.model) + ' ' + q.pct.toFixed(0) + '%').join(' · ') + '</div>' : '';
    const task = t ? '<div class="node-task" data-task="' + esc(t.id) + '" style="cursor:pointer"><span class="role ' + roleCls(t.role) + '">' + esc(t.role) + '</span> ' +
      esc(t.title) + '</div><div class="node-sub">' + esc(t.id) + ' · pokus ' + (t.attempts + 1) + '/' + (t.max_attempts || 4) + ' · beží ' + ago(t.dispatched) +
      (e.tool_calls ? ' · ' + e.tool_calls + ' nástrojov' : '') + '</div>'
      : '<div class="node-sub">' + (cool ? 'pauza do ' + tfmt(cool.until) + ': ' + esc(cool.reason || 'neznámy dôvod') : (live.reserved || []).includes(w) ? 'požičaný orchestrátorovi (Claude cez Antigravity)' : 'voľný') + '</div>';
    return '<div class="node"><div class="node-title"><span class="dot ' + state + '"></span>' + esc(w) + '</div>' + task + quota + '</div>';
  }).join('');
  return orch + (ws ? '<div class="branch"></div><div class="workers">' + ws + '</div>' : '');
}

function goalCard(g, ts) {
  const col = {done:'var(--ok)', gate:'var(--accent)', review:'var(--accent)', running:'var(--tool)', blocked:'var(--bad)'};
  const mine = ts.filter(t => t.goal === g.id && t.status !== 'cancelled'), n = mine.length || 1;
  const seg = ['done','gate','review','running','blocked'].map(s => { const k = mine.filter(t => t.status === s).length;
    return k ? '<i style="width:' + (100*k/n) + '%;background:' + col[s] + '"></i>' : ''; }).join('');
  const done = mine.filter(t => t.status === 'done').length;
  const lines = mine.reduce((a, t) => a + (t.status === 'done' && t.diff ? t.diff.added + t.diff.removed : 0), 0);
  const st = {new:'plánuje sa', active:'beží', done:'hotový', blocked:'zaseknutý'}[g.status] || g.status;
  // acceptance criteria of the whole goal, checked by the orchestrator when all its tasks finished (B4)
  const acc = g.acceptance || [], cr = g.criteria || [];
  const crit = acc.length ? '<div class="crit">' + acc.map((a, i) => {
      const c = cr[i] || cr.find(x => x.criterion && x.criterion.slice(0, 40) === String(a).slice(0, 40));
      const mark = !c ? '<span class="cm wait" title="ešte nekontrolované">○</span>' : c.met ? '<span class="cm ok">✓</span>' : '<span class="cm no">✗</span>';
      return '<div class="critrow">' + mark + '<span>' + esc(a) + (c && c.evidence ? '<span class="muted"> — ' + esc(c.evidence) + '</span>' : '') + '</span></div>';
    }).join('') + (g.criteria_at ? '<div class="muted" style="font-size:11.5px">kontrola: ' + esc(dfmt(g.criteria_at)) + '</div>' : '') + '</div>' : '';
  const metCount = cr.filter(c => c.met).length;
  return '<div class="goal"><div class="goal-head"><div class="goal-text">' + esc(g.text) + '</div><div class="goal-meta">' + esc(st) +
    (mine.length ? ' · úlohy ' + done + '/' + mine.length + ' (' + Math.round(100 * done / n) + ' %)' : '') +
    (acc.length ? ' · kritériá ' + metCount + '/' + acc.length : '') + (g.rounds > 1 ? ' · kolo ' + g.rounds : '') +
    (lines ? ' · ' + lines + ' riadkov' : '') + (g.source && g.source !== 'owner' ? ' · ' + esc(g.source) : '') + '</div></div>' +
    '<div class="bar">' + seg + '</div>' + ((g.summary || g.notes) ? '<div class="goal-sum">' + esc(g.summary || g.notes) + '</div>' : '') + crit +
    '<div class="chips">' + mine.map(t => '<span class="chip" data-task="' + esc(t.id) + '">' + esc(STATUS[t.status] || t.status) + ' · ' + esc(t.id) +
      (t.diff ? ' · +' + t.diff.added + '/-' + t.diff.removed : '') + '</span>').join('') + '</div>' +
    (mine.length > 1 ? '<button class="linkbtn" data-dag="' + esc(g.id) + '">' + (openDags.has(g.id) ? 'skryť graf závislostí' : 'graf závislostí') + '</button>' +
      (openDags.has(g.id) ? dagSvg(mine, ts) : '') : '') + '</div>';
}
function goalsHtml() {
  if (!F) return '';
  const ts = F.tasks || [];
  const gs = (F.goals || []).filter(g => g.status !== 'cancelled').slice().reverse();
  if (!gs.length) return '<div class="empty">Žiadne ciele — zadaj prvý hore v „Nový cieľ“.</div>';
  const open = gs.filter(g => g.status === 'new' || g.status === 'active' || g.status === 'blocked'), fin = gs.filter(g => !open.includes(g));
  return (open.map(g => goalCard(g, ts)).join('') || '<div class="empty">Nič nebeží.</div>') +
    (fin.length ? '<details' + (openFin ? ' open' : '') + ' id="fin-goals"><summary class="muted">Hotové ciele (' + fin.length + ')</summary>' +
      fin.slice(0, 30).map(g => goalCard(g, ts)).join('') + '</details>' : '');
}
let openFin = false;
document.addEventListener('toggle', e => { if (e.target.id === 'fin-goals') openFin = e.target.open; }, true);

function boardHtml() {
  if (!F) return '';
  const ts = F.tasks || [];
  const cols = [['pending', 'Čaká'], ['running', 'Beží'], ['gate', 'Kontrola (testy + review)'], ['done', 'Hotovo'], ['blocked', 'Zaseknuté / zrušené']];
  return cols.map(([s, lab]) => {
    let items = ts.filter(t => t.status === s || (s === 'gate' && t.status === 'review') || (s === 'blocked' && t.status === 'cancelled'));
    if (s === 'done') items = items.slice().sort((a, b) => (b.finished || '').localeCompare(a.finished || '')).slice(0, 15);
    const phase = id => { const st = ((F.live || {}).steps || []).find(x => x.key === 'task:' + id); return st ? st.phase : ''; };
    return '<div class="col"><h4>' + lab + '<span class="muted">' + items.length + '</span></h4>' + (items.map(t => {
      const deps = (t.depends_on || []).filter(d => !ts.find(x => x.id === d && x.status === 'done'));
      return '<div class="card s-' + t.status + '" data-task="' + esc(t.id) + '"><div class="id">' + esc(t.id) + '</div><div class="t">' + esc(t.title) + '</div><div class="m">' +
        '<span class="role ' + roleCls(t.role) + '">' + esc(t.role) + '</span>' + (t.worker && s !== 'pending' ? '<span>' + esc(t.worker) + '</span>' : '') +
        (t.attempts ? '<span style="color:var(--warn)">pokus ' + (t.attempts + 1) + '/' + (t.max_attempts || 4) + '</span>' : '') +
        (t.pr ? '<span>PR #' + t.pr + '</span>' : '') + (deps.length && s === 'pending' ? '<span>čaká na ' + deps.map(esc).join(', ') + '</span>' : '') +
        (t.diff ? '<span style="color:var(--ok)">+' + t.diff.added + '</span><span style="color:var(--bad)">-' + t.diff.removed + '</span>' : '') +
        (phase(t.id) ? '<span style="color:var(--accent)">' + esc(phase(t.id)) + '</span>' : '') + '</div></div>';
    }).join('') || '<div class="empty">—</div>') + '</div>';
  }).join('');
}

function renderDetail() {
  const d = document.getElementById('detail');
  const t = F && (F.tasks || []).find(x => x.id === openTask);
  if (!t) { d.classList.remove('on'); lastDetailHtml = ''; return; }
  const g = (F.goals || []).find(x => x.id === t.goal);
  const sheet = document.getElementById('sheet'), keep = sheet.scrollTop;
  const hist = (t.history || []).map(h => '<div class="hist"><b>' + esc(dfmt(h.at)) + ' · ' + esc(h.outcome) + '</b>' +
    (h.reviewer ? ' (' + esc(h.reviewer) + ')' : '') + '<br>' + esc(h.feedback || h.why || h.what || '') + '</div>').join('');
  const html = '<button class="close">Zavrieť</button><h2>' + esc(t.title) + '</h2>' +
    '<div class="muted" style="font-size:12.5px">' + esc(t.id) + ' · <span class="role ' + roleCls(t.role) + '">' + esc(t.role) + '</span> · ' +
    esc(STATUS[t.status] || t.status) + ' · pokus ' + (t.attempts + 1) + '/' + (t.max_attempts || 4) + (t.worker ? ' · ' + esc(t.worker) : '') +
    (t.pr ? ' · PR #' + t.pr : '') + (t.risk ? ' · riziko: ' + esc(t.risk) : '') + (t.run_tokens ? ' · ' + (t.run_tokens/1000).toFixed(0) + 'k tokenov' : '') +
    (t.diff ? ' · <span style="color:var(--ok)">+' + t.diff.added + '</span> <span style="color:var(--bad)">-' + t.diff.removed + '</span> riadkov' : '') +
    (t.total_lines != null ? ' · súbor(y) spolu ' + t.total_lines + ' riadkov' : '') + '</div>' +
    (g ? '<h3>Cieľ</h3><div style="font-size:13px">' + esc(g.text) + '</div>' : '') +
    (t.blocked_why ? '<h3 style="color:var(--bad)">Prečo zaseknuté</h3><pre>' + esc(t.blocked_why) + '</pre>' : '') +
    (t.feedback && t.status !== 'done' ? '<h3>Posledná spätná väzba workerovi</h3><pre>' + esc(t.feedback) + '</pre>' : '') +
    (hist ? '<h3>História</h3>' + hist : '') +
    ((t.acceptance || []).length ? '<h3>Akceptačné kritériá</h3><ul style="font-size:13px;margin:0;padding-left:18px">' + t.acceptance.map(a => '<li>' + esc(a) + '</li>').join('') + '</ul>' : '') +
    ((t.files || []).length ? '<h3>Súbory</h3><div class="muted" style="font-size:12.5px;font-family:ui-monospace,monospace">' + t.files.map(esc).join('<br>') + '</div>' : '') +
    ((t.mutations || []).length ? '<h3>Mutačné testy</h3><pre>' + esc(t.mutations.join('\n')) + '</pre>' : '') +
    (t.diff || t.pr ? '<h3>Zmeny (diff)</h3>' + (diffCache[t.id] == null
        ? '<button class="linkbtn" data-diff="' + esc(t.id) + '">' + (diffLoading.has(t.id) ? 'načítavam…' : 'zobraziť diff') + '</button>'
        : diffHtml(diffCache[t.id])) : '') +
    (t.spec ? '<h3>Zadanie</h3><pre>' + esc(t.spec) + '</pre>' : '');
  if (html === lastDetailHtml && d.classList.contains('on')) return;
  lastDetailHtml = html;
  sheet.innerHTML = html;
  d.classList.add('on');
  sheet.scrollTop = keep;
}

function eventsHtml() {
  if (!F) return '';
  const ev = (F.events || []).slice().reverse();
  return ev.map(e => { const m = e.msg || '';
    const cls = /BLOCKED|refused|error|failed|unavailable|kill switch/i.test(m) ? 'bad' : /done|DONE|merged|planned/.test(m) ? 'good' : '';
    return '<div class="ev ' + cls + '"><time>' + esc(dfmt(e.at)) + '</time><div>' + esc(m) + '</div></div>'; }).join('') || '<div class="empty">Zatiaľ nič.</div>';
}

const paneEls = {};
function liveHtml() {
  const grid = document.getElementById('grid');
  const panes = P.panes || [];
  document.getElementById('c-live').textContent = panes.length;
  if (tab !== 'live') return;
  if (!panes.length) { setHTML('grid', '<div class="empty">Žiadne AI momentálne nebežia — prepis sa zjaví, keď niečo pobeží.</div>'); for (const k in paneEls) delete paneEls[k]; return; }
  if (grid.querySelector('.empty')) { grid.innerHTML = ''; lastHTML['grid'] = null; }
  const ts = (F && F.tasks) || [];
  const seen = new Set();
  for (const p of panes) {
    if (p.id.startsWith('worker:') ? !inPool(p.id.slice(7)) : project !== 'default') continue;
    seen.add(p.id);
    let pe = paneEls[p.id];
    if (!pe) {
      const el = document.createElement('div'); el.className = 'pane';
      const h = document.createElement('h2'); const log = document.createElement('div'); log.className = 'log';
      el.appendChild(h); el.appendChild(log); grid.appendChild(el);
      pe = paneEls[p.id] = {el, h, log, n: 0, first: null, last: null, head: ''};
    }
    const w = p.id.startsWith('worker:') ? p.id.slice(7) : null, t = w && ts.find(x => x.worker === w && x.status === 'running');
    const head = '<span class="dot ' + (p.state === 'running' ? 'running' : '') + '"></span>' + esc(p.title) +
      (t ? ' <span class="role ' + roleCls(t.role) + '">' + esc(t.role) + '</span> <span class="muted" style="font-weight:400;font-size:13px">' + esc(t.id) + '</span>' :
           ' <span class="muted" style="font-weight:400;font-size:13px">(' + esc(p.state) + ')</span>');
    if (head !== pe.head) { pe.h.innerHTML = head; pe.head = head; }
    const L = p.lines, firstTxt = L.length ? L[0].text : null, lastTxt = L.length ? L[L.length - 1].text : null;
    if (L.length === pe.n && firstTxt === pe.first && lastTxt === pe.last) continue;
    const atBottom = pe.log.scrollHeight - pe.log.scrollTop - pe.log.clientHeight < 40;
    const line = l => '<div class="line l-' + l.kind + '">' + esc(l.text) + '</div>';
    if (firstTxt === pe.first && L.length > pe.n) pe.log.insertAdjacentHTML('beforeend', L.slice(pe.n).map(line).join(''));
    else pe.log.innerHTML = L.map(line).join('');
    pe.n = L.length; pe.first = firstTxt; pe.last = lastTxt;
    if (atBottom) pe.log.scrollTop = pe.log.scrollHeight;
  }
  for (const id in paneEls) if (!seen.has(id)) { paneEls[id].el.remove(); delete paneEls[id]; }
}

// ---- only touch the DOM when a section's HTML actually changed (no flicker, no lost scroll, less battery)
const lastHTML = {};
function setHTML(id, html, force) {
  if (!force && lastHTML[id] === html) return;
  lastHTML[id] = html;
  document.getElementById(id).innerHTML = html;
}
let lastDetailHtml = '';
const openDags = new Set(), diffCache = {}, diffLoading = new Set();

// ---- 1. timeline: one lane per worker, one bar per run, last 24 h
const RUNCOL = {ok: 'var(--ok)', running: 'var(--accent)', 'token-cap': 'var(--warn)', timeout: 'var(--warn)', failed: 'var(--bad)',
                'setup-failed': 'var(--bad)', killswitch: 'var(--idle)'};
function timelineHtml() {
  if (!F || !F.timeline) return '<div class="empty">Časová os potrebuje novšiu verziu foremana.</div>';
  const now = Date.now() / 1000, span = 24 * 3600, t0 = now - span, W = 1000, LW = 90, lane = 30;
  const runs = F.timeline;
  const ws = [...new Set(runs.map(r => r.worker).concat(Object.keys((P && P.roster) || {}).filter(k => k.startsWith('worker:')).map(k => k.slice(7))))].filter(Boolean).sort();
  if (!ws.length) return '<div class="empty">Za posledných 24 h žiadne behy.</div>';
  const x = t => LW + Math.max(0, Math.min(1, (t - t0) / span)) * (W - LW);
  let svg = '<svg class="tl" viewBox="0 0 ' + W + ' ' + (ws.length * lane + 24) + '" preserveAspectRatio="none">';
  for (let h = 0; h <= 24; h += 3) {
    const tx = x(t0 + h * 3600), d = new Date((t0 + h * 3600) * 1000);
    svg += '<line x1="' + tx + '" x2="' + tx + '" y1="0" y2="' + ws.length * lane + '" stroke="var(--line)"/>' +
           '<text x="' + tx + '" y="' + (ws.length * lane + 16) + '" text-anchor="middle">' + d.getHours() + ':00</text>';
  }
  const util = {};
  ws.forEach((w, i) => {
    const y = i * lane;
    svg += '<text class="lane" x="4" y="' + (y + 19) + '">' + esc(w) + '</text>';
    let busy = 0;
    for (const r of runs.filter(r => r.worker === w)) {
      const s0 = Math.max(r.start, t0), e0 = Math.min(r.end || now, now);
      if (e0 <= s0) continue;
      busy += e0 - s0;
      const tip = esc(r.task) + ' · ' + esc(r.reason) + ' · ' + Math.round((e0 - s0) / 60) + ' min · ' + Math.round((r.tokens || 0) / 1000) + 'k tokenov';
      svg += '<g data-task="' + esc(r.task.replace(/\.md$/, '')) + '"><title>' + tip + '</title><rect class="run" x="' + x(s0) + '" y="' + (y + 5) + '" width="' +
             Math.max(2, x(e0) - x(s0)) + '" height="' + (lane - 10) + '" rx="3" fill="' + (RUNCOL[r.reason] || 'var(--idle)') + '"' +
             (r.reason === 'running' ? ' opacity=".8"><animate attributeName="opacity" values=".5;1;.5" dur="2s" repeatCount="indefinite"/></rect>' : '/>') + '</g>';
    }
    util[w] = Math.round(100 * busy / span);
  });
  svg += '</svg>';
  const legend = '<div class="legend">' + [['ok','hotové'],['running','beží'],['token-cap','strop tokenov / čas'],['failed','zlyhalo'],['killswitch','kill switch']]
    .map(([k, l]) => '<span><i style="background:' + RUNCOL[k] + '"></i>' + l + '</span>').join('') + '</div>';
  const u = '<div class="legend">' + ws.map(w => '<span>' + esc(w) + ': <b style="color:var(--text)">' + util[w] + ' %</b> času pracoval</span>').join('') + '</div>';
  return '<h3>Posledných 24 hodín</h3>' + legend + '<div class="tablewrap" style="padding:8px">' + svg + '</div>' + u +
         '<div class="muted" style="font-size:12px;margin-top:6px">Medzery v pruhu = worker stál. Klik na blok otvorí úlohu.</div>';
}

// ---- 3. dependency graph of a goal
const NODECOL = {done: 'var(--ok)', running: 'var(--tool)', gate: 'var(--accent)', review: 'var(--accent)', blocked: 'var(--bad)', pending: 'var(--idle)', cancelled: 'var(--idle)'};
function dagSvg(mine) {
  const ids = new Set(mine.map(t => t.id)), depth = {};
  const d = (t, seen) => { if (depth[t.id] != null) return depth[t.id]; if (seen.has(t.id)) return 0; seen.add(t.id);
    const ps = (t.depends_on || []).filter(x => ids.has(x)).map(x => d(mine.find(m => m.id === x), seen));
    return depth[t.id] = ps.length ? 1 + Math.max(...ps) : 0; };
  mine.forEach(t => d(t, new Set()));
  const cols = {}; mine.forEach(t => (cols[depth[t.id]] = cols[depth[t.id]] || []).push(t));
  const nc = Object.keys(cols).length, maxRows = Math.max(...Object.values(cols).map(c => c.length));
  const BW = 170, BH = 34, GX = 50, GY = 12, W = nc * (BW + GX), H = maxRows * (BH + GY);
  const pos = {};
  Object.entries(cols).forEach(([c, ts]) => ts.forEach((t, r) => pos[t.id] = {x: c * (BW + GX), y: r * (BH + GY)}));
  let svg = '<svg class="dag" viewBox="0 0 ' + W + ' ' + H + '" style="max-width:' + W + 'px">';
  mine.forEach(t => (t.depends_on || []).filter(x => pos[x]).forEach(x => {
    const a = pos[x], b = pos[t.id];
    svg += '<path d="M' + (a.x + BW) + ',' + (a.y + BH / 2) + ' C' + (a.x + BW + GX / 2) + ',' + (a.y + BH / 2) + ' ' + (b.x - GX / 2) + ',' + (b.y + BH / 2) + ' ' + b.x + ',' + (b.y + BH / 2) +
           '" fill="none" stroke="var(--muted)" stroke-width="1.5"/>';
  }));
  mine.forEach(t => { const p = pos[t.id];
    svg += '<g data-task="' + esc(t.id) + '"><title>' + esc(t.title) + ' · ' + esc(t.role) + '</title><rect x="' + p.x + '" y="' + p.y + '" width="' + BW + '" height="' + BH + '" rx="6" fill="var(--card2)" stroke="' +
           (NODECOL[t.status] || 'var(--idle)') + '" stroke-width="2"/><text x="' + (p.x + 8) + '" y="' + (p.y + 14) + '">' + esc(t.id.slice(0, 24)) + '</text><text x="' + (p.x + 8) + '" y="' + (p.y + 27) +
           '" style="fill:var(--muted)">' + esc((STATUS[t.status] || t.status) + ' · ' + t.role).slice(0, 26) + '</text></g>'; });
  return svg + '</svg>';
}

// ---- 4. diff of a task (what the reviewer saw, or the merged PR)
async function loadDiff(id) {
  diffLoading.add(id); renderDetail();
  try { diffCache[id] = await (await fetch('/api/diff?task=' + encodeURIComponent(id) + '&project=' + encodeURIComponent(project))).text(); }
  catch (e) { diffCache[id] = '(nepodarilo sa načítať: ' + e + ')'; }
  diffLoading.delete(id); renderDetail();
}
function diffHtml(text) {
  const lines = text.split('\n').slice(0, 4000);
  return '<div class="diff">' + lines.map(l => {
    const c = l.startsWith('diff --git') || l.startsWith('+++') || l.startsWith('---') ? 'h' : l.startsWith('@@') ? 'at' : l.startsWith('+') ? 'a' : l.startsWith('-') ? 'r' : '';
    return '<div' + (c ? ' class="' + c + '"' : '') + '>' + (esc(l) || ' ') + '</div>'; }).join('') + '</div>';
}

// ---- 8. team metrics
function metricsHtml() {
  const M = F && F.metrics;
  if (!M) return '<div class="empty">Metriky potrebujú novšiu verziu foremana.</div>';
  const T = M.totals || {}, box = (v, lab) => '<div class="stat"><b>' + (v == null ? '–' : v) + '</b><span>' + lab + '</span></div>';
  let h = '<div class="stats">' + box(T.done, 'hotových úloh') + box(T.first_pass_pct == null ? null : T.first_pass_pct + ' %', 'schválené na 1. pokus') +
          box(T.reworks, 'vrátení na opravu') + box(T.blocked, 'zaseknutých') + box(T.tokens_per_done ? Math.round(T.tokens_per_done / 1000) + 'k' : null, 'tokenov na hotovú úlohu') + '</div>';
  const days = M.merged_per_day || [], mx = Math.max(1, ...days.map(d => d[1]));
  h += '<h3>Hotové úlohy za deň</h3><div class="bars">' + (days.map(([d, n]) => '<div><span>' + n + '</span><b style="height:' + (100 * n / mx) + '%"></b><span>' + d.slice(5) + '</span></div>').join('') || '<span class="muted">zatiaľ nič</span>') + '</div>';
  const roles = Object.entries(M.roles || {}).sort((a, b) => b[1].tasks - a[1].tasks);
  h += '<h3>Podľa roly</h3><div class="tablewrap"><table class="m"><tr><th>rola</th><th class="num">úloh</th><th class="num">hotovo</th><th class="num">1. pokus</th><th class="num">vrátení</th><th class="num">zaseknuté</th><th class="num">tokeny/úloha</th><th class="num">riadky</th></tr>' +
       roles.map(([r, v]) => '<tr><td>' + esc(r) + '</td><td class="num">' + v.tasks + '</td><td class="num">' + v.done + '</td><td class="num">' + (v.first_pass_pct == null ? '–' : v.first_pass_pct + ' %') +
         '</td><td class="num">' + v.reworks + '</td><td class="num">' + v.blocked + '</td><td class="num">' + (v.tokens_per_done ? Math.round(v.tokens_per_done / 1000) + 'k' : '–') + '</td><td class="num">' + v.lines_done + '</td></tr>').join('') + '</table></div>';
  const E = M.efficiency || {};
  const effTable = (title, rows, note) => rows.length ? '<h3>' + title + '</h3>' + (note ? '<div class="muted" style="font-size:12.5px">' + note + '</div>' : '') +
    '<div class="tablewrap"><table class="m"><tr><th></th><th class="num">behov</th><th class="num">ok</th><th class="num">strop</th><th class="num">kvóta/beh</th><th class="num">kvóta/hotová úloha</th>' +
    '<th class="num">nástroje/beh</th><th class="num">tokeny/beh</th><th class="num">hotovo</th><th class="num">1. pokus</th></tr>' +
    rows.map(([k, v]) => '<tr><td>' + esc(k) + '</td><td class="num">' + v.runs + '</td><td class="num">' + v.ok_pct + ' %</td><td class="num">' + v.cap_pct + ' %</td><td class="num">' +
      (v.week_per_run == null ? '–' : v.week_per_run + ' %') + '</td><td class="num"><b>' + (v.week_per_done == null ? '–' : v.week_per_done + ' %') + '</b></td><td class="num">' +
      (v.tools_median == null ? '–' : v.tools_median) + '</td><td class="num">' + (v.tokens_median ? Math.round(v.tokens_median / 1000) + 'k' : '–') + '</td><td class="num">' + v.done +
      '</td><td class="num">' + (v.first_pass_pct == null ? '–' : v.first_pass_pct + ' %') + '</td></tr>').join('') + '</table></div>' : '';
  h += effTable('Efektivita podľa modelu workerov', Object.entries(E.model || {}).sort((a, b) => b[1].runs - a[1].runs),
                'kvóta = % týždenného limitu jedného účtu (agy /usage pred a po behu); nižšie je lepšie');
  h += effTable('Balík kontextu — A/B', Object.entries(E.pack || {}).sort((a, b) => b[1].runs - a[1].runs),
                'pri CONTEXT_PACK=ab dostane balík polovica úloh; porovnaj kvótu na hotovú úlohu a počet nástrojov na beh');
  const SC = E.self_check;
  if (SC && SC.runs) h += '<h3>Kontrola testov v behu</h3><div class="stats">' + box(SC.runs, 'behov s kontrolou') + box(SC.green_first, 'zelené hneď') +
    box(SC.fixed, 'opravené v tom istom behu') + box(SC.still_red, 'ostali červené') + '</div>';
  const MT = F.maintenance;
  if (MT) h += '<h3>Údržba main (bez AI)</h3><div class="muted maint" style="font-size:13px">' + esc(dfmt(MT.at)) + ' · ' + esc(MT.sha || '') + ' · testy: ' +
    (MT.suite || []).map(r => (r.rc === 0 ? '✓ ' : '✗ ') + esc(r.summary)).join(' / ') + (MT.nodoc ? ' · modulov bez docstringu: ' + MT.nodoc : '') + '</div>';
  const prov = Object.entries(M.providers || {}).sort((a, b) => b[1].calls - a[1].calls);
  h += '<h3>Orchestrátor za 7 dní (kto plánoval a robil review)</h3><div class="tablewrap"><table class="m"><tr><th>poskytovateľ</th><th class="num">volaní</th><th class="num">zlyhalo</th><th class="num">priemer</th></tr>' +
       prov.map(([p, v]) => '<tr><td>' + esc(p) + '</td><td class="num">' + v.calls + '</td><td class="num">' + v.failed + '</td><td class="num">' + (v.calls ? Math.round(v.secs / v.calls) + ' s' : '–') + '</td></tr>').join('') + '</table></div>';
  return h;
}

// ---- 10. ask the team
async function askTeam() {
  const q = document.getElementById('ask-q').value.trim(), btn = document.getElementById('ask-btn'), out = document.getElementById('ask-a');
  if (!q) return;
  btn.disabled = true; btn.textContent = 'Orchestrátor premýšľa… (aj 1–2 min)';
  let r;
  try { r = await (await fetch('/api/ask', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({question: q, project})})).json(); }
  catch (e) { r = {answer: '(chyba spojenia: ' + e + ')'}; }
  btn.disabled = false; btn.textContent = 'Spýtať sa';
  out.innerHTML = '<div class="answer"><b>' + esc(q) + '</b>\n\n' + esc(r.answer || r.error || '(bez odpovede)') + '</div>' + out.innerHTML;
}

function render(force) {
  setHTML('pills', pills(), force);
  setHTML('stats', stats(), force);
  const ts = (F && F.tasks) || [];
  document.getElementById('c-queue').textContent = ts.filter(t => ['pending', 'running', 'gate', 'review'].includes(t.status)).length;
  if (tab === 'overview') {
    setHTML('team', team(), force);
    setHTML('goals', goalsHtml(), force);
    const bl = (F && F.backlog) || [];
    setHTML('backlog', bl.length ? bl.map((b, i) => '<div class="goal blrow"><span class="muted">' + (i + 1) + '.</span> <span class="bltext">' + esc(b) +
        '</span><span class="blbtns">' + (i ? '<button class="mini" data-bl="move" data-n="' + (i + 1) + '" data-to="' + i + '" title="vyššie">↑</button>' : '') +
        (i < bl.length - 1 ? '<button class="mini" data-bl="move" data-n="' + (i + 1) + '" data-to="' + (i + 2) + '" title="nižšie">↓</button>' : '') +
        '<button class="mini" data-bl="drop" data-n="' + (i + 1) + '" title="zahodiť">✕</button></span></div>').join('')
      : '<div class="empty">Prázdny' + (F && F.autopilot ? ' — keď bude worker voľný, autopilot vyberie ďalší cieľ z vízie.' : '.') + '</div>', force);
    const qs = (F && F.owner_questions) || [];
    if (!document.getElementById('questions').contains(document.activeElement))
      setHTML('questions', qs.length ? '<h3>Otázky plánovača (' + qs.length + ')</h3>' + qs.map((q, i) => '<div class="goal qrow"><div class="qtext">' + esc(q) +
        '</div><div class="btnrow"><input type="text" class="qin" data-qn="' + (i + 1) + '" placeholder="tvoja odpoveď (plánovač sa ňou bude riadiť)">' +
        '<button class="mini" data-qa="' + (i + 1) + '">Odpovedať</button><button class="mini" data-qd="' + (i + 1) + '" title="otázka nie je dôležitá">Zahodiť</button></div></div>').join('') : '', force);
  } else if (tab === 'queue') setHTML('board', boardHtml(), force);
  else if (tab === 'events') setHTML('events', eventsHtml(), force);
  else if (tab === 'timeline') setHTML('timeline', timelineHtml(), force);
  else if (tab === 'metrics') setHTML('metrics', metricsHtml(), force);
  else if (tab === 'settings' && (force || !document.getElementById('settings').contains(document.activeElement))) setHTML('settings', settingsHtml(), force);
  liveHtml();
  if (openTask) renderDetail();
}

let foremanFetchedAt = 0;
let projAt = 0;
async function loadProjects() {
  if (Date.now() - projAt < 30000) return;
  projAt = Date.now();
  let ps = [];
  try { ps = (await (await fetch('/api/projects')).json()).projects || []; } catch (e) { return; }
  const sel = document.getElementById('proj');
  if (!ps.some(p => p.name === project)) project = (ps[0] || {name: 'default'}).name;
  PROJ_LIST = ps;
  const html = ps.map(p => '<option value="' + esc(p.name) + '"' + (p.name === project ? ' selected' : '') + '>' + esc(p.name) +
                     (p.repo ? ' · ' + esc(p.repo) : '') + '</option>').join('') + '<option value="__new">+ Nový projekt…</option>';
  if (sel.innerHTML !== html) sel.innerHTML = html;
  sel.style.display = '';
}
let PROJ_LIST = [];

// ---- a new project from the dashboard (bin/foreman init-project + forge-bootstrap + its foreman service)
async function openNewProject() {
  const m = document.getElementById('newproj'); m.classList.add('on');
  document.getElementById('np-form').style.display = ''; document.getElementById('np-log').style.display = 'none';
  const box = document.getElementById('np-workers');
  let st = {}; try { st = await (await fetch('/api/settings?project=default')).json(); } catch (e) {}
  const owner = {};
  for (const p of PROJ_LIST) for (const w of String(p.workers || '').split(',').filter(Boolean)) owner[w] = p.name;
  const ws = st.workers || [];
  box.innerHTML = ws.length ? ws.map(w => '<label class="chk"><input type="checkbox" data-npw="' + esc(w) + '"> ' + esc(w) +
      ' <span class="muted">(' + esc(owner[w] || (st.values && !st.values.WORKERS ? 'default — všetci' : 'voľný')) + ')</span></label>').join(' ')
    : 'žiadni workeri — pridaj ich v Nastaveniach';
}
async function createProject() {
  const v = id => document.getElementById(id).value.trim();
  const body = {action: 'create', name: v('np-name'), repo: v('np-repo'), lang: v('np-lang'), vision: document.getElementById('np-vision').value,
                workers: [...document.querySelectorAll('[data-npw]')].filter(c => c.checked).map(c => c.dataset.npw)};
  for (const k of ['name', 'repo', 'lang', 'workers']) document.getElementById('np-e-' + k).textContent = '';
  let j = await (await fetch('/api/projects', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})).json();
  if (j.errors) { for (const k in j.errors) { const el = document.getElementById('np-e-' + k); if (el) el.textContent = j.errors[k]; } return; }
  document.getElementById('np-form').style.display = 'none';
  const log = document.getElementById('np-log'); log.style.display = '';
  while (j.state === 'running') { log.textContent = (j.log || '') + '\n' + T('… pracujem'); await new Promise(r => setTimeout(r, 2500));
    j = await (await fetch('/api/jobs/' + encodeURIComponent(j.id))).json(); }
  log.textContent = (j.log || '') + '\n' + T({done: '✔ Projekt je pripravený — vyber ho hore a zadaj prvý goal.',
    'needs-sudo': '⚠ Projekt je vytvorený, len jeho služba potrebuje root — spusti príkaz vyššie.'}[j.state] || '✘ Zlyhalo — pozri výpis vyššie.');
  projAt = 0; await loadProjects();
  if (j.state !== 'failed') { project = body.name; try { localStorage.setItem('af-project', project); } catch (e) {} }
}
document.getElementById('proj').addEventListener('change', e => {
  if (e.target.value === '__new') { e.target.value = project; openNewProject(); return; }
  project = e.target.value;
  try { localStorage.setItem('af-project', project); } catch (err) {}
  F = null; openTask = null; lastDetailHtml = ''; document.getElementById('detail').classList.remove('on');
  for (const k in lastHTML) delete lastHTML[k];
  for (const k in diffCache) delete diffCache[k];
  for (const id in paneEls) { paneEls[id].el.remove(); delete paneEls[id]; }
  S = null; sLoadedFor = null; sErr = {}; sMsg = '';
  poll();
});
async function poll() {
  await loadProjects();
  try { const r = await (await fetch('/api/foreman?project=' + encodeURIComponent(project))).json(); F = r.foreman; window._ferr = r.error; foremanFetchedAt = r.fetched_at || 0; } catch (e) {}
  try { P = await (await fetch('/api/panes')).json(); } catch (e) {}
  render();
}
function showJsError(msg) {
  const el = document.getElementById('jserr');
  if (el) { el.style.display = 'block'; el.textContent = 'Chyba stránky: ' + msg; }
}
window.addEventListener('error', e => showJsError((e.message || 'neznáma') + ' (' + (e.lineno || '?') + ')'));
window.addEventListener('unhandledrejection', e => showJsError(String(e.reason && e.reason.message || e.reason)));
// translate the static page once, then everything rendered later as it appears
if (LANG !== 'sk') {
  document.documentElement.lang = 'en';
  translate(document.body);
  new MutationObserver(ms => { for (const m of ms) { if (m.type === 'characterData') translate(m.target);
    else m.addedNodes.forEach(translate); } }).observe(document.body, {childList: true, subtree: true, characterData: true});
}
const langBtn = document.getElementById('lang');
langBtn.textContent = LANG === 'sk' ? 'EN' : 'SK';
langBtn.addEventListener('click', () => { try { localStorage.setItem('af-lang', LANG === 'sk' ? 'en' : 'sk'); } catch (e) {} location.reload(); });
setTab(tab);
let busyPoll = false;
async function tickPoll() { if (document.hidden || busyPoll) return; busyPoll = true; try { await poll(); } finally { busyPoll = false; } }
document.addEventListener('visibilitychange', () => { if (!document.hidden) tickPoll(); });
tickPoll(); setInterval(tickPoll, 2500);
</script>
</body></html>"""


# ---- access control. The dashboard can stop projects, change settings and drive a worker's login, so:
#  - Host must be this machine (127.0.0.1 / localhost), a Tailscale name (*.ts.net) or one listed in DASHBOARD_HOSTS - this
#    defeats DNS rebinding (a web page whose domain suddenly resolves to 127.0.0.1);
#  - every POST must carry the header X-Agent-Forge (the page's own fetch adds it). A custom header makes the browser ask
#    permission first (CORS preflight), which this server never grants - so no other web site can POST here (CSRF);
#  - the page cannot be framed by another site (clickjacking);
#  - optional DASHBOARD_TOKEN: open the page once as /?token=<token>; a SameSite=Strict, HttpOnly cookie keeps you signed in.
DASHBOARD_HOSTS = {h.strip().lower() for h in os.environ.get("DASHBOARD_HOSTS", "").split(",") if h.strip()}
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")
SEC_HEADERS = {"X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
               "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                                          "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}


def host_allowed(host):
    host = (host or "").strip().lower()
    name = host.split("]")[0] + "]" if host.startswith("[") else host.rsplit(":", 1)[0]
    return name in ("127.0.0.1", "localhost", "[::1]") or name.endswith(".ts.net") or name in DASHBOARD_HOSTS


def token_ok(cookie_header, query_token=""):
    if not DASHBOARD_TOKEN:
        return True
    import hmac
    if query_token and hmac.compare_digest(query_token, DASHBOARD_TOKEN):
        return True
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == "af_token" and hmac.compare_digest(v, DASHBOARD_TOKEN):
            return True
    return False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def end_headers(self):
        for k, v in SEC_HEADERS.items():
            self.send_header(k, v)
        if self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _deny(self, code, msg):
        raw = msg.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _allowed(self, post=False):
        import urllib.parse
        if not host_allowed(self.headers.get("Host")):
            self._deny(403, "host not allowed (set DASHBOARD_HOSTS to add your reverse proxy's name)"); return False
        qtok = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("token", [""])[0]
        if not token_ok(self.headers.get("Cookie"), qtok):
            self._deny(401, "sign in: open this page as /?token=<DASHBOARD_TOKEN>"); return False
        if post and self.headers.get("X-Agent-Forge") != "1":
            self._deny(403, "missing X-Agent-Forge header"); return False
        return True

    def _json(self, obj, code=200):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        try:
            n = min(int(self.headers.get("Content-Length", "0")), 50000)
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def do_POST(self):
        if not self._allowed(post=True):
            return
        body = self._body()
        proj = str(body.get("project", "default"))
        try:
            pf = pflag(proj)
        except ValueError:
            self._json({"error": "bad project"}, 400); return
        penv = f"FORGE_PROJECT={proj} " if pf else ""
        if self.path == "/api/ask":
            q = str(body.get("question", "")).strip()[:4000]
            if not q:
                self._json({"error": "empty question"}, 400); return
            if not ASK_LOCK.acquire(blocking=False):
                self._json({"error": "another question is being answered - wait for it"}, 429); return
            try:
                rc, out, err = ssh_run_full(f"cd {FORGE_DIR} && bin/foreman{pf} ask", timeout=900, inp=q)
            finally:
                ASK_LOCK.release()
            self._json({"answer": out.strip() or (err.strip()[-500:] if rc else "(empty answer)")})
        elif self.path in ("/api/killswitch", "/api/resume"):
            action = "off" if self.path == "/api/resume" else str(body.get("action"))
            scope = "--all " if body.get("all") else ""
            steps = []

            def run(label, cmd, timeout=40):
                rc, out, err = ssh_run_full(cmd, timeout=timeout)
                steps.append({"label": label, "ok": rc == 0, "output": (out or err or "").strip()[-600:]})
                return rc == 0
            if action == "on":
                ok = run("kill switch on" + (" (hard)" if body.get("hard") else ""),
                         f"cd {FORGE_DIR} && {penv}bin/killswitch {scope}on{' --hard' if body.get('hard') else ''} --reason dashboard", timeout=90)
            elif action == "off":
                run("acknowledge egress alerts", f"cd {FORGE_DIR} && bin/egress-report --ack", timeout=30)
                ok = run("kill switch off (+ start stopped containers)", f"cd {FORGE_DIR} && {penv}bin/killswitch {scope}off --start", timeout=120)
                svc = FOREMAN_SERVICE_PROJECT.format(project=proj) if pf else FOREMAN_SERVICE
                rc, active, _ = ssh_run_full(f"systemctl is-active {svc}", timeout=10)
                if active.strip() != "active":
                    run(f"restart {svc}", f"sudo systemctl restart {svc}", timeout=60)
            else:
                self._json({"error": "action must be on or off"}, 400); return
            self._json({"ok": ok, "steps": steps})
        elif self.path == "/api/settings":
            if body.get("refresh_models"):
                rc, out, err = ssh_run_full(f"cd {FORGE_DIR} && bin/foreman{pf} settings --refresh-models", timeout=200)
                try:
                    self._json(json.loads(out) if rc == 0 else {"error": err[-300:]})
                except ValueError:
                    self._json({"error": (err or out)[-300:]})
                return
            changes = body.get("changes") or {}
            if not isinstance(changes, dict) or not all(isinstance(k, str) and isinstance(v, (str, int)) for k, v in changes.items()):
                self._json({"error": "changes must be an object of strings"}, 400); return
            rc, out, err = ssh_run_full(f"cd {FORGE_DIR} && bin/foreman{pf} settings apply", timeout=60,
                                        inp=json.dumps({k: str(v) for k, v in changes.items()}))
            try:
                self._json(json.loads(out))
            except Exception:
                self._json({"ok": False, "errors": {"_": (err or out)[-300:]}})
        elif self.path == "/api/projects":
            if body.get("action") != "create":
                self._json({"error": "unknown action"}, 400); return
            name, repo = str(body.get("name", "")).strip(), str(body.get("repo", "")).strip()
            lang = str(body.get("lang", "English")).strip() or "English"
            ws = [str(w) for w in body.get("workers") or []]
            errs = {}
            if not PROJECT_RE.fullmatch(name) or name == "default":
                errs["name"] = "malé písmená, číslice a pomlčky (nie 'default')"
            elif any(p.get("name") == name for p in PROJECTS["list"]):
                errs["name"] = "projekt už existuje"
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}/[A-Za-z0-9_.-]{1,60}", repo):
                errs["repo"] = "organizácia/repozitár, napr. acme/shop"
            if not all(re.fullmatch(r"worker-\d+", w) for w in ws):
                errs["workers"] = "neplatný worker"
            if not re.fullmatch(r"[A-Za-z][A-Za-z ()-]{0,30}", lang):
                errs["lang"] = "názov jazyka, napr. Slovak"
            if errs:
                self._json({"ok": False, "errors": errs}); return
            vision = str(body.get("vision", ""))[:20000]
            self._json(start_job("create-project", lambda job: create_project_job(job, name, repo, ws, lang, vision)))
        elif self.path == "/api/goal":
            # the owner's text goes to bin/foreman on stdin, never into the command line
            text, mode = str(body.get("text", "")).strip()[:6000], str(body.get("mode", "now"))
            if not text:
                self._json({"error": "empty goal"}, 400); return
            cmd = {"now": "goal -", "backlog": "backlog add -", "roadmap": "project -"}.get(mode)
            if not cmd:
                self._json({"error": "mode must be now, backlog or roadmap"}, 400); return
            rc, out, err = ssh_run_full(f"cd {FORGE_DIR} && bin/foreman{pf} {cmd}", timeout=30, inp=text)
            self._json({"ok": rc == 0, "message": (out or err).strip()[-400:]})
        elif self.path == "/api/backlog":
            act, n, to = str(body.get("action", "")), str(body.get("n", "")), str(body.get("to", ""))
            if act not in ("drop", "move") or not n.isdigit() or (act == "move" and not to.isdigit()):
                self._json({"error": "action drop <n> | move <n> <to>"}, 400); return
            rc, out, err = ssh_run_full(f"cd {FORGE_DIR} && bin/foreman{pf} backlog {act} {int(n)}" + (f" {int(to)}" if act == "move" else ""),
                                        timeout=30)
            self._json({"ok": rc == 0, "message": (err or "").strip()[-300:]})
        elif self.path == "/api/answer":
            n = str(body.get("n", ""))
            if not n.isdigit():
                self._json({"error": "bad question number"}, 400); return
            rc, out, err = ssh_run_full(f"cd {FORGE_DIR} && bin/foreman{pf} answer {int(n)}", timeout=30,
                                        inp=str(body.get("answer", ""))[:4000])
            self._json({"ok": rc == 0, "message": (out or err).strip()[-300:]})
        elif self.path == "/api/workers":
            if body.get("action") != "add":
                self._json({"error": "unknown action"}, 400); return
            self._json(start_job("add-worker", add_worker_job))
        elif self.path == "/api/login":
            w, act = str(body.get("worker", "")), str(body.get("action", ""))
            if not re.fullmatch(r"worker-\d+", w):
                self._json({"error": "bad worker"}, 400); return
            if act == "start":
                self._json(LOGIN.start(w))
            elif act == "input":
                self._json(LOGIN.send(w, str(body.get("text", ""))[:500]))
            elif act == "stop":
                self._json(LOGIN.stop(w))
            else:
                self._json({"error": "unknown action"}, 400)
        else:
            self.send_response(404); self.end_headers()

    def do_GET(self):
        if self.path == "/healthz":
            self._json({"ok": True}); return
        if not self._allowed():
            return
        if DASHBOARD_TOKEN and self.path.split("?")[0] in ("/", "/index.html") and "token=" in self.path:
            # signed in with ?token=: keep it in a cookie and drop it from the address bar (and from history)
            self.send_response(303)
            self.send_header("Set-Cookie", f"af_token={DASHBOARD_TOKEN}; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000"
                             + ("; Secure" if (self.headers.get("X-Forwarded-Proto") == "https" or ".ts.net" in (self.headers.get("Host") or "")) else ""))
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/api/panes":
            with PANES_LOCK:
                order = {"orchestrator": 0, "worker": 1}
                items = sorted(PANES.values(), key=lambda p: (order.get(p.kind, 9), p.title))
                panes = [{"id": p.id, "title": p.title, "kind": p.kind, "state": p.state,
                          "lines": list(p.lines)} for p in items]
            self._json({"roster": ROSTER.snapshot(), "panes": panes})
        elif self.path.startswith("/api/diff?"):
            import urllib.parse
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            tid, proj = qs.get("task", [""])[0], qs.get("project", ["default"])[0]
            try:
                pf = pflag(proj)
            except ValueError:
                self._json({"error": "bad project"}, 400); return
            if not re.fullmatch(r"\d{4}-[\w-]+", tid):
                self._json({"error": "bad task id"}, 400); return
            rc, out, err = ssh_run_full(f"cd {FORGE_DIR} && bin/foreman{pf} diff {tid}", timeout=40)
            raw = (out if rc == 0 else f"(chyba: {err.strip()[-300:]})").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        elif self.path.split("?")[0] == "/api/settings":
            import urllib.parse
            proj = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("project", ["default"])[0]
            try:
                pf = pflag(proj)
            except ValueError:
                self._json({"error": "bad project"}, 400); return
            rc, out, err = ssh_run_full(f"cd {FORGE_DIR} && bin/foreman{pf} settings", timeout=60)
            try:
                self._json(json.loads(out))
            except Exception:
                self._json({"error": (err or out)[-300:] or "settings need a newer bin/foreman"})
        elif self.path.startswith("/api/jobs/"):
            j = JOBS.get(self.path.rsplit("/", 1)[-1])
            self._json(j or {"error": "unknown job"})
        elif self.path.startswith("/api/login?"):
            import urllib.parse
            w = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("worker", [""])[0]
            self._json(LOGIN.read(w) if re.fullmatch(r"worker-\d+", w) else {"error": "bad worker"})
        elif self.path == "/api/projects":
            self._json({"projects": PROJECTS["list"] or [{"name": "default"}]})
        elif self.path.split("?")[0] == "/api/foreman":
            import urllib.parse
            proj = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("project", ["default"])[0]
            e = FOREMAN_BY.get(proj) or {"data": None, "at": 0, "error": "unknown project or not loaded yet"}
            self._json({"foreman": e["data"], "fetched_at": e["at"], "error": e["error"]})
        elif self.path.split("?")[0] in ("/", "/index.html"):
            raw = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        elif self.path == "/healthz":
            self._json({"ok": True})
        else:
            self.send_response(404)
            self.end_headers()


def main():
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    atexit.register(shutdown)
    threading.Thread(target=discover_loop, daemon=True).start()
    threading.Thread(target=quota_loop, daemon=True).start()
    threading.Thread(target=foreman_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", BIND_PORT), Handler)
    srv.serve_forever()


if __name__ == "__main__":
    main()
