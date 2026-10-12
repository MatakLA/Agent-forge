"""The dashboard's backend: reaches Agent-Forge locally or over SSH, polls foreman status,
and executes background jobs (new worker, new project, connecting upstream) and pty login sessions.

Public interface: ssh_run, ssh_run_full, local_run, local_run_full, pflag, add_worker_job, create_project_job,
connect_upstream_job, LOGIN (LoginSessions), foreman_loop.

Invariants:
- All owner-supplied parameters formatted into shell strings are strictly allowlisted.
- Vision text and configuration secrets travel strictly via stdin, never in command line or argv.
- LoginSessions validates worker names and rejects pty control sequences to prevent breakout.
- In DEMO_MODE, ssh_run and ssh_run_full are never called and all write actions return read-only errors.
"""
import atexit, collections, json, os, re, shlex, signal, subprocess, threading, time


DEMO_MODE = os.environ.get("DEMO_MODE") == "1"


BIND_PORT = int(os.environ.get("BIND_PORT", "8171"))


DISCOVER_INTERVAL = 5           # seconds between "what's running" scans


FINISHED_GRACE = 90             # seconds a finished worker's live pane stays visible after its run ends


LINE_BUFFER = 400               # rendered lines kept per pane


SSH_TIMEOUT = 6


# Two modes: LOCAL (default) - the dashboard runs on the same machine as bin/foreman and reads it directly; REMOTE - set VM_IP (+
# VM_USER, VM_KEY, VM_KNOWN_HOSTS) and it reads the Agent-Forge machine over ssh, e.g. from a host that runs Agent-Forge in a VM.
VM_IP = "" if DEMO_MODE else os.environ.get("VM_IP", "")


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
    assert not DEMO_MODE, "Demo mode: ssh_run must never be called"
    if not isinstance(remote_cmd, str):
        LAST_ERR["msg"] = "remote_cmd must be a string"
        return ""
    try:
        r = subprocess.run(SSH_BASE + [remote_cmd], capture_output=True, text=True, timeout=timeout)
        if r.returncode:
            LAST_ERR["msg"] = f"exit {r.returncode}: {(r.stderr or '').strip()[-200:]}"
        return r.stdout if r.returncode == 0 else ""
    except Exception as e:
        LAST_ERR["msg"] = f"{type(e).__name__}: {e}"[:300]
        return ""


def local_run(cmd, timeout=SSH_TIMEOUT):
    if not isinstance(cmd, str):
        LAST_ERR["msg"] = "cmd must be a string"
        return ""
    try:
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout)
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
PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]{1,40}/[A-Za-z0-9_.-]{1,60}")
WORKER_RE = re.compile(r"worker-\d+")
LANG_RE = re.compile(r"[A-Za-z][A-Za-z ()-]{0,30}")
VALID_RUNTIMES = frozenset({"agy", "claude", "codex", "gemini", "compat", "aider", "opencode", "goose", "qwen-code", "anthropic-compat"})
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


JOBS = {}


def start_job(kind, fn):
    for k in [k for k, j in JOBS.items() if j["state"] != "running" and time.time() - j["started"] > 86400]:
        del JOBS[k]                                  # finished jobs are kept a day for the page, then dropped
    jid = f"{kind}-{int(time.time() * 1000)}"
    JOBS[jid] = {"id": jid, "kind": kind, "state": "running", "log": "", "started": time.time()}
    threading.Thread(target=fn, args=(JOBS[jid],), daemon=True).start()
    return JOBS[jid]


def add_worker_job(job, runtime="agy"):
    """Next free worker-N: Forgejo account + token, HOME volume, container (bin/worker-add). agy: the login is a separate
    interactive step (LOGIN), the owner opens a URL with that worker's Google account. API runtimes: the key must already be
    stored (POST /api/secret) - the worker works right away."""
    if DEMO_MODE:
        job["state"] = "failed"
        job["log"] = "Demo mode: read-only, no real workers"
        return {"error": "Demo mode: read-only, no real workers"}
    try:
        if not isinstance(runtime, str) or runtime not in VALID_RUNTIMES:
            raise ValueError(f"bad runtime: {runtime!r}")
        out = ssh_run("docker ps -a --format '{{.Names}}'", timeout=20)
        nums = [int(x.split("-")[1]) for x in out.split() if WORKER_RE.fullmatch(x)]
        n = max(nums or [0]) + 1
        job["worker"] = f"worker-{n}"
        job["log"] += f"creating worker-{n}...\n"
        rc, o, e = ssh_run_full(f"cd {FORGE_DIR} && bin/worker-add {n}" + (f" --runtime {shlex.quote(runtime)}" if runtime != "agy" else "") + " 2>&1", timeout=1800)
        job["log"] += ANSI.sub("", o + e)[-4000:]
        job["state"] = "done" if rc == 0 else "failed"
    except Exception as ex:
        job["log"] += f"\nerror: {ex}"; job["state"] = "failed"


