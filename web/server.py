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
    """One event of a worker's stream, whatever its runtime (detected from the event's shape): agy has "event"; Claude Code has
    type system/assistant/user/result; Codex has thread.* / turn.* / item.*; Gemini has init/message/tool_use/tool_result/result
    with a "timestamp"."""
    t = d.get("type")
    if "event" not in d and isinstance(t, str):
        if t.startswith(("thread.", "turn.", "item.")) or (t == "error" and "timestamp" not in d):
            yield from _render_codex(d)
        elif "timestamp" in d and t in ("init", "message", "tool_use", "tool_result", "result", "error"):
            yield from _render_gemini(d, acc)
        else:
            yield from _render_claude(d)
        return
    yield from _render_agy(d, acc)


def is_final(d):
    """The event that ends a worker's run, for any runtime."""
    t = d.get("type")
    return d.get("event") == "result" or t == "result" or t in ("turn.completed", "turn.failed")


def _render_claude(d):
    if d.get("type") == "result":
        ok = d.get("subtype") == "success" and not d.get("is_error")
        cost = d.get("total_cost_usd")
        yield "result", ("SUCCESS: " if ok else f"ERROR {d.get('subtype')}: ") + trunc(str(d.get("result") or "")) + \
            (f"  (${cost:.2f})" if isinstance(cost, (int, float)) else "")
        return
    if d.get("type") == "system":
        return
    yield from render_orchestrator_line(d, {})


def _render_codex(d):
    t, it = d.get("type"), d.get("item") or {}
    if t == "item.started" and it.get("type") == "command_execution":
        yield "tool", trunc(f"shell: {it.get('command', '')}")
    elif t == "item.completed":
        k = it.get("type")
        if k == "command_execution":
            yield "tool_done", f"done (exit {it.get('exit_code')})"
        elif k == "agent_message":
            yield "assistant", trunc(it.get("text", ""))
        elif k == "file_change":
            yield "tool", trunc("files: " + ", ".join(str(c.get("path", "")) for c in it.get("changes") or [] if isinstance(c, dict)))
        elif k == "error":
            yield "tool_done", trunc("note: " + str(it.get("message", "")))
    elif t == "turn.completed":
        u = d.get("usage") or {}
        yield "result", f"SUCCESS: turn completed ({u.get('input_tokens', 0)} in / {u.get('output_tokens', 0)} out tokens)"
    elif t == "turn.failed":
        err = d.get("error")
        yield "result", "ERROR: " + trunc(str(err.get("message") if isinstance(err, dict) else err))


def _render_gemini(d, acc):
    t = d.get("type")
    if t == "message" and d.get("role") == "user":
        yield "user", trunc(str(d.get("content", "")))
        yield "task", trunc(str(d.get("content", "")), 140)
    elif t == "message":
        acc["g"] = acc.get("g", "") + str(d.get("content", ""))
    elif t == "tool_use":
        if acc.get("g"):
            yield "assistant", trunc(acc.pop("g"))
        p = d.get("parameters") or {}
        yield "tool", trunc(f"{d.get('tool_name')}: {p.get('command') or json.dumps(p, ensure_ascii=False)}")
    elif t == "tool_result":
        yield "tool_done", "done" if d.get("status") == "success" else trunc(f"{d.get('status')}: {d.get('output', '')}")
    elif t == "result":
        if acc.get("g"):
            yield "assistant", trunc(acc.pop("g"))
        yield "result", f"{'SUCCESS' if d.get('status') == 'success' else 'ERROR'}: {(d.get('stats') or {}).get('total_tokens', 0)} tokens"
    elif t == "error":
        yield "tool_done", trunc("error: " + str(d.get("message") or d))


def _render_agy(d, acc):
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
            if self.kind == "worker" and is_final(d):
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


def discover_orchestrator():
    """The orchestrator's Claude Code session (if one runs next to Agent-Forge): the newest transcript of CLAUDE_PROJECT."""
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
    # newest 200 run directories with the age of their "finished" marker (-1 = still running) in one call
    out = ssh_run(f"cd {FORGE_DIR}/runs 2>/dev/null && now=$(date +%s) && ls -1dt */ 2>/dev/null | head -200 | while read d; do "
                  f"f=-1; [ -e \"$d/finished\" ] && f=$(( now - $(stat -c %Y \"$d/finished\") )); echo \"$d $f\"; done")
    latest_per_worker, all_workers = {}, set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        base = parts[0].rstrip("/")
        m = RUN_DIR_RE.match(base)
        if not m:
            continue
        worker = m.group(1)
        all_workers.add(worker)
        if worker not in latest_per_worker:
            # a live pane only for a run that is running or finished a moment ago - an old finished run (or one that
            # never wrote a transcript, e.g. a failed setup) must not show up as a "running" empty pane
            if parts[1].lstrip("-").isdigit() and (int(parts[1]) < 0 or int(parts[1]) < FINISHED_GRACE):
                latest_per_worker[worker] = (base, f"{FORGE_DIR}/runs/{base}")
            else:
                latest_per_worker.setdefault(worker, None)
    return {w: v for w, v in latest_per_worker.items() if v}, all_workers


QUOTA_POLL_INTERVAL = 60   # seconds; agy /usage is metadata, not a model call, but no need to hammer it


