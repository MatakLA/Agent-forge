"""The dashboard's backend: how it reaches Agent-Forge (LOCAL: next to it; REMOTE: over ssh to the machine that runs it), the
foreman's status per project (polled every few seconds), and the long write actions that run as jobs (new worker, new project,
connecting GitHub/GitLab) plus the browser terminal for a worker's Antigravity sign-in."""
import atexit, collections, json, os, re, signal, subprocess, threading, time


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


ANSI = re.compile(r"\x1b_.*?\x1b\\|\x1bP.*?\x1b\\|\x1b\[[0-9;?<>=]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\r", re.S)


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
    try:
        out = ssh_run("docker ps -a --format '{{.Names}}'", timeout=20)
        nums = [int(x.split("-")[1]) for x in out.split() if re.fullmatch(r"worker-\d+", x)]
        n = max(nums or [0]) + 1
        job["worker"] = f"worker-{n}"
        job["log"] += f"creating worker-{n}...\n"
        rc, o, e = ssh_run_full(f"cd {FORGE_DIR} && bin/worker-add {n}" + (f" --runtime {runtime}" if runtime != "agy" else "") + " 2>&1", timeout=1800)
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


def connect_upstream_job(job, pf, changes, upstream, force):
    """Save where the code lives, check the token, and import upstream's main into Forgejo (bin/upstream import)."""
    def step(label, cmd, inp=None, timeout=120):
        job["log"] += f"==> {label}\n"
        rc, o, e = ssh_run_full(cmd, timeout=timeout, inp=inp)
        job["log"] += ANSI.sub("", o + e)[-2500:] + "\n"
        return rc == 0
    try:
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
    """`docker exec -it worker-N agy-run` in a real pseudo-terminal (through ssh -tt in remote mode), so the owner can do agy's
    interactive login from the browser: the page runs a terminal emulator (xterm.js) on the raw byte stream, types into it and resizes it."""
    KEEP = 400000

    def __init__(self):
        self.s = {}
        self.lock = threading.Lock()

    @staticmethod
    def _size(fd, cols, rows):
        import fcntl, struct, termios
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def start(self, w, cols=120, rows=32):
        import pty
        cols, rows = min(max(int(cols), 40), 400), min(max(int(rows), 10), 120)
        with self.lock:
            old = self.s.get(w)
            if old and old["proc"].poll() is None:
                return {"ok": True, "note": "already running"}
            mfd, sfd = pty.openpty()
            self._size(mfd, cols, rows)
            cmd = f"docker exec -it {w} agy-run"
            argv = (SSH_BASE[:1] + ["-tt"] + SSH_BASE[1:] + [cmd]) if VM_IP else ["docker", "exec", "-it", w, "agy-run"]
            proc = subprocess.Popen(argv, stdin=sfd, stdout=sfd, stderr=sfd, start_new_session=True)
            os.close(sfd)
            sess = {"proc": proc, "rfd": mfd, "buf": "", "raw": "", "out": b"", "base": 0, "cols": cols, "started": time.time()}
            self.s[w] = sess
            ALL_PROCS.add(proc)
            threading.Thread(target=self._pump, args=(sess,), daemon=True).start()
            return {"ok": True}

    def _put(self, sess, data):
        try:
            os.write(sess["rfd"], data)
            return True
        except OSError:
            return False

    def _pump(self, sess):
        while True:
            try:
                chunk = os.read(sess["rfd"], 4096)
            except OSError:
                break
            if not chunk:
                break
            sess["out"] += chunk
            if len(sess["out"]) > self.KEEP:
                cut = len(sess["out"]) - self.KEEP
                sess["out"], sess["base"] = sess["out"][cut:], sess["base"] + cut
            sess["raw"] = (sess["raw"] + chunk.decode(errors="replace"))[-20000:]
            sess["buf"] = ANSI.sub("", sess["raw"])
            if not sess.get("menu_done") and "Select login method" in sess["buf"] and "Google OAuth" in sess["buf"]:
                sess["menu_done"] = True
                time.sleep(0.5)
                self._put(sess, b"\r")
        try:
            os.close(sess["rfd"])
        except OSError:
            pass
        reap(sess["proc"])

    def _live(self, w):
        sess = self.s.get(w)
        return sess if sess and sess["proc"].poll() is None else None

    def write(self, w, b64):
        """Raw keystrokes (or a paste) from the browser's terminal."""
        import base64, binascii
        sess = self._live(w)
        if not sess:
            return {"error": "no running login session"}
        try:
            data = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            return {"error": "bad data"}
        if len(data) > 8192:
            return {"error": "too much at once"}
        return {"ok": self._put(sess, data)}

    def resize(self, w, cols, rows):
        sess = self._live(w)
        if not sess:
            return {"error": "no running login session"}
        cols, rows = min(max(int(cols), 40), 400), min(max(int(rows), 10), 120)
        sess["cols"] = cols
        self._size(sess["rfd"], cols, rows)
        try:
            os.kill(sess["proc"].pid, signal.SIGWINCH)
        except OSError:
            pass
        return {"ok": True}

    def read(self, w, since=None):
        import base64
        sess = self.s.get(w)
        if not sess:
            return {"running": False, "output": "", "next": 0}
        out = sess["buf"]
        res = {"running": sess["proc"].poll() is None, "url": self._url(out, sess["cols"])}
        if since is None:
            res["output"] = re.sub(r"[-─━=_]{60,}", "─" * 40, out)[-6000:]
        else:
            start = max(int(since), sess["base"])
            res["data"] = base64.b64encode(sess["out"][start - sess["base"]:]).decode()
            res["next"] = sess["base"] + len(sess["out"])
        return res

    @staticmethod
    def _url(text, cols):
        """The sign-in URL (agy breaks a long line at the terminal width: a full line continues on the next one), else the last link."""
        lines = text.split("\n")
        found = []
        for i, ln in enumerate(lines):
            m = re.search(r"https://[^\s'\"<>]+", ln)
            if not m:
                continue
            u, j = m.group(0), i
            while len(ln.rstrip()) >= cols - 3 and j + 1 < len(lines) and re.match(r"\s?(?!https?:)[^\s'\"<>]+", lines[j + 1]):
                j += 1
                ln = lines[j]
                u += re.match(r"\s?([^\s'\"<>]+)", ln).group(1)
            found.append(u)
        oauth = [u for u in found if "accounts.google.com/o/oauth2" in u] or [u for u in found if "accounts.google.com" in u]
        return (oauth or found or [None])[-1]

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