def create_project_job(job, name, repo, workers_, lang, vision):
    """A new project end to end: projects/<name>/ (bin/foreman init-project), its repository + access on Forgejo
    (scripts/forge-bootstrap with FORGE_REPO), its own foreman service (scripts/install-services --project, needs sudo), and -
    if the default project still uses every worker - the default project's pool shrinks to the workers not given away."""
    if DEMO_MODE:
        job["state"] = "failed"
        job["log"] = "Demo mode: read-only, no real workers"
        return {"error": "Demo mode: read-only, no real workers"}
    def step(label, cmd, timeout=600, inp=None):
        job["log"] += f"==> {label}\n"
        rc, o, e = ssh_run_full(cmd, timeout=timeout, inp=inp)
        job["log"] += ANSI.sub("", o + e)[-3000:] + "\n"
        return rc == 0
    try:
        if not isinstance(name, str) or not PROJECT_RE.fullmatch(name) or name == "default":
            raise ValueError(f"bad project name: {name!r}")
        if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
            raise ValueError(f"bad repo: {repo!r}")
        if workers_:
            if isinstance(workers_, str):
                ws = [w.strip() for w in workers_.split(",") if w.strip()]
            elif isinstance(workers_, (list, tuple, set)):
                ws = list(workers_)
            else:
                raise ValueError(f"bad workers: {workers_!r}")
            if not all(isinstance(w, str) and WORKER_RE.fullmatch(w) for w in ws):
                raise ValueError(f"bad workers: {workers_!r}")
        else:
            ws = []
        if not isinstance(lang, str) or not LANG_RE.fullmatch(lang):
            raise ValueError(f"bad lang: {lang!r}")
        if vision is not None and not isinstance(vision, str):
            raise ValueError(f"bad vision: {type(vision)!r}")
        wf = f" --workers {','.join(ws)}" if ws else ""
        if not step("project files", f"cd {FORGE_DIR} && bin/foreman init-project {name} --repo {shlex.quote(repo)}{wf} --lang {shlex.quote(lang)} --vision -",
                    timeout=30, inp=vision):
            job["state"] = "failed"; return
        if not step("repository on Forgejo", f"cd {FORGE_DIR} && FORGE_REPO={shlex.quote(repo)} scripts/forge-bootstrap 2>&1"):
            job["state"] = "failed"; return
        if ws:
            rc, out, _ = ssh_run_full(f"cd {FORGE_DIR} && bin/foreman settings", timeout=60)
            try:
                cur = json.loads(out)
                if not cur["values"].get("WORKERS"):
                    rest = [w for w in cur.get("workers", []) if w not in ws]
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


def connect_upstream_job(job, pf, changes, upstream, force):
    """Save where the code lives, check the token, and import upstream's main into Forgejo (bin/upstream import)."""
    if DEMO_MODE:
        job["state"] = "failed"
        job["log"] = "Demo mode: read-only, no real workers"
        return {"error": "Demo mode: read-only, no real workers"}
    def step(label, cmd, inp=None, timeout=120):
        job["log"] += f"==> {label}\n"
        rc, o, e = ssh_run_full(cmd, timeout=timeout, inp=inp)
        job["log"] += ANSI.sub("", o + e)[-2500:] + "\n"
        return rc == 0
    try:
        if pf:
            if isinstance(pf, str) and pf.startswith(" --project "):
                p = pf[len(" --project "):]
            elif isinstance(pf, str):
                p = pf
            else:
                raise ValueError(f"bad project flag: {pf!r}")
            if p != "default":
                if not PROJECT_RE.fullmatch(p):
                    raise ValueError(f"bad project: {p!r}")
                pf = f" --project {p}"
            else:
                pf = ""
        else:
            pf = ""
        if not isinstance(changes, dict):
            raise ValueError("changes must be a dict")
        if not step("settings", f"cd {FORGE_DIR} && bin/foreman{pf} settings apply", inp=json.dumps(changes)):
            job["state"] = "failed"; return
        if upstream:
            if not step("access check", f"cd {FORGE_DIR} && bin/upstream{pf} check"):
                job["state"] = "failed"; return
            if not step("import main into the internal Forgejo", f"cd {FORGE_DIR} && bin/upstream{pf} import" + (" --force" if force else ""), timeout=600):
                job["state"] = "failed"; return
        job["state"] = "done"
    except Exception as ex:
        job["log"] += f"\nerror: {ex}"; job["state"] = "failed"