def parse_usage(text):
    """Same as bin/lib/sbx.py's parse_usage: tab-separated model / label / pct% / reset."""
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
    orch = discover_orchestrator()
    if orch:
        sid, path, running, tail_argv = orch
        wanted["orchestrator"] = ("Orchestrator (Claude Code)", "orchestrator", path, render_orchestrator_line, running, tail_argv)

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
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
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
  .rt { font-size:11px; padding:1px 6px; border-radius:5px; background:var(--card2); color:var(--muted); }
  .rt-claude { color:#d97757; } .rt-codex { color:var(--ok); } .rt-gemini { color:var(--accent); }
  .addw { background:var(--card2); border-radius:10px; padding:8px 10px; margin-top:8px; }
  .addw input[type=password], #key-form input, #key-form select { background:var(--card); color:var(--text); border:1px solid var(--line); border-radius:8px; padding:6px 8px; font:inherit; }
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
  <div class="top"><h1>Agent-Forge</h1><select id="proj" class="projsel" style="display:none" aria-label="project"></select><span id="pills"></span></div>
  <div id="jserr" style="display:none; margin-top:8px; font-size:12.5px; color:var(--bad);"></div>
  <div id="resume-result" style="display:none; margin-top:8px; font-size:12.5px;"></div>
  <div class="stats" id="stats"></div>
  <nav id="nav">
    <button data-t="overview">Overview</button>
    <button data-t="queue">Queue <span class="cnt" id="c-queue">0</span></button>
    <button data-t="timeline">Timeline</button>
    <button data-t="live">Live <span class="cnt" id="c-live">0</span></button>
    <button data-t="metrics">Metrics</button>
    <button data-t="settings">Settings</button>
    <button data-t="events">Events</button>
  </nav>
</header>
<main>
  <section id="t-overview">
    <h3>New goal</h3>
    <div class="ask"><textarea id="goal-text" placeholder="What should the team do? E.g.: Add a CSV export of expenses, with tests."></textarea>
      <div class="btnrow"><button id="goal-now">Start now</button><button class="mini" id="goal-backlog">Add to backlog</button>
        <button class="mini" id="goal-roadmap" title="The planner splits a big request into a series of smaller goals in the backlog">Big project → roadmap</button>
        <span id="goal-msg" class="muted" style="font-size:12.5px"></span></div></div>
    <div id="questions"></div>
    <h3>Ask the team</h3>
    <div class="ask"><textarea id="ask-q" placeholder="E.g. How does module X work and what is missing? (the orchestrator answers from the code; uses Claude usage)"></textarea>
      <button id="ask-btn">Ask</button><div id="ask-a"></div></div>
    <h3>Team</h3><div id="team" class="team"><div class="empty">loading…</div></div>
    <h3>Goals</h3><div id="goals"></div>
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
<div class="detail" id="login"><div class="sheet"><button class="close" id="login-close">Close</button><h2 id="login-title">Sign-in</h2>
  <div class="muted" style="font-size:12.5px;margin:6px 0">This is the worker's terminal. Open the link, sign in with the Google account meant for this worker, copy the
  code and send it here. If agy asks anything, answer here.</div>
  <div id="login-url" style="font-size:13px;margin:6px 0"></div>
  <pre id="login-out" class="term"></pre>
  <div class="btnrow"><input type="text" id="login-in" placeholder="code or answer" style="flex:1"><button class="mini" id="login-send">Send</button></div>
  <div class="btnrow"><button class="resume-btn" id="login-done">Done — check the sign-in</button></div></div></div>
<div class="detail" id="newproj"><div class="sheet"><button class="close" id="np-close">Close</button><h2>New project</h2>
  <div class="muted" style="font-size:12.5px;margin:6px 0">Creates a project with its own orchestrator, a repository on Forgejo and a group of workers.
  Each worker should belong to one project only.</div>
  <div id="np-form">
  <div class="setrow"><label>Name (lowercase letters, dashes)</label><div><input type="text" id="np-name" placeholder="e.g. shop"><div class="seterr" id="np-e-name"></div></div></div>
  <div class="setrow"><label>Repository (organisation/repository)</label><div><input type="text" id="np-repo" placeholder="e.g. acme/shop"><div class="seterr" id="np-e-repo"></div></div></div>
  <div class="setrow"><label>Language the team uses with you</label><div><input type="text" id="np-lang" value="English"><div class="seterr" id="np-e-lang"></div></div></div>
  <div class="setrow"><label>Vision — what the project should do (the autopilot plans from it)</label><textarea id="np-vision" rows="6" class="npta"></textarea></div>
  <div class="setrow"><label>Workers for this project</label><div><div id="np-workers" class="muted">loading…</div><div class="seterr" id="np-e-workers"></div></div></div>
  <div class="btnrow"><button class="resume-btn" id="np-create">Create project</button></div></div>
  <pre id="np-log" class="term" style="display:none"></pre></div></div>
<div class="detail" id="setup"><div class="sheet"><button class="close" id="su-close">Close</button><h2>Where does this project's code live?</h2>
  <div class="muted" style="font-size:12.5px;margin:6px 0">Workers always work on the internal Forgejo inside their sandbox. If the project's home is on GitHub
  or GitLab, the orchestrator opens and merges pull requests there and takes main from there. Workers never reach GitHub/GitLab.</div>
  <div id="su-form">
  <label class="chk" style="display:flex"><input type="radio" name="su-kind" value="forgejo"> Internal Forgejo only</label>
  <label class="chk" style="display:flex"><input type="radio" name="su-kind" value="github"> GitHub</label>
  <label class="chk" style="display:flex"><input type="radio" name="su-kind" value="gitlab"> GitLab</label>
  <div id="su-up">
  <div class="setrow"><label>Server</label><div><input type="text" id="su-url" placeholder="https://github.com"><div class="seterr" id="su-e-url"></div></div></div>
  <div class="setrow"><label>Repository (owner/repository)</label><div><input type="text" id="su-repo" placeholder="e.g. acme/shop"><div class="seterr" id="su-e-repo"></div></div></div>
  <div class="setrow"><label>Token (empty = keep the stored one)</label><div><input type="password" id="su-token" autocomplete="off"><div class="seterr" id="su-e-token"></div>
    <div class="muted" id="su-help" style="font-size:12px;margin-top:4px"></div></div></div>
  <div class="setrow" id="su-force-row" style="display:none"><label>The internal repository already has commits</label><div><label class="chk"><input type="checkbox" id="su-force">
    replace them with the content from GitHub/GitLab</label></div></div>
  </div>
  <div class="btnrow"><button class="resume-btn" id="su-go">Save and connect</button><button class="mini" id="su-later">Later</button></div></div>
  <pre id="su-log" class="term" style="display:none"></pre></div></div>
<script>
// every write carries X-Agent-Forge: the server refuses POSTs without it (a cross-site page cannot add it without CORS consent)
const _fetch = window.fetch.bind(window);
window.fetch = (u, o) => { o = o || {}; if ((o.method || 'GET').toUpperCase() === 'POST') o.headers = Object.assign({'X-Agent-Forge': '1'}, o.headers || {}); return _fetch(u, o); };

const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const tfmt = ts => { const d = typeof ts === 'number' ? new Date(ts*1000) : new Date(ts); return isNaN(d) ? '' : d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}); };
const dfmt = ts => { const d = new Date(ts); if (isNaN(d)) return ''; const today = new Date().toDateString() === d.toDateString();
  return today ? d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'}) : d.toLocaleString([], {day:'numeric', month:'numeric', hour:'2-digit', minute:'2-digit'}); };
const ago = ts => { if (!ts) return ''; const s = Math.max(0, Math.round(Date.now()/1000 - (typeof ts === 'number' ? ts : Date.parse(ts)/1000)));
  return s < 60 ? s + ' s' : s < 3600 ? Math.round(s/60) + ' min' : Math.round(s/3600) + ' h'; };
const STATUS = {splitting:'Splitting', pending:'Waiting', running:'Running', gate:'Checking', review:'Checking', merging:'Merging', done:'Done', blocked:'Blocked', cancelled:'Cancelled'};
const RTNAME = {agy: 'Antigravity', claude: 'Claude Code', codex: 'Codex', gemini: 'Gemini CLI'};
const RTDESC = {agy: 'a Google account with a subscription, signed in from the browser', claude: 'Anthropic API key, paid per token',
                codex: 'OpenAI API key, paid per token', gemini: 'Gemini API key, paid per token'};
const roleCls = r => /secur/i.test(r) ? 'sec' : (/test/i.test(r) && !/develop/i.test(r)) ? 'test' : '';
let tab = 'overview', F = null, P = {roster:{}, panes:[]}, openTask = null, project = 'default';
try { project = localStorage.getItem('af-project') || 'default'; } catch (e) {}
const inPool = w => !F || !(F.pool || []).length || F.pool.includes(w);
try { tab = localStorage.getItem('af-tab') || 'overview'; } catch (e) {}
// deep links: #tab=queue, #task=0012-store, #project=shop (bookmarks, links in notifications)
const HASH = Object.fromEntries((location.hash || '').replace(/^#/, '').split('&').filter(Boolean).map(kv => kv.split('=').map(decodeURIComponent)));
if (['overview', 'queue', 'timeline', 'live', 'metrics', 'settings', 'events'].includes(HASH.tab)) tab = HASH.tab;
if (HASH.project && /^[a-z0-9][a-z0-9-]{0,39}$/.test(HASH.project)) project = HASH.project;

function setTab(t) {
  tab = t;
  for (const k in lastHTML) delete lastHTML[k];
  try { localStorage.setItem('af-tab', t); } catch (e) {}
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
  msg.textContent = 'sending…';
  const r = await post('/api/goal', {text, mode});
  msg.textContent = r.ok ? {now: 'Goal queued — the planner splits it into tasks within half a minute.', backlog: 'Added to the backlog.',
    roadmap: 'The planner is preparing a roadmap — its goals will appear in the backlog.'}[mode] : 'Nepodarilo sa: ' + (r.message || r.error || '');
  if (r.ok) ta.value = '';
  setTimeout(poll, 1500);
}
document.getElementById('goal-now').addEventListener('click', () => addGoal('now'));
document.getElementById('goal-backlog').addEventListener('click', () => addGoal('backlog'));
document.getElementById('goal-roadmap').addEventListener('click', () => addGoal('roadmap'));
document.addEventListener('click', async e => {
  const b = e.target.closest('[data-bl]');
  if (b) { if (b.dataset.bl === 'drop' && !confirm('Remove this goal from the backlog?')) return;
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
  if (action === 'on' && !confirm((all ? 'Stop ALL projects' : 'Stop project “' + project + '”') + (hard ? ' HARD (also stops the worker containers)' : '') + '?')) return;
  if (action === 'off' && F && F.killswitch && F.killswitch.hard && !confirm('The kill switch is in hard mode. Start the workers again and continue?')) return;
  ksBusy = true;
  const box = document.getElementById('resume-result');
  box.style.display = 'block'; box.innerHTML = '<div class="ev">' + (action === 'on' ? 'stopping…' : 'continuing…') + '</div>';
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
const LABEL = {PLAN_PROVIDERS: 'Who plans (in order)', REVIEW_PROVIDERS: 'Who reviews (in order)', CLAUDE_MODEL: 'Claude Code model',
  AGY_PLAN_MODEL: 'Antigravity model for planning', AGY_REVIEW_MODEL: 'Antigravity model for reviews', AGY_FALLBACK_MODEL: 'Antigravity fallback (Gemini)',
  WORKER_MODEL: 'Worker model', WORKER_MODEL_RISKY: 'Worker model for risky tasks', ANTHROPIC_MODEL: 'Anthropic API model (paid)',
  OPENAI_MODEL: 'OpenAI-compatible model (paid)', GEMINI_MODEL: 'Gemini API model (paid)', OWNER_LANG: 'Your language',
  AUTOPILOT: 'Autopilot (picks the next goals itself)', AUTO_MERGE: 'Automatic merge', CONTINUE_CONVERSATIONS: 'Continue the conversation on a rework',
  MAX_REWORK: 'Max. reworks per task', MAX_ACTIVE_GOALS: 'Max. active goals', SPLIT_AFTER_CAPS: 'Split a task after N token caps',
  TASK_TOKEN_BUDGET: 'Target task size (tokens)', MAX_PARALLEL: 'Max. parallel runs', RUN_TOKEN_CAP: 'Token cap per run',
  RUN_TIMEOUT_MIN: 'Run time limit (min)', QUOTA_MIN_5H_PCT: 'Worker\'s minimum 5-hour quota (%)', QUOTA_MIN_WEEK_PCT: 'Minimum weekly quota (%)',
  CONSEC_FAIL_TRIP: 'Kill switch after N failures in a row',
  CONTEXT_PACK: 'Context pack in each task (on / off / ab = half the tasks, to measure)', CONTEXT_PACK_CHARS: 'Max. pack size (characters)',
  SELF_CHECK_ROUNDS: 'Fix failing tests in the same run (max. rounds)', MAX_TASK_FILES: 'Max. files per task (more = split up front)',
  WORKER_MODEL_LIGHT: 'Model for light roles', LIGHT_ROLES: 'Light roles (comma-separated)', AB_MODEL: 'A/B test: alternative worker model',
  AB_SHARE: 'A/B test: share of tasks on it (%)', PLAN_AHEAD: 'Plan ahead while the workers wait for quota',
  MAINTENANCE: 'LLM-free maintenance of main while idle (tests 3×, docstrings)', TEST_CMD: 'Test command (empty = pytest)',
  GATE_IMAGE: 'Test image (empty = the worker image)', NOTIFY_URL: 'Notifications: URL (ntfy topic, Slack/Discord webhook)',
  NOTIFY_FORMAT: 'Notifications: format', NOTIFY_EVENTS: 'Notifications: events', RUNS_KEEP_DAYS: 'Compress run transcripts after (days)',
  API_WORKERS_POLICY: 'When they get work', WORKER_MODEL_CLAUDE: 'Claude Code model (empty = default)',
  WORKER_MODEL_CODEX: 'Codex model (empty = default)', WORKER_MODEL_GEMINI: 'Gemini CLI model (empty = default)',
  API_RUN_BUDGET_USD: 'Cap per run (USD)', API_DAILY_BUDGET_USD: 'Cap per day, all projects (USD)',
  RUN_MAX_TOOL_CALLS: 'Max. tool calls per run', PRICE_CODEX: 'Codex price: input, cached, output (USD / 1M tokens)',
  PRICE_GEMINI: 'Gemini price: input, cached, output (USD / 1M tokens)'};
const PROVLABEL = {'claude-cli': 'Claude Code (subscription)', 'agy-claude': 'Claude via Antigravity', 'agy-gemini': 'Gemini via Antigravity',
  'anthropic-api': 'Anthropic API (paid)', 'openai-api': 'OpenAI API (paid)', 'gemini-api': 'Gemini API (paid)'};
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
  if (!S) { loadSettings(); return '<div class="empty">loading…</div>'; }
  if (S.error) return '<div class="empty">Settings are not available: ' + esc(S.error) + '</div>';
  const V = S.values || {}, num = k => '<input type="number" id="set-' + k + '" value="' + esc(V[k]) + '">',
        txt = k => '<input type="text" id="set-' + k + '" value="' + esc(V[k]) + '">', tog = k => '<input type="checkbox" id="set-' + k + '"' + (String(V[k]) === '1' ? ' checked' : '') + '>';
  const ks = F && F.killswitch, pool = String(V.WORKERS || '').split(',').filter(Boolean);
  let h = '<div class="setcard"><h3>Kill switch</h3><div class="muted" style="font-size:12.5px;margin-bottom:8px">' +
    (ks ? 'ON' + (ks.scope === 'all' ? ' for all projects' : ' for this project') + (ks.hard ? ' (hard)' : '') + ' — ' + esc(ks.reason || '') : 'off — the project is running') + '</div>' +
    '<div class="btnrow">' + (ks ? '<button class="resume-btn" onclick="resumeDev()">Continue</button>' :
      '<button class="warnbtn" onclick="killswitch(\'on\')">Stop project</button><button class="badbtn" onclick="killswitch(\'on\',{hard:true})">Stop hard</button>') +
    '<button class="badbtn" onclick="killswitch(\'on\',{all:true})">Stop all projects</button></div></div>';
  h += '<div class="setcard"><h3>This project\'s workers</h3><div class="muted" style="font-size:12.5px">Ticked workers belong to project “' + esc(project) +
    '”. None ticked = all. Each worker is its own Antigravity account or API key.</div><div id="set-WORKERS" data-kind="pool" class="pool">' +
    (S.workers || []).map(w => '<div class="poolrow"><label><input type="checkbox" value="' + esc(w.name) + '"' + (pool.includes(w.name) ? ' checked' : '') + '> ' + esc(w.name) +
      ' <span class="rt rt-' + esc(w.runtime || 'agy') + '">' + esc(RTNAME[w.runtime || 'agy'] || w.runtime) + '</span> <span class="muted">(' + esc(w.state) + ')</span></label>' +
      ((w.runtime || 'agy') === 'agy' ? '<button class="mini" data-login="' + esc(w.name) + '">sign in to Antigravity</button>' : '') + '</div>').join('') +
    '</div>' + (sErr.WORKERS ? '<div class="seterr">' + esc(sErr.WORKERS) + '</div>' : '') +
    '<div class="btnrow"><button class="mini" id="add-worker">+ Add worker</button><span id="addw-status" class="muted" style="font-size:12.5px"></span></div>' +
    '<div id="addw-form" style="display:none" class="addw"><div class="muted" style="font-size:12.5px;margin:8px 0 4px">What will the new worker run on?</div>' +
    Object.entries(S.runtimes || {agy: {label: 'Antigravity'}}).map(([k, r], i) => '<label class="chk" style="display:flex"><input type="radio" name="addw-rt" value="' + esc(k) + '"' +
      (i ? '' : ' checked') + '> ' + esc(RTNAME[k] || k) + ' <span class="muted">— ' + esc(RTDESC[k] || r.label) + '</span></label>').join('') +
    '<div id="addw-key" style="display:none;margin-top:6px"><input type="password" id="addw-keyin" autocomplete="off" placeholder="API key (stored on the server only, in the secrets directory)" style="width:100%"></div>' +
    '<div class="btnrow"><button class="resume-btn" id="addw-go">Add</button><button class="mini" id="addw-cancel">Cancel</button></div></div></div>';
  const R = S.runtimes || {}, sec = S.secrets || {};
  h += '<div class="setcard"><h3>API workers</h3><div class="muted" style="font-size:12.5px;margin-bottom:6px">Workers with an API key pay per token. ' +
    'Spent today: <b>$' + Number(S.api_spent_today || 0).toFixed(2) + '</b> of $' + esc(V.API_DAILY_BUDGET_USD) + '. Keys: ' +
    ['anthropic.key', 'openai.key', 'gemini.key'].map(k => esc(k.split('.')[0]) + ' ' + (sec[k] ? '✓' : '—')).join(', ') +
    ' <button class="mini" id="key-change">change a key…</button></div>' +
    '<div id="key-form" style="display:none" class="btnrow"><select id="key-name"><option>anthropic.key</option><option>openai.key</option><option>gemini.key</option></select>' +
    '<input type="password" id="key-val" autocomplete="off" placeholder="new key" style="flex:1;min-width:180px"><button class="mini" id="key-save">Save key</button></div>' +
    field('API_WORKERS_POLICY', '<select id="set-API_WORKERS_POLICY">' + [['overflow', 'only when the subscription workers cannot'], ['always', 'always first']].map(([v, l]) =>
      '<option value="' + v + '"' + (v === V.API_WORKERS_POLICY ? ' selected' : '') + '>' + l + '</option>').join('') + '</select>') +
    field('WORKER_MODEL_CLAUDE', txt('WORKER_MODEL_CLAUDE')) + field('WORKER_MODEL_CODEX', txt('WORKER_MODEL_CODEX')) + field('WORKER_MODEL_GEMINI', txt('WORKER_MODEL_GEMINI')) +
    field('API_RUN_BUDGET_USD', txt('API_RUN_BUDGET_USD')) + field('API_DAILY_BUDGET_USD', txt('API_DAILY_BUDGET_USD')) + field('RUN_MAX_TOOL_CALLS', num('RUN_MAX_TOOL_CALLS')) +
    field('PRICE_CODEX', txt('PRICE_CODEX')) + field('PRICE_GEMINI', txt('PRICE_GEMINI')) + '</div>';
  h += '<div class="setcard"><h3>Orchestrator</h3>' + field('PLAN_PROVIDERS', provEditor('PLAN_PROVIDERS', V.PLAN_PROVIDERS)) +
    field('REVIEW_PROVIDERS', provEditor('REVIEW_PROVIDERS', V.REVIEW_PROVIDERS)) +
    field('CLAUDE_MODEL', '<select id="set-CLAUDE_MODEL">' + ['sonnet', 'opus', 'haiku', 'fable'].concat(['sonnet', 'opus', 'haiku', 'fable'].includes(V.CLAUDE_MODEL) ? [] : [V.CLAUDE_MODEL])
      .map(m => '<option' + (m === V.CLAUDE_MODEL ? ' selected' : '') + '>' + esc(m) + '</option>').join('') + '</select>') +
    field('AGY_PLAN_MODEL', modelSel('AGY_PLAN_MODEL', V.AGY_PLAN_MODEL)) + field('AGY_REVIEW_MODEL', modelSel('AGY_REVIEW_MODEL', V.AGY_REVIEW_MODEL)) +
    field('AGY_FALLBACK_MODEL', modelSel('AGY_FALLBACK_MODEL', V.AGY_FALLBACK_MODEL)) +
    '<details><summary class="muted">Paid APIs (only if ticked above and you have a key)</summary>' + field('ANTHROPIC_MODEL', txt('ANTHROPIC_MODEL')) +
    field('OPENAI_MODEL', txt('OPENAI_MODEL')) + field('GEMINI_MODEL', txt('GEMINI_MODEL')) + '</details></div>';
  h += '<div class="setcard"><h3>Workers — models</h3>' + field('WORKER_MODEL', modelSel('WORKER_MODEL', V.WORKER_MODEL, true, 'default (' + (S.limits_worker_model || '') + ')')) +
    field('WORKER_MODEL_RISKY', modelSel('WORKER_MODEL_RISKY', V.WORKER_MODEL_RISKY, true, 'same as routine tasks')) +
    '<div class="btnrow"><button class="mini" id="refresh-models">refresh the model list from Antigravity</button></div></div>';
  const fk = V.FORGE_KIND || '';
  h += '<div class="setcard"><h3>Project</h3><div class="muted" style="font-size:12.5px;margin-bottom:6px">How the project is tested and where the team notifies you.</div>' +
    '<div class="setrow"><label>Where the code lives</label><div>' + (fk === 'github' || fk === 'gitlab' ? esc(fk === 'github' ? 'GitHub' : 'GitLab') + ': <b>' + esc(V.UPSTREAM_REPO) + '</b>' :
      fk === 'forgejo' ? 'internal Forgejo only' : 'not chosen') + ' <button class="mini" id="open-setup">change…</button></div></div>' +
    field('TEST_CMD', txt('TEST_CMD')) + field('GATE_IMAGE', txt('GATE_IMAGE')) + field('NOTIFY_URL', txt('NOTIFY_URL')) +
    field('NOTIFY_FORMAT', '<select id="set-NOTIFY_FORMAT">' + ['ntfy', 'slack', 'discord', 'json'].map(x => '<option' + (x === V.NOTIFY_FORMAT ? ' selected' : '') + '>' + x + '</option>').join('') + '</select>') +
    field('NOTIFY_EVENTS', txt('NOTIFY_EVENTS')) + field('RUNS_KEEP_DAYS', num('RUNS_KEEP_DAYS')) + '</div>';
  h += '<div class="setcard"><h3>Efficiency</h3><div class="muted" style="font-size:12.5px;margin-bottom:6px">How the team saves quota. The measurements are in the Metrics tab.</div>' +
    field('CONTEXT_PACK', '<select id="set-CONTEXT_PACK">' + ['on', 'ab', 'off'].map(x => '<option' + (x === V.CONTEXT_PACK ? ' selected' : '') + '>' + x + '</option>').join('') + '</select>') +
    field('CONTEXT_PACK_CHARS', num('CONTEXT_PACK_CHARS')) + field('SELF_CHECK_ROUNDS', num('SELF_CHECK_ROUNDS')) + field('MAX_TASK_FILES', num('MAX_TASK_FILES')) +
    field('WORKER_MODEL_LIGHT', modelSel('WORKER_MODEL_LIGHT', V.WORKER_MODEL_LIGHT, true, 'same as routine tasks')) + field('LIGHT_ROLES', txt('LIGHT_ROLES')) +
    field('AB_MODEL', modelSel('AB_MODEL', V.AB_MODEL, true, 'no A/B test')) + field('AB_SHARE', num('AB_SHARE')) +
    field('PLAN_AHEAD', tog('PLAN_AHEAD')) + field('MAINTENANCE', tog('MAINTENANCE')) + '</div>';
  h += '<div class="setcard"><h3>Behaviour</h3>' + field('AUTOPILOT', tog('AUTOPILOT')) + field('AUTO_MERGE', tog('AUTO_MERGE')) +
    field('CONTINUE_CONVERSATIONS', tog('CONTINUE_CONVERSATIONS')) + field('OWNER_LANG', txt('OWNER_LANG')) + field('MAX_REWORK', num('MAX_REWORK')) +
    field('MAX_ACTIVE_GOALS', num('MAX_ACTIVE_GOALS')) + field('SPLIT_AFTER_CAPS', num('SPLIT_AFTER_CAPS')) + field('TASK_TOKEN_BUDGET', num('TASK_TOKEN_BUDGET')) + '</div>';
  h += '<div class="setcard"><h3>Limity <span class="pill warn">apply to ALL projects</span></h3>' +
    ['MAX_PARALLEL', 'RUN_TOKEN_CAP', 'RUN_TIMEOUT_MIN', 'QUOTA_MIN_5H_PCT', 'QUOTA_MIN_WEEK_PCT', 'CONSEC_FAIL_TRIP'].map(k => field(k, num(k))).join('') +
    ((S.workers || []).length > Number(V.MAX_PARALLEL) ? '<div class="seterr">There are more workers (' + S.workers.length + ') than MAX_PARALLEL — some will idle.</div>' : '') + '</div>';
  h += '<div class="btnrow sticky"><button class="resume-btn" id="save-settings">Save changes</button><span class="muted" style="font-size:12.5px">' + esc(sMsg) + '</span></div>';
  return h;
}
async function saveSettings() {
  const V = S.values || {}, changes = {};
  for (const k of Object.keys(V)) { const v = sval(k); if (v !== null && String(v) !== String(V[k])) changes[k] = v; }
  if (!Object.keys(changes).length) { sMsg = 'nothing changed'; render(true); return; }
  let r;
  try { r = await (await fetch('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({project, changes})})).json(); }
  catch (e) { r = {ok: false, errors: {_: String(e)}}; }
  sErr = r.errors || {};
  sMsg = r.ok ? 'saved: ' + Object.keys(changes).join(', ') + ' — applies from the next round (within 20 s)' : 'not saved — fix the marked fields' + (sErr._ ? ': ' + sErr._ : '');
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
  if (e.target.id === 'refresh-models') { e.target.textContent = 'loading…';
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
  const f = document.getElementById('addw-form'); f.style.display = f.style.display === 'none' ? '' : 'none'; addwRt();
}
function addwRt() {
  const rt = (document.querySelector('input[name="addw-rt"]:checked') || {}).value || 'agy', R = (S && S.runtimes) || {};
  const key = (R[rt] || {}).key, have = key && ((S.secrets || {})[key]);
  document.getElementById('addw-key').style.display = key && !have ? '' : 'none';
}
async function addWorkerGo() {
  const rt = (document.querySelector('input[name="addw-rt"]:checked') || {}).value || 'agy', st = document.getElementById('addw-status');
  const key = ((S.runtimes || {})[rt] || {}).key, kin = document.getElementById('addw-keyin');
  if (key && !(S.secrets || {})[key]) {
    if (!kin.value.trim()) { kin.focus(); return; }
    const r = await post('/api/secret', {name: key, value: kin.value.trim()}); kin.value = '';
    if (!r.ok) { st.textContent = 'the key could not be saved: ' + (r.message || r.error || ''); return; }
  }
  document.getElementById('addw-form').style.display = 'none';
  let j = await post('/api/workers', {action: 'add', runtime: rt});
  while (j.state === 'running') { st.textContent = 'creating ' + (j.worker || 'the worker') + '… (the first time takes a few minutes)'; await new Promise(r => setTimeout(r, 3000));
    j = await (await fetch('/api/jobs/' + encodeURIComponent(j.id))).json(); }
  st.textContent = j.state !== 'done' ? 'failed: ' + (j.log || j.error || '').slice(-300) : rt === 'agy' ? j.worker + ' created — now sign it in' : j.worker + ' created and ready';
  await loadSettings(true);
  if (j.state === 'done' && rt === 'agy') openLogin(j.worker);
}
document.addEventListener('change', e => { if (e.target.name === 'addw-rt') addwRt(); });
document.addEventListener('click', async e => {
  if (e.target.id === 'addw-go') { addWorkerGo(); return; }
  if (e.target.id === 'addw-cancel') { document.getElementById('addw-form').style.display = 'none'; return; }
  if (e.target.id === 'key-change') { const f = document.getElementById('key-form'); f.style.display = f.style.display === 'none' ? '' : 'none'; return; }
  if (e.target.id === 'key-save') { const v = document.getElementById('key-val');
    if (!v.value.trim()) { v.focus(); return; }
    const r = await post('/api/secret', {name: document.getElementById('key-name').value, value: v.value.trim()}); v.value = '';
    sMsg = r.ok ? 'key saved' : 'key not saved: ' + (r.message || r.error || ''); loadSettings(true); return; }
  if (e.target.id === 'open-setup') { openSetup(); return; }
  if (e.target.id === 'su-close' || e.target.id === 'su-later') { document.getElementById('setup').classList.remove('on'); setupDismissed[project] = true; return; }
  if (e.target.id === 'su-go') { setupGo(); return; }
});

// ---- where the code lives: internal Forgejo only, GitHub or GitLab (shown once for a new project, or from Settings → Project)
const setupDismissed = {};
const SU_URL = {github: 'https://github.com', gitlab: 'https://gitlab.com'};
function suKind() { return (document.querySelector('input[name="su-kind"]:checked') || {}).value || 'forgejo'; }
function suRefresh() {
  const k = suKind(), up = k !== 'forgejo';
  document.getElementById('su-up').style.display = up ? '' : 'none';
  if (up) { const u = document.getElementById('su-url'); if (!u.value || Object.values(SU_URL).includes(u.value)) u.value = SU_URL[k];
    document.getElementById('su-help').textContent = k === 'github'
      ? 'Token: GitHub → Settings → Developer settings → Fine-grained token for this repository only, permissions Contents and Pull requests: Read and write.'
      : 'Token: GitLab → project → Settings → Access tokens, role Developer, scopes api and write_repository.'; }
}
document.addEventListener('change', e => { if (e.target.name === 'su-kind') suRefresh(); });
function openSetup() {
  const V = (S && S.values) || {}, m = document.getElementById('setup');
  const k = V.FORGE_KIND || (F && F.forge && F.forge.kind) || 'forgejo';
  document.querySelectorAll('input[name="su-kind"]').forEach(r => r.checked = r.value === (k || 'forgejo'));
  document.getElementById('su-repo').value = V.UPSTREAM_REPO || (F && F.forge && F.forge.repo) || '';
  document.getElementById('su-url').value = V.UPSTREAM_URL || (F && F.forge && F.forge.url) || '';
  document.getElementById('su-form').style.display = ''; document.getElementById('su-log').style.display = 'none';
  document.getElementById('su-force-row').style.display = 'none';
  suRefresh(); m.classList.add('on');
}
async function setupGo() {
  const k = suKind(), log = document.getElementById('su-log'), tok = document.getElementById('su-token');
  for (const x of ['repo', 'url', 'token']) document.getElementById('su-e-' + x).textContent = '';
  if (k !== 'forgejo' && tok.value.trim()) {
    const r = await post('/api/secret', {name: k + '.token', value: tok.value.trim()}); tok.value = '';
    if (!r.ok) { document.getElementById('su-e-token').textContent = r.message || r.error || 'the token could not be saved'; return; }
  }
  let j = await post('/api/upstream', {action: 'connect', kind: k, repo: document.getElementById('su-repo').value.trim(),
                                       url: document.getElementById('su-url').value.trim(), force: document.getElementById('su-force').checked});
  if (j.errors) { for (const x in j.errors) { const el = document.getElementById('su-e-' + x); if (el) el.textContent = j.errors[x]; } return; }
  document.getElementById('su-form').style.display = 'none'; log.style.display = '';
  while (j.state === 'running') { log.textContent = (j.log || '') + '\n… working'; await new Promise(r => setTimeout(r, 2000));
    j = await (await fetch('/api/jobs/' + encodeURIComponent(j.id))).json(); }
  log.textContent = (j.log || '') + '\n' + (j.state === 'done' ? '✔ Done — the team now merges through this repository.' : '✘ Failed — see the output above.');
  if (j.state !== 'done' && /already has \d+ commits/.test(j.log || '')) {
    document.getElementById('su-form').style.display = ''; document.getElementById('su-force-row').style.display = ''; }
  S = null; loadSettings(true); setTimeout(poll, 1000);
}
let loginW = null, loginTimer = null;
async function openLogin(w) {
  loginW = w;
  const m = document.getElementById('login'); m.classList.add('on');
  document.getElementById('login-title').textContent = 'Sign ' + w + ' in to Antigravity';
  document.getElementById('login-out').textContent = 'starting…';
  await fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({worker: w, action: 'start'})});
  clearInterval(loginTimer);
  loginTimer = setInterval(async () => {
    let r; try { r = await (await fetch('/api/login?worker=' + encodeURIComponent(w))).json(); } catch (e) { return; }
    const o = document.getElementById('login-out'), atBottom = o.scrollHeight - o.scrollTop - o.clientHeight < 30;
    o.textContent = r.output || '(waiting for output…)'; if (atBottom) o.scrollTop = o.scrollHeight;
    const u = document.getElementById('login-url');
    u.innerHTML = r.url ? 'Open it and sign in with <b>the account for ' + esc(w) + '</b>: <a href="' + esc(r.url) + '" target="_blank" rel="noopener">' + esc(r.url.slice(0, 60)) + '…</a>' : '';
  }, 1500);
}
async function closeLogin(check) {
  clearInterval(loginTimer);
  const st = document.getElementById('login-url');
  if (check) st.textContent = 'checking the sign-in…';
  let r = {}; try { r = await (await fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({worker: loginW, action: 'stop'})})).json(); } catch (e) {}
  if (check) { alert(r.logged_in ? loginW + ' is signed in and sees its quota. Tick it in the project and save.' : loginW + ' is not signed in yet — try again.'); }
  document.getElementById('login').classList.remove('on');
  loadSettings(true);
}

function pills() {
  if (!F) return '<span class="pill bad">foreman: ' + esc(window._ferr || 'loading') + '</span>';
  const p = [];
  const staleFor = foremanFetchedAt ? (Date.now()/1000 - foremanFetchedAt) : 0;
  if (staleFor > 30) {
    p.push('<span class="pill bad">the dashboard has no fresh data (' + Math.round(staleFor/60) + ' min old) - do not trust the state below' +
           (window._ferr ? ' · cause: ' + esc(window._ferr) : '') + '</span>');
    return p.join(' ');
  }
  if (!F.alive) p.push('<span class="pill bad"><span class="dot bad"></span>foreman not running</span>');
  else if (F.killswitch) {
    p.push('<span class="pill bad">kill switch ON — ' + esc(F.killswitch.reason || '') + '</span>');
    p.push((F.killswitch.scope === 'all' ? '<span class="pill bad">all projects</span>' : '') +
           (F.killswitch.hard ? '<span class="pill bad">hard — workers stopped</span>' : '') +
           '<button class="resume-btn" onclick="resumeDev()" id="resume-btn">Continue</button>');
  }
  else if (F.paused) p.push('<span class="pill warn">foreman paused</span>');
  else p.push('<span class="pill ok"><span class="dot running"></span>foreman running</span>');
  if (F.claude_until) p.push('<span class="pill warn">orchestrator: Claude via Antigravity (Claude Code back ' + tfmt(F.claude_until) + ')</span>');
  else p.push('<span class="pill ok">orchestrator: Claude Code</span>');
  const agyDown = Object.keys(F.agy_cooldown || {}).length;
  if (agyDown) p.push('<span class="pill warn">' + agyDown + ' worker(s) temporarily without a substitute orchestrator</span>');
  p.push('<span class="pill">autopilot ' + (F.autopilot ? 'on' : 'off') + '</span>');
  if (F.forge && (F.forge.kind === 'github' || F.forge.kind === 'gitlab')) p.push('<span class="pill">' + (F.forge.kind === 'github' ? 'GitHub' : 'GitLab') + ': ' + esc(F.forge.repo) + '</span>');
  if (F.api && (F.api.workers || []).length) p.push('<span class="pill' + (F.api.spent_today >= F.api.budget ? ' bad' : '') + '">API today $' + Number(F.api.spent_today).toFixed(2) + ' / $' + Number(F.api.budget).toFixed(0) + '</span>');
  if (F.old) p.push('<span class="pill warn">old foreman version — fewer details</span>');
  return p.join(' ');
}

function stats() {
  if (!F) return '';
  const t = F.today || {}, l = F.llm_today || {}, ts = F.tasks || [];
  const n = s => ts.filter(x => x.status === s).length;
  const box = (v, lab) => '<div class="stat"><b>' + v + '</b><span>' + lab + '</span></div>';
  const blocked = n('blocked');
  return box(n('running'), 'running') + box(n('pending'), 'waiting') + box(n('gate') + n('review'), 'checking') +
         box(t.merged != null ? t.merged : n('done'), 'done today') +
         (blocked ? box('<span style="color:var(--bad)">' + blocked + '</span>', 'blocked') : '') +
         (t.tokens ? box((t.tokens/1e6).toFixed(1) + 'M', 'tokens today') : '') +
         (l.cost_usd ? box('$' + l.cost_usd.toFixed(2), 'orchestrator today') : '');
}

function team() {
  const R = P.roster || {}, ts = (F && F.tasks) || [], live = (F && F.live) || {};
  const steps = (live.steps || []).map(s => {
    const [kind, id] = s.key.split(':');
    const t = ts.find(x => x.id === id), g = F && (F.goals || []).find(x => x.id === id);
    const what = s.phase || (kind === 'goal' ? 'planning' : kind === 'task' ? 'checking' : kind);
    const obj = t ? esc(t.id) : g ? '„' + esc(g.text.slice(0, 60)) + '“' : '';
    return '<div class="step"><span class="dot think"></span>' + esc(what) + (obj ? ' — ' + obj : '') + ' <span class="muted">(' + ago(s.since) + ')</span></div>';
  }).join('');
  let orch = '<div class="node orch"><div class="node-title"><span class="dot ' + (steps ? 'think' : '') + '"></span>Orchestrator (lead)</div>' +
    '<div class="node-sub">' + (F && F.claude_until ? 'Claude via Antigravity (substitute)' : 'Claude Code') + ' · plans, reviews, merges</div>' +
    (steps || '<div class="node-sub">idle — waiting for the workers</div>') +
    (live.unavailable ? '<div class="step" style="color:var(--warn)">waiting: ' + esc(live.unavailable) + '</div>' : '') + '</div>';
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
      esc(t.title) + '</div><div class="node-sub">' + esc(t.id) + ' · attempt ' + (t.attempts + 1) + '/' + (t.max_attempts || 4) + ' · running ' + ago(t.dispatched) +
      (e.tool_calls ? ' · ' + e.tool_calls + ' tools' : '') + '</div>'
      : '<div class="node-sub">' + (cool ? 'pauza do ' + tfmt(cool.until) + ': ' + esc(cool.reason || 'unknown reason') : (live.reserved || []).includes(w) ? 'lent to the orchestrator (Claude via Antigravity)' : 'free') + '</div>';
    const rt = (F && F.api && (F.api.workers || []).includes(w)) ? ((ts.find(x => x.worker === w && x.runtime) || {}).runtime || 'API') : '';
    return '<div class="node"><div class="node-title"><span class="dot ' + state + '"></span>' + esc(w) + (rt ? ' <span class="rt rt-' + esc(rt) + '">' + esc(RTNAME[rt] || rt) + '</span>' : '') +
      '</div>' + task + quota + '</div>';
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
  const st = {new:'planning', active:'running', done:'done', blocked:'blocked'}[g.status] || g.status;
  // acceptance criteria of the whole goal, checked by the orchestrator when all its tasks finished (B4)
  const acc = g.acceptance || [], cr = g.criteria || [];
  const crit = acc.length ? '<div class="crit">' + acc.map((a, i) => {
      const c = cr[i] || cr.find(x => x.criterion && x.criterion.slice(0, 40) === String(a).slice(0, 40));
      const mark = !c ? '<span class="cm wait" title="not checked yet">○</span>' : c.met ? '<span class="cm ok">✓</span>' : '<span class="cm no">✗</span>';
      return '<div class="critrow">' + mark + '<span>' + esc(a) + (c && c.evidence ? '<span class="muted"> — ' + esc(c.evidence) + '</span>' : '') + '</span></div>';
    }).join('') + (g.criteria_at ? '<div class="muted" style="font-size:11.5px">checked: ' + esc(dfmt(g.criteria_at)) + '</div>' : '') + '</div>' : '';
  const metCount = cr.filter(c => c.met).length;
  return '<div class="goal"><div class="goal-head"><div class="goal-text">' + esc(g.text) + '</div><div class="goal-meta">' + esc(st) +
    (mine.length ? ' · tasks ' + done + '/' + mine.length + ' (' + Math.round(100 * done / n) + ' %)' : '') +
    (acc.length ? ' · criteria ' + metCount + '/' + acc.length : '') + (g.rounds > 1 ? ' · round ' + g.rounds : '') +
    (lines ? ' · ' + lines + ' lines' : '') + (g.source && g.source !== 'owner' ? ' · ' + esc(g.source) : '') + '</div></div>' +
    '<div class="bar">' + seg + '</div>' + ((g.summary || g.notes) ? '<div class="goal-sum">' + esc(g.summary || g.notes) + '</div>' : '') + crit +
    '<div class="chips">' + mine.map(t => '<span class="chip" data-task="' + esc(t.id) + '">' + esc(STATUS[t.status] || t.status) + ' · ' + esc(t.id) +
      (t.diff ? ' · +' + t.diff.added + '/-' + t.diff.removed : '') + '</span>').join('') + '</div>' +
    (mine.length > 1 ? '<button class="linkbtn" data-dag="' + esc(g.id) + '">' + (openDags.has(g.id) ? 'hide dependency graph' : 'dependency graph') + '</button>' +
      (openDags.has(g.id) ? dagSvg(mine, ts) : '') : '') + '</div>';
}
function goalsHtml() {
  if (!F) return '';
  const ts = F.tasks || [];
  const gs = (F.goals || []).filter(g => g.status !== 'cancelled').slice().reverse();
  if (!gs.length) return '<div class="empty">No goals yet — enter the first one above under “New goal”.</div>';
  const open = gs.filter(g => g.status === 'new' || g.status === 'active' || g.status === 'blocked'), fin = gs.filter(g => !open.includes(g));
  return (open.map(g => goalCard(g, ts)).join('') || '<div class="empty">Nothing is running.</div>') +
    (fin.length ? '<details' + (openFin ? ' open' : '') + ' id="fin-goals"><summary class="muted">Finished goals (' + fin.length + ')</summary>' +
      fin.slice(0, 30).map(g => goalCard(g, ts)).join('') + '</details>' : '');
}
let openFin = false;
document.addEventListener('toggle', e => { if (e.target.id === 'fin-goals') openFin = e.target.open; }, true);

function boardHtml() {
  if (!F) return '';
  const ts = F.tasks || [];
  const cols = [['pending', 'Waiting'], ['running', 'Running'], ['gate', 'Checking (tests + review)'], ['done', 'Done'], ['blocked', 'Blocked / cancelled']];
  const inCol = (t, s) => t.status === s || (s === 'gate' && (t.status === 'review' || t.status === 'merging')) || (s === 'pending' && t.status === 'splitting');
  return cols.map(([s, lab]) => {
    let items = ts.filter(t => inCol(t, s) || (s === 'blocked' && t.status === 'cancelled'));
    if (s === 'done') items = items.slice().sort((a, b) => (b.finished || '').localeCompare(a.finished || '')).slice(0, 15);
    const phase = id => { const st = ((F.live || {}).steps || []).find(x => x.key === 'task:' + id); return st ? st.phase : ''; };
    return '<div class="col"><h4>' + lab + '<span class="muted">' + items.length + '</span></h4>' + (items.map(t => {
      const deps = (t.depends_on || []).filter(d => !ts.find(x => x.id === d && x.status === 'done'));
      return '<div class="card s-' + t.status + '" data-task="' + esc(t.id) + '"><div class="id">' + esc(t.id) + '</div><div class="t">' + esc(t.title) + '</div><div class="m">' +
        '<span class="role ' + roleCls(t.role) + '">' + esc(t.role) + '</span>' + (t.worker && s !== 'pending' ? '<span>' + esc(t.worker) + '</span>' : '') +
        (t.attempts ? '<span style="color:var(--warn)">attempt ' + (t.attempts + 1) + '/' + (t.max_attempts || 4) + '</span>' : '') +
        (t.pr ? '<span>PR #' + t.pr + '</span>' : '') + (deps.length && s === 'pending' ? '<span>waits for ' + deps.map(esc).join(', ') + '</span>' : '') +
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
  const html = '<button class="close">Close</button><h2 class="task-title">' + esc(t.title) + '</h2>' +
    '<div class="muted" style="font-size:12.5px">' + esc(t.id) + ' · <span class="role ' + roleCls(t.role) + '">' + esc(t.role) + '</span> · ' +
    esc(STATUS[t.status] || t.status) + ' · attempt ' + (t.attempts + 1) + '/' + (t.max_attempts || 4) + (t.worker ? ' · ' + esc(t.worker) : '') +
    (t.pr_url ? ' · <a href="' + esc(t.pr_url) + '" target="_blank" rel="noopener">PR #' + esc(t.pr || '') + '</a>' : t.pr ? ' · PR #' + t.pr : '') +
    (t.runtime && t.runtime !== 'agy' ? ' · ' + esc(RTNAME[t.runtime] || t.runtime) : '') + (t.risk ? ' · risk: ' + esc(t.risk) : '') + (t.run_tokens ? ' · ' + (t.run_tokens/1000).toFixed(0) + 'k tokens' : '') +
    (t.diff ? ' · <span style="color:var(--ok)">+' + t.diff.added + '</span> <span style="color:var(--bad)">-' + t.diff.removed + '</span> lines' : '') +
    (t.total_lines != null ? ' · file(s) total ' + t.total_lines + ' lines' : '') + '</div>' +
    (g ? '<h3>Goal</h3><div style="font-size:13px">' + esc(g.text) + '</div>' : '') +
    (t.blocked_why ? '<h3 style="color:var(--bad)">Why blocked</h3><pre>' + esc(t.blocked_why) + '</pre>' : '') +
    (t.feedback && t.status !== 'done' ? '<h3>Latest feedback to the worker</h3><pre>' + esc(t.feedback) + '</pre>' : '') +
    (hist ? '<h3>History</h3>' + hist : '') +
    ((t.acceptance || []).length ? '<h3>Acceptance criteria</h3><ul style="font-size:13px;margin:0;padding-left:18px">' + t.acceptance.map(a => '<li>' + esc(a) + '</li>').join('') + '</ul>' : '') +
    ((t.files || []).length ? '<h3>Files</h3><div class="muted" style="font-size:12.5px;font-family:ui-monospace,monospace">' + t.files.map(esc).join('<br>') + '</div>' : '') +
    ((t.mutations || []).length ? '<h3>Mutation tests</h3><pre>' + esc(t.mutations.join('\n')) + '</pre>' : '') +
    (t.diff || t.pr ? '<h3>Changes (diff)</h3>' + (diffCache[t.id] == null
        ? '<button class="linkbtn" data-diff="' + esc(t.id) + '">' + (diffLoading.has(t.id) ? 'loading…' : 'show diff') + '</button>'
        : diffHtml(diffCache[t.id])) : '') +
    (t.spec ? '<h3>Spec</h3><pre>' + esc(t.spec) + '</pre>' : '');
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
    return '<div class="ev ' + cls + '"><time>' + esc(dfmt(e.at)) + '</time><div>' + esc(m) + '</div></div>'; }).join('') || '<div class="empty">Nothing yet.</div>';
}

const paneEls = {};
function liveHtml() {
  const grid = document.getElementById('grid');
  const panes = P.panes || [];
  document.getElementById('c-live').textContent = panes.length;
  if (tab !== 'live') return;
  if (!panes.length) { setHTML('grid', '<div class="empty">No AI is running right now — the transcript appears when one runs.</div>'); for (const k in paneEls) delete paneEls[k]; return; }
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
  if (!F || !F.timeline) return '<div class="empty">The timeline needs a newer foreman.</div>';
  const now = Date.now() / 1000, span = 24 * 3600, t0 = now - span, W = 1000, LW = 90, lane = 30;
  const runs = F.timeline;
  const ws = [...new Set(runs.map(r => r.worker).concat(Object.keys((P && P.roster) || {}).filter(k => k.startsWith('worker:')).map(k => k.slice(7))))].filter(Boolean).sort();
  if (!ws.length) return '<div class="empty">No runs in the last 24 h.</div>';
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
      const tip = esc(r.task) + ' · ' + esc(r.reason) + ' · ' + Math.round((e0 - s0) / 60) + ' min · ' + Math.round((r.tokens || 0) / 1000) + 'k tokens';
      svg += '<g data-task="' + esc(r.task.replace(/\.md$/, '')) + '"><title>' + tip + '</title><rect class="run" x="' + x(s0) + '" y="' + (y + 5) + '" width="' +
             Math.max(2, x(e0) - x(s0)) + '" height="' + (lane - 10) + '" rx="3" fill="' + (RUNCOL[r.reason] || 'var(--idle)') + '"' +
             (r.reason === 'running' ? ' opacity=".8"><animate attributeName="opacity" values=".5;1;.5" dur="2s" repeatCount="indefinite"/></rect>' : '/>') + '</g>';
    }
    util[w] = Math.round(100 * busy / span);
  });
  svg += '</svg>';
  const legend = '<div class="legend">' + [['ok','done'],['running','running'],['token-cap','token cap / time'],['failed','failed'],['killswitch','kill switch']]
    .map(([k, l]) => '<span><i style="background:' + RUNCOL[k] + '"></i>' + l + '</span>').join('') + '</div>';
  const u = '<div class="legend">' + ws.map(w => '<span>' + esc(w) + ': <b style="color:var(--text)">' + util[w] + ' %</b> of the time working</span>').join('') + '</div>';
  return '<h3>Last 24 hours</h3>' + legend + '<div class="tablewrap" style="padding:8px">' + svg + '</div>' + u +
         '<div class="muted" style="font-size:12px;margin-top:6px">Gaps = the worker was idle. Click a block to open its task.</div>';
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
  catch (e) { diffCache[id] = '(could not load: ' + e + ')'; }
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
  if (!M) return '<div class="empty">Metrics need a newer foreman.</div>';
  const T = M.totals || {}, box = (v, lab) => '<div class="stat"><b>' + (v == null ? '–' : v) + '</b><span>' + lab + '</span></div>';
  let h = '<div class="stats">' + box(T.done, 'tasks done') + box(T.first_pass_pct == null ? null : T.first_pass_pct + ' %', 'approved first time') +
          box(T.reworks, 'sent back') + box(T.blocked, 'blocked') + box(T.tokens_per_done ? Math.round(T.tokens_per_done / 1000) + 'k' : null, 'tokens per done task') + '</div>';
  const days = M.merged_per_day || [], mx = Math.max(1, ...days.map(d => d[1]));
  h += '<h3>Tasks done per day</h3><div class="bars">' + (days.map(([d, n]) => '<div><span>' + n + '</span><b style="height:' + (100 * n / mx) + '%"></b><span>' + d.slice(5) + '</span></div>').join('') || '<span class="muted">nothing yet</span>') + '</div>';
  const roles = Object.entries(M.roles || {}).sort((a, b) => b[1].tasks - a[1].tasks);
  h += '<h3>By role</h3><div class="tablewrap"><table class="m"><tr><th>role</th><th class="num">tasks</th><th class="num">done</th><th class="num">1st try</th><th class="num">sent back</th><th class="num">blocked</th><th class="num">tokens/task</th><th class="num">lines</th></tr>' +
       roles.map(([r, v]) => '<tr><td>' + esc(r) + '</td><td class="num">' + v.tasks + '</td><td class="num">' + v.done + '</td><td class="num">' + (v.first_pass_pct == null ? '–' : v.first_pass_pct + ' %') +
         '</td><td class="num">' + v.reworks + '</td><td class="num">' + v.blocked + '</td><td class="num">' + (v.tokens_per_done ? Math.round(v.tokens_per_done / 1000) + 'k' : '–') + '</td><td class="num">' + v.lines_done + '</td></tr>').join('') + '</table></div>';
  const E = M.efficiency || {};
  const effTable = (title, rows, note) => rows.length ? '<h3>' + title + '</h3>' + (note ? '<div class="muted" style="font-size:12.5px">' + note + '</div>' : '') +
    '<div class="tablewrap"><table class="m"><tr><th></th><th class="num">runs</th><th class="num">ok</th><th class="num">cap</th><th class="num">quota/run</th><th class="num">quota/done task</th>' +
    '<th class="num">tools/run</th><th class="num">tokens/run</th><th class="num">done</th><th class="num">1st try</th></tr>' +
    rows.map(([k, v]) => '<tr><td>' + esc(k) + '</td><td class="num">' + v.runs + '</td><td class="num">' + v.ok_pct + ' %</td><td class="num">' + v.cap_pct + ' %</td><td class="num">' +
      (v.week_per_run == null ? '–' : v.week_per_run + ' %') + '</td><td class="num"><b>' + (v.week_per_done == null ? '–' : v.week_per_done + ' %') + '</b></td><td class="num">' +
      (v.tools_median == null ? '–' : v.tools_median) + '</td><td class="num">' + (v.tokens_median ? Math.round(v.tokens_median / 1000) + 'k' : '–') + '</td><td class="num">' + v.done +
      '</td><td class="num">' + (v.first_pass_pct == null ? '–' : v.first_pass_pct + ' %') + '</td></tr>').join('') + '</table></div>' : '';
  h += effTable('Efficiency by worker model', Object.entries(E.model || {}).sort((a, b) => b[1].runs - a[1].runs),
                'quota = % of one account\'s weekly limit (agy /usage before and after the run); lower is better');
  h += effTable('Context pack — A/B', Object.entries(E.pack || {}).sort((a, b) => b[1].runs - a[1].runs),
                'with CONTEXT_PACK=ab half the tasks get the pack; compare quota per done task and tools per run');
  const SC = E.self_check;
  if (SC && SC.runs) h += '<h3>Self-check in the run</h3><div class="stats">' + box(SC.runs, 'runs with a self-check') + box(SC.green_first, 'green at once') +
    box(SC.fixed, 'fixed in the same run') + box(SC.still_red, 'stayed red') + '</div>';
  const MT = F.maintenance;
  if (MT) h += '<h3>Maintenance of main (no AI)</h3><div class="muted maint" style="font-size:13px">' + esc(dfmt(MT.at)) + ' · ' + esc(MT.sha || '') + ' · testy: ' +
    (MT.suite || []).map(r => (r.rc === 0 ? '✓ ' : '✗ ') + esc(r.summary)).join(' / ') + (MT.nodoc ? ' · modulov bez docstringu: ' + MT.nodoc : '') + '</div>';
  const prov = Object.entries(M.providers || {}).sort((a, b) => b[1].calls - a[1].calls);
  h += '<h3>Orchestrator, last 7 days (who planned and reviewed)</h3><div class="tablewrap"><table class="m"><tr><th>provider</th><th class="num">calls</th><th class="num">failed</th><th class="num">average</th></tr>' +
       prov.map(([p, v]) => '<tr><td>' + esc(p) + '</td><td class="num">' + v.calls + '</td><td class="num">' + v.failed + '</td><td class="num">' + (v.calls ? Math.round(v.secs / v.calls) + ' s' : '–') + '</td></tr>').join('') + '</table></div>';
  return h;
}

// ---- 10. ask the team
async function askTeam() {
  const q = document.getElementById('ask-q').value.trim(), btn = document.getElementById('ask-btn'), out = document.getElementById('ask-a');
  if (!q) return;
  btn.disabled = true; btn.textContent = 'The orchestrator is thinking… (1–2 min)';
  let r;
  try { r = await (await fetch('/api/ask', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({question: q, project})})).json(); }
  catch (e) { r = {answer: '(connection error: ' + e + ')'}; }
  btn.disabled = false; btn.textContent = 'Ask';
  out.innerHTML = '<div class="answer"><b>' + esc(q) + '</b>\n\n' + esc(r.answer || r.error || '(no answer)') + '</div>' + out.innerHTML;
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
        '</span><span class="blbtns">' + (i ? '<button class="mini" data-bl="move" data-n="' + (i + 1) + '" data-to="' + i + '" title="up">↑</button>' : '') +
        (i < bl.length - 1 ? '<button class="mini" data-bl="move" data-n="' + (i + 1) + '" data-to="' + (i + 2) + '" title="down">↓</button>' : '') +
        '<button class="mini" data-bl="drop" data-n="' + (i + 1) + '" title="remove">✕</button></span></div>').join('')
      : '<div class="empty">Empty' + (F && F.autopilot ? ' — when a worker is free, the autopilot picks the next goal from the vision.' : '.') + '</div>', force);
    const qs = (F && F.owner_questions) || [];
    if (!document.getElementById('questions').contains(document.activeElement))
      setHTML('questions', qs.length ? '<h3>The planner\'s questions (' + qs.length + ')</h3>' + qs.map((q, i) => '<div class="goal qrow"><div class="qtext">' + esc(q) +
        '</div><div class="btnrow"><input type="text" class="qin" data-qn="' + (i + 1) + '" placeholder="your answer (the planner will follow it)">' +
        '<button class="mini" data-qa="' + (i + 1) + '">Answer</button><button class="mini" data-qd="' + (i + 1) + '" title="the question does not matter">Dismiss</button></div></div>').join('') : '', force);
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
                     (p.repo ? ' · ' + esc(p.repo) : '') + '</option>').join('') + '<option value="__new">+ New project…</option>';
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
      ' <span class="muted">(' + esc(owner[w] || (st.values && !st.values.WORKERS ? 'default — all' : 'free')) + ')</span></label>').join(' ')
    : 'no workers — add them in Settings';
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
  while (j.state === 'running') { log.textContent = (j.log || '') + '\n… working'; await new Promise(r => setTimeout(r, 2500));
    j = await (await fetch('/api/jobs/' + encodeURIComponent(j.id))).json(); }
  log.textContent = (j.log || '') + '\n' + ({done: '✔ The project is ready — pick it at the top and enter its first goal.',
    'needs-sudo': '⚠ The project exists; only its service needs root — run the command above.'}[j.state] || '✘ Failed — see the output above.');
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
  if (F && F.forge && F.forge.setup_needed && !setupDismissed[project] && !document.getElementById('setup').classList.contains('on')) {
    setupDismissed[project] = true; if (!S) await loadSettings(true); openSetup(); }
  render();
}
function showJsError(msg) {
  const el = document.getElementById('jserr');
  if (el) { el.style.display = 'block'; el.textContent = 'Page error: ' + msg; }
}
window.addEventListener('error', e => showJsError((e.message || 'unknown') + ' (' + (e.lineno || '?') + ')'));
window.addEventListener('unhandledrejection', e => showJsError(String(e.reason && e.reason.message || e.reason)));
setTab(tab);
if (HASH.task && /^\d{4}-[\w-]+$/.test(HASH.task)) { openTask = HASH.task; }
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
                errs["name"] = "lowercase letters, digits and dashes (not 'default')"
            elif any(p.get("name") == name for p in PROJECTS["list"]):
                errs["name"] = "the project already exists"
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}/[A-Za-z0-9_.-]{1,60}", repo):
                errs["repo"] = "organisation/repository, e.g. acme/shop"
            if not all(re.fullmatch(r"worker-\d+", w) for w in ws):
                errs["workers"] = "invalid worker"
            if not re.fullmatch(r"[A-Za-z][A-Za-z ()-]{0,30}", lang):
                errs["lang"] = "a language name, e.g. English"
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
            rt = str(body.get("runtime") or "agy")
            if body.get("action") != "add" or rt not in ("agy", "claude", "codex", "gemini"):
                self._json({"error": "action add, runtime agy|claude|codex|gemini"}, 400); return
            self._json(start_job("add-worker", lambda job: add_worker_job(job, rt)))
        elif self.path == "/api/secret":
            # an API key or forge token typed by the owner: goes to bin/secret-set on stdin, never into a command line or a log
            name, value = str(body.get("name", "")), str(body.get("value", "")).strip()
            if name not in ("anthropic.key", "openai.key", "gemini.key", "github.token", "gitlab.token", "notify.token") or not value:
                self._json({"error": "unknown secret name or empty value"}, 400); return
            rc, out, err = ssh_run_full(f"cd {FORGE_DIR} && bin/secret-set {name}", timeout=30, inp=value)
            self._json({"ok": rc == 0, "message": (out or err).strip()[-200:]})
        elif self.path == "/api/upstream":
            # where the project's code lives: internal Forgejo only, or GitHub / GitLab (bin/upstream, bin/lib/upstream.py)
            act, kind = str(body.get("action", "")), str(body.get("kind", ""))
            if act == "check":
                rc, out, err = ssh_run_full(f"cd {FORGE_DIR} && bin/upstream{pf} check", timeout=60)
                self._json({"ok": rc == 0, "message": (out + err).strip()[-400:]}); return
            if act != "connect" or kind not in ("forgejo", "github", "gitlab"):
                self._json({"error": "action check | connect, kind forgejo | github | gitlab"}, 400); return
            repo, url = str(body.get("repo", "")).strip(), str(body.get("url", "")).strip().rstrip("/")
            if kind != "forgejo" and (not re.fullmatch(r"[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+){1,4}", repo)
                                      or not re.fullmatch(r"https?://[A-Za-z0-9.-]+(:\d+)?(/[A-Za-z0-9._~/-]*)?", url)):
                self._json({"ok": False, "errors": {"repo": "owner/repository (GitLab: group/project)", "url": "https://github.com, https://gitlab.com or your server"}}); return
            changes = {"FORGE_KIND": kind} if kind == "forgejo" else {"FORGE_KIND": kind, "UPSTREAM_REPO": repo, "UPSTREAM_URL": url}
            self._json(start_job("connect-upstream", lambda job: connect_upstream_job(job, pf, changes, kind != "forgejo",
                                                                                         bool(body.get("force")))))
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
            raw = (out if rc == 0 else f"(error: {err.strip()[-300:]})").encode()
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