class LoginSessions:
    """`docker exec -it worker-N agy-run` with a real terminal (ssh -tt / a local pty), so the owner can do agy's interactive
    login from the browser: the page shows the output (the login URL) and sends the code back."""
    def __init__(self):
        self.s = {}
        self.lock = threading.Lock()

    def start(self, w):
        if DEMO_MODE:
            return {"error": "Demo mode: read-only, no real workers"}
        if not isinstance(w, str) or not WORKER_RE.fullmatch(w):
            return {"error": "bad worker"}
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
        if DEMO_MODE:
            return {"error": "Demo mode: read-only, no real workers"}
        if not isinstance(w, str) or not WORKER_RE.fullmatch(w):
            return {"error": "bad worker"}
        if not isinstance(text, str) or CONTROL_CHARS.search(text):
            return {"error": "control characters not allowed"}
        if len(text) > 4096:
            return {"error": "text too long"}
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
        if not isinstance(w, str) or not WORKER_RE.fullmatch(w):
            return {"running": False, "output": "", "error": "bad worker"}
        sess = self.s.get(w)
        if not sess:
            return {"running": False, "output": ""}
        out = sess["buf"]
        urls = re.findall(r"https://[^\s'\"<>]+", out)
        return {"running": sess["proc"].poll() is None, "output": out[-6000:], "url": urls[-1] if urls else None}

    def stop(self, w):
        if DEMO_MODE:
            return {"error": "Demo mode: read-only, no real workers"}
        if not isinstance(w, str) or not WORKER_RE.fullmatch(w):
            return {"error": "bad worker"}
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
    assert not DEMO_MODE, "Demo mode: ssh_run_full must never be called"
    if not isinstance(remote_cmd, str):
        return 1, "", "remote_cmd must be a string"
    try:
        r = subprocess.run(SSH_BASE + [remote_cmd], capture_output=True, text=True, timeout=timeout, input=inp)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 1, "", str(e)


def local_run_full(cmd, timeout=30, inp=None):
    if not isinstance(cmd, str):
        return 1, "", "cmd must be a string"
    try:
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout, input=inp)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 1, "", str(e)


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


FOREMAN_INTERVAL = 4


FOREMAN_BY = {}           # project name -> {"data", "at", "error"}; "default" = the project without --project


PROJECTS = {"list": [], "at": 0}


def pflag(project):
    """`--project NAME` for bin/foreman, "" for the default project; anything that is not a project name is rejected."""
    if project is None or project == "" or project == "default":
        return ""
    if not isinstance(project, str) or not PROJECT_RE.fullmatch(project):
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
    try:
        pf = pflag(name)
    except ValueError:
        entry["error"] = f"bad project: {name}"
        return
    run_fn = local_run if DEMO_MODE else ssh_run
    out = run_fn(f"cd {FORGE_DIR} && bin/foreman{pf} status --dashboard 2>/dev/null", timeout=12)
    data = None
    try:
        data = json.loads(out)
    except Exception:
        if name == "default":
            try:
                data = foreman_from_old_json(json.loads(run_fn(f"cd {FORGE_DIR} && bin/foreman status --json 2>/dev/null", timeout=12)))
            except Exception:
                pass
    if data is not None:
        entry.update(data=data, at=time.time(), error=None)
    else:
        entry["error"] = f"cannot read foreman status ({LAST_ERR['msg'] or 'no output'}; open files {open_fds()})"


def foreman_loop():
    run_fn = local_run if DEMO_MODE else ssh_run
    while True:
        try:
            if time.time() - PROJECTS["at"] > 30:
                try:
                    lst = json.loads(run_fn(f"cd {FORGE_DIR} && bin/foreman projects --json 2>/dev/null", timeout=15))
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
