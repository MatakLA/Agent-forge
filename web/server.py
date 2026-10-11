#!/usr/bin/env python3
"""Agent-Forge web dashboard and admin console: the team live (orchestrator, every worker), goals with their acceptance criteria,
the task board and dependency graph, run replays, timeline, money and quota, metrics, health, egress and disk; and the owner's
controls - goals and backlog, approvals, task and goal actions, answers to the planner's questions, kill switch per project or
for all, settings, the team's documents, new workers with their Antigravity login in a browser terminal, and new projects.

Python standard library only: this file is the HTTP side, web/dashboard/ the backend, web/static/ the page. Runs next to
bin/foreman (LOCAL mode, default) or on another machine reading it over ssh (REMOTE mode: set VM_IP, VM_USER, VM_KEY,
VM_KNOWN_HOSTS, and FORGE_DIR if Agent-Forge is not in ~/sandbox there). Binds 127.0.0.1 only - publish it with Tailscale serve
or a reverse proxy (then list its host name in DASHBOARD_HOSTS) and keep it private: it is an admin console. Every write goes
through bin/foreman / bin/killswitch / bin/worker-add / ..., which validate their input; owner text always travels on stdin.
Live updates are pushed with Server-Sent Events (/api/stream): the foreman's state as deltas, new transcript lines only.

Config (environment): BIND_PORT (8171), FORGE_DIR, VM_IP / VM_USER / VM_KEY / VM_KNOWN_HOSTS for remote mode, DASHBOARD_HOSTS,
DASHBOARD_TOKEN (optional sign-in), FOREMAN_SERVICE / FOREMAN_SERVICE_PROJECT (systemd unit names), CLAUDE_PROJECT.
"""
import atexit, hmac, json, os, re, shlex, signal, sys, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WEB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WEB)
from dashboard import backend as B, live as L  # noqa: E402


def shutdown(*_a):
    with L.PANES_LOCK:
        for p in list(L.PANES.values()):
            p.stop()
    for proc in list(B.ALL_PROCS):
        try:
            proc.terminate()
        except Exception:
            pass
    os._exit(0)


# ---- access control. The dashboard can stop projects, change settings and drive a worker's login, so:
#  - Host must be this machine (127.0.0.1 / localhost), a Tailscale name (*.ts.net) or one listed in DASHBOARD_HOSTS - this
#    defeats DNS rebinding (a web page whose domain suddenly resolves to 127.0.0.1);
#  - every POST must carry the header X-Agent-Forge (the page's own fetch adds it). A custom header makes the browser ask
#    permission first (CORS preflight), which this server never grants - so no other web site can POST here (CSRF);
#  - the page cannot be framed by another site (clickjacking) and runs scripts from this server only (CSP, no inline code);
#  - optional DASHBOARD_TOKEN: open the page once as /?token=<token>; a SameSite=Strict, HttpOnly cookie keeps you signed in.
DASHBOARD_HOSTS = {h.strip().lower() for h in os.environ.get("DASHBOARD_HOSTS", "").split(",") if h.strip()}
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")
SEC_HEADERS = {"X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
               "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                          "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}
TASK_ID = re.compile(r"\d{4}-[\w-]{1,60}")
GOAL_ID = re.compile(r"G\d{8}-\d{6}(-[\w-]{1,30})?")
DOC_NAME = re.compile(r"vision|team-rules|decisions|lessons|role:[a-z][a-z0-9-]{1,40}")
HOST = re.compile(r"[a-z0-9][a-z0-9.-]{2,252}")          # bin/lib/egress.py check_host() decides the rest
TARGET = re.compile(r"|worker-\d+|[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,99}")
TASK_ACTIONS = ("approve", "cancel", "retry", "feedback", "assign", "model")
GOAL_ACTIONS = ("approve", "replan", "pause", "resume", "prioritize", "edit", "cancel")


def load_static():
    """web/static/ read once at start: {relative path: (bytes, content type)}. Only these files are ever served (no path from the
    request touches the file system). index.html gets a version stamp so a browser fetches changed scripts after an update."""
    types = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8",
             ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json", ".webmanifest": "application/manifest+json"}
    root, out = os.path.join(WEB, "static"), {}
    for d, _, fs in os.walk(root):
        for f in fs:
            ext = os.path.splitext(f)[1]
            if ext in types:
                rel = os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/")
                out[rel] = (open(os.path.join(d, f), "rb").read(), types[ext])
    import hashlib
    v = hashlib.sha1(b"".join(out[k][0] for k in sorted(out) if k != "index.html")).hexdigest()[:10]
    if "index.html" in out:
        out["index.html"] = (out["index.html"][0].replace(b"{{V}}", v.encode()), out["index.html"][1])
    return out


STATIC = load_static()


def host_allowed(host):
    host = (host or "").strip().lower()
    if not host:
        return False
    if host.startswith("["):
        if "]" not in host:
            return False
        name, _, rest = host.partition("]")
        name = name + "]"
        if rest and (not rest.startswith(":") or not rest[1:].isdigit() or len(rest) == 1):
            return False
    elif ":" in host:
        name, port = host.rsplit(":", 1)
        if not port or not port.isdigit():
            return False
    else:
        name = host
    if not name:
        return False
    return (name in ("127.0.0.1", "localhost", "[::1]")
            or (name.endswith(".ts.net") and len(name) > 7 and not name.startswith("."))
            or name in DASHBOARD_HOSTS or host in DASHBOARD_HOSTS)


def token_ok(cookie_header, query_token=""):
    if not DASHBOARD_TOKEN:
        return True
    qtok = str(query_token or "").strip()
    if qtok and hmac.compare_digest(qtok, DASHBOARD_TOKEN):
        return True
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k.strip() == "af_token" and hmac.compare_digest(v.strip().strip('"'), DASHBOARD_TOKEN):
            return True
    return False


def foreman(pf, args, inp=None, timeout=60):
    """bin/foreman for a project ("" or " --project NAME"): (rc, stdout, stderr). Owner text only ever as `inp` (stdin)."""
    return B.ssh_run_full(f"cd {B.FORGE_DIR} && bin/foreman{pf} {args}", timeout=timeout, inp=inp)


def as_json(out, err, rc):
    try:
        return json.loads(out)
    except ValueError:
        msg = (err or out or f"exit {rc}").strip()[-400:]
        if DASHBOARD_TOKEN:
            msg = msg.replace(DASHBOARD_TOKEN, "[REDACTED]")
        return {"ok": False, "error": msg}


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
        if DASHBOARD_TOKEN:
            msg = msg.replace(DASHBOARD_TOKEN, "[REDACTED]")
        raw = msg.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _allowed(self, post=False):
        if not host_allowed(self.headers.get("Host")):
            self._deny(403, "host not allowed (set DASHBOARD_HOSTS to add your reverse proxy's name)"); return False
        qtok = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("token", [""])[0]
        if not token_ok(self.headers.get("Cookie"), qtok):
            self._deny(401, "sign in: open this page as /?token=<DASHBOARD_TOKEN>"); return False
        if post:
            if self.headers.get("X-Agent-Forge") != "1":
                self._deny(403, "missing X-Agent-Forge header"); return False
            origin = self.headers.get("Origin")
            if origin is not None:
                origin = origin.strip()
                if origin == "null" or not origin:
                    self._deny(403, "origin not allowed"); return False
                o_host = urllib.parse.urlsplit(origin).netloc if "://" in origin else origin
                if not host_allowed(o_host):
                    self._deny(403, "origin not allowed"); return False
            referer = self.headers.get("Referer")
            if referer is not None:
                referer = referer.strip()
                if referer == "null" or not referer:
                    self._deny(403, "referer not allowed"); return False
                r_host = urllib.parse.urlsplit(referer).netloc if "://" in referer else referer
                if not host_allowed(r_host):
                    self._deny(403, "referer not allowed"); return False
        return True

    def _json(self, obj, code=200):
        raw = json.dumps(obj).encode()
        if DASHBOARD_TOKEN:
            raw = raw.replace(DASHBOARD_TOKEN.encode(), b"[REDACTED]")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _raw(self, raw, ctype, cache="no-store"):
        if DASHBOARD_TOKEN and isinstance(raw, (bytes, bytearray)):
            raw = raw.replace(DASHBOARD_TOKEN.encode(), b"[REDACTED]")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        try:
            n = min(int(self.headers.get("Content-Length", "0")), 300000)
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def _query(self):
        return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}

    def _service(self, proj, pf):
        return B.FOREMAN_SERVICE_PROJECT.format(project=proj) if pf else B.FOREMAN_SERVICE

    # ------------------------------------------------------------------------------------------------------- POST: writes
    def do_POST(self):
        if not self._allowed(post=True):
            return
        body = self._body()
        proj = str(body.get("project", "default"))
        try:
            pf = B.pflag(proj)
        except ValueError:
            self._json({"error": "bad project"}, 400); return
        penv = f"FORGE_PROJECT={proj} " if pf else ""
        path = self.path.split("?")[0]
        if path == "/api/ask":
            q = str(body.get("question", "")).strip()[:4000]
            if not q:
                self._json({"error": "empty question"}, 400); return
            if not B.ASK_LOCK.acquire(blocking=False):
                self._json({"error": "another question is being answered - wait for it"}, 429); return
            try:
                rc, out, err = foreman(pf, "ask", inp=q, timeout=900)
            finally:
                B.ASK_LOCK.release()
            self._json({"answer": out.strip() or (err.strip()[-500:] if rc else "(empty answer)")})
        elif path in ("/api/killswitch", "/api/resume"):
            action = "off" if path == "/api/resume" else str(body.get("action"))
            scope = "--all " if body.get("all") else ""
            steps = []

            def run(label, cmd, timeout=40):
                rc, out, err = B.ssh_run_full(cmd, timeout=timeout)
                steps.append({"label": label, "ok": rc == 0, "output": (out or err or "").strip()[-600:]})
                return rc == 0
            if action == "on":
                ok = run("kill switch on" + (" (hard)" if body.get("hard") else ""),
                         f"cd {B.FORGE_DIR} && {penv}bin/killswitch {scope}on{' --hard' if body.get('hard') else ''} --reason dashboard", timeout=90)
            elif action == "off":
                run("acknowledge egress alerts", f"cd {B.FORGE_DIR} && bin/egress-report --ack", timeout=30)
                ok = run("kill switch off (+ start stopped containers)", f"cd {B.FORGE_DIR} && {penv}bin/killswitch {scope}off --start", timeout=120)
                svc = self._service(proj, pf)
                rc, active, _ = B.ssh_run_full(f"systemctl is-active {svc}", timeout=10)
                if active.strip() != "active":
                    run(f"restart {svc}", f"sudo systemctl restart {svc}", timeout=60)
            else:
                self._json({"error": "action must be on or off"}, 400); return
            self._json({"ok": ok, "steps": steps})
        elif path == "/api/settings":
            if body.get("refresh_models"):
                rc, out, err = foreman(pf, "settings --refresh-models", timeout=200)
                try:
                    self._json(json.loads(out) if rc == 0 else {"error": err[-300:]})
                except ValueError:
                    self._json({"error": (err or out)[-300:]})
                return
            changes = body.get("changes") or {}
            if not isinstance(changes, dict) or not all(isinstance(k, str) and isinstance(v, (str, int)) for k, v in changes.items()):
                self._json({"error": "changes must be an object of strings"}, 400); return
            rc, out, err = foreman(pf, "settings apply", inp=json.dumps({k: str(v) for k, v in changes.items()}))
            try:
                self._json(json.loads(out))
            except Exception:
                self._json({"ok": False, "errors": {"_": (err or out)[-300:]}})
        elif path == "/api/projects":
            if body.get("action") != "create":
                self._json({"error": "unknown action"}, 400); return
            name, repo = str(body.get("name", "")).strip(), str(body.get("repo", "")).strip()
            lang = str(body.get("lang", "English")).strip() or "English"
            ws = [str(w) for w in body.get("workers") or []]
            errs = {}
            if not B.PROJECT_RE.fullmatch(name) or name == "default":
                errs["name"] = "lowercase letters, digits and dashes (not 'default')"
            elif any(p.get("name") == name for p in B.PROJECTS["list"]):
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
            self._json(B.start_job("create-project", lambda job: B.create_project_job(job, name, repo, ws, lang, vision)))
        elif path == "/api/goal":
            # a NEW goal; the owner's text goes to bin/foreman on stdin, never into the command line
            text, mode = str(body.get("text", "")).strip()[:6000], str(body.get("mode", "now"))
            if not text:
                self._json({"error": "empty goal"}, 400); return
            cmd = {"now": "goal -", "backlog": "backlog add -", "roadmap": "project -"}.get(mode)
            if not cmd:
                self._json({"error": "mode must be now, backlog or roadmap"}, 400); return
            rc, out, err = foreman(pf, cmd, inp=text, timeout=30)
            self._json({"ok": rc == 0, "message": (out or err).strip()[-400:]})
        elif path == "/api/backlog":
            act, n, to = str(body.get("action", "")), str(body.get("n", "")), str(body.get("to", ""))
            if act not in ("drop", "move", "top", "edit") or not n.isdigit() or (act == "move" and not to.isdigit()):
                self._json({"error": "action drop|top <n> | move <n> <to> | edit <n> (text)"}, 400); return
            text = str(body.get("text", "")).strip()[:4000]
            if act == "edit" and not text:
                self._json({"error": "empty text"}, 400); return
            args = f"backlog {act} {int(n)}" + (f" {int(to)}" if act == "move" else " -" if act == "edit" else "")
            rc, out, err = foreman(pf, args, inp=text if act == "edit" else None, timeout=30)
            self._json({"ok": rc == 0, "message": (err or "").strip()[-300:]})
        elif path == "/api/answer":
            n = str(body.get("n", ""))
            if not n.isdigit():
                self._json({"error": "bad question number"}, 400); return
            rc, out, err = foreman(pf, f"answer {int(n)}", inp=str(body.get("answer", ""))[:4000], timeout=30)
            self._json({"ok": rc == 0, "message": (out or err).strip()[-300:]})
        elif path == "/api/task":
            # the owner's action on one task: approve / cancel / retry / feedback (text on stdin) / assign / model
            tid, act = str(body.get("id", "")), str(body.get("action", ""))
            target = str(body.get("target", "") or "")
            if not TASK_ID.fullmatch(tid) or act not in TASK_ACTIONS or not TARGET.fullmatch(target):
                self._json({"ok": False, "message": "bad task, action or target"}, 400); return
            if act == "feedback":
                rc, out, err = foreman(pf, f"task {tid} feedback -", inp=str(body.get("text", ""))[:3000], timeout=30)
            elif act in ("assign", "model"):
                rc, out, err = foreman(pf, f"task {tid} {act} {shlex.quote(target or 'any')}", timeout=30)
            else:
                rc, out, err = foreman(pf, f"task {tid} {act}", timeout=30)
            self._json(as_json(out, err, rc))
        elif path == "/api/goalctl":
            gid, act = str(body.get("id", "")), str(body.get("action", ""))
            if not GOAL_ID.fullmatch(gid) or act not in GOAL_ACTIONS:
                self._json({"ok": False, "message": "bad goal or action"}, 400); return
            if act in ("replan", "edit"):
                rc, out, err = foreman(pf, f"goal {gid} {act} -", inp=str(body.get("text", ""))[:4000], timeout=30)
            else:
                rc, out, err = foreman(pf, f"goal {gid} {act}", timeout=30)
            self._json(as_json(out, err, rc))
        elif path == "/api/doc":
            name = str(body.get("name", ""))
            if not DOC_NAME.fullmatch(name):
                self._json({"ok": False, "message": "unknown document"}, 400); return
            if body.get("reset"):
                rc, out, err = foreman(pf, f"doc reset {name}", timeout=30)
            else:
                rc, out, err = foreman(pf, f"doc set {name}", inp=str(body.get("text", ""))[:200000], timeout=30)
            self._json(as_json(out, err, rc))
        elif path == "/api/workers":
            rt = str(body.get("runtime") or "agy")
            if body.get("action") != "add" or rt not in ("agy", "claude", "codex", "gemini", "compat", "aider"):
                self._json({"error": "action add, runtime agy|claude|codex|gemini|compat|aider"}, 400); return
            self._json(B.start_job("add-worker", lambda job: B.add_worker_job(job, rt)))
        elif path == "/api/secret":
            # an API key or forge token typed by the owner: goes to bin/secret-set on stdin, never into a command line or a log
            name, value = str(body.get("name", "")), str(body.get("value", "")).strip()
            if name not in ("anthropic.key", "openai.key", "gemini.key", "compat.key", "github.token", "gitlab.token", "notify.token") or not value:
                self._json({"error": "unknown secret name or empty value"}, 400); return
            rc, out, err = B.ssh_run_full(f"cd {B.FORGE_DIR} && bin/secret-set {name}", timeout=30, inp=value)
            self._json({"ok": rc == 0, "message": (out or err).strip()[-200:]})
        elif path == "/api/upstream":
            # where the project's code lives: internal Forgejo only, or GitHub / GitLab (bin/upstream, bin/lib/upstream.py)
            act, kind = str(body.get("action", "")), str(body.get("kind", ""))
            if act == "check":
                rc, out, err = B.ssh_run_full(f"cd {B.FORGE_DIR} && bin/upstream{pf} check", timeout=60)
                self._json({"ok": rc == 0, "message": (out + err).strip()[-400:]}); return
            if act != "connect" or kind not in ("forgejo", "github", "gitlab"):
                self._json({"error": "action check | connect, kind forgejo | github | gitlab"}, 400); return
            repo, url = str(body.get("repo", "")).strip(), str(body.get("url", "")).strip().rstrip("/")
            if kind != "forgejo" and (not re.fullmatch(r"[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+){1,4}", repo)
                                      or not re.fullmatch(r"https?://[A-Za-z0-9.-]+(:\d+)?(/[A-Za-z0-9._~/-]*)?", url)):
                self._json({"ok": False, "errors": {"repo": "owner/repository (GitLab: group/project)", "url": "https://github.com, https://gitlab.com or your server"}}); return
            changes = {"FORGE_KIND": kind} if kind == "forgejo" else {"FORGE_KIND": kind, "UPSTREAM_REPO": repo, "UPSTREAM_URL": url}
            self._json(B.start_job("connect-upstream", lambda job: B.connect_upstream_job(job, pf, changes, kind != "forgejo",
                                                                                            bool(body.get("force")))))
        elif path == "/api/fix":
            # the Health view's buttons: only fixes that are safe to run unattended, each one a fixed command
            fix = str(body.get("fix", ""))
            m = re.fullmatch(r"worker-up:(\d{1,3})", fix)
            if fix == "egress-up":
                rc, out, err = B.ssh_run_full(f"cd {B.FORGE_DIR} && bin/egress-up 2>&1", timeout=600)
            elif m:
                rc, out, err = B.ssh_run_full(f"cd {B.FORGE_DIR} && bin/worker-up {int(m.group(1))} 2>&1", timeout=900)
            elif fix == "restart-foreman":
                svc = self._service(proj, pf)
                rc, out, err = B.ssh_run_full(f"sudo -n systemctl restart {svc} 2>&1", timeout=60)
                if rc:
                    out = (out + err).strip() + f"\nThis needs root - on the Agent-Forge machine: sudo systemctl restart {svc}"
                    err = ""
            elif fix == "resume":
                rc, out, err = foreman(pf, "resume", timeout=30)
            else:
                self._json({"ok": False, "message": "this fix has no button - follow the hint"}, 400); return
            self._json({"ok": rc == 0, "message": B.ANSI.sub("", (out + err).strip())[-1500:]})
        elif path == "/api/egress":
            # acknowledge the alarms, or decide about one host: allow it, take it off again, or accept it as known noise
            act, host = str(body.get("action", "")), str(body.get("host", "")).strip().lower()
            if act == "ack":
                rc, out, err = B.ssh_run_full(f"cd {B.FORGE_DIR} && bin/egress-report --ack", timeout=30)
                self._json({"ok": rc == 0, "message": (out or err).strip()[-300:]}); return
            flag = {"allow": "--allow", "unallow": "--unallow", "ignore": "--baseline-add"}.get(act)
            if not flag or not HOST.fullmatch(host):
                self._json({"ok": False, "message": "action ack | allow | unallow | ignore, and an exact host name"}, 400); return
            rc, out, err = B.ssh_run_full(f"cd {B.FORGE_DIR} && bin/egress-report {flag} {host} --json", timeout=60)
            self._json(as_json(out, err, rc))
        elif path == "/api/disk":
            act, days = str(body.get("action", "")), str(body.get("days", ""))
            if act not in ("compress", "transcripts", "docker") or (days and not days.isdigit()):
                self._json({"error": "action compress | transcripts | docker"}, 400); return
            rc, out, err = foreman(pf, f"cleanup {act}" + (f" {int(days)}" if days else ""), timeout=900)
            self._json(as_json(out, err, rc))
        elif path == "/api/login":
            w, act = str(body.get("worker", "")), str(body.get("action", ""))
            if not re.fullmatch(r"worker-\d+", w):
                self._json({"error": "bad worker"}, 400); return
            if act == "start":
                self._json(B.LOGIN.start(w))
            elif act == "input":
                self._json(B.LOGIN.send(w, str(body.get("text", ""))[:500]))
            elif act == "stop":
                self._json(B.LOGIN.stop(w))
            else:
                self._json({"error": "unknown action"}, 400)
        else:
            self.send_response(404); self.end_headers()

    # ------------------------------------------------------------------------------------------------------- GET: reads
    def do_HEAD(self):
        if not host_allowed(self.headers.get("Host")):
            self._deny(403, "host not allowed (set DASHBOARD_HOSTS to add your reverse proxy's name)"); return
        self.send_response(405); self.end_headers()

    def do_GET(self):
        if not host_allowed(self.headers.get("Host")):
            self._deny(403, "host not allowed (set DASHBOARD_HOSTS to add your reverse proxy's name)"); return
        if self.path.split("?")[0] == "/healthz":
            self._json({"ok": True}); return
        if not self._allowed():
            return
        path, q = self.path.split("?")[0], self._query()
        if DASHBOARD_TOKEN and path in ("/", "/index.html") and "token" in q:
            # signed in with ?token=: keep it in a cookie and drop it from the address bar (and from history)
            self.send_response(303)
            self.send_header("Set-Cookie", f"af_token={DASHBOARD_TOKEN}; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000"
                             + ("; Secure" if (self.headers.get("X-Forwarded-Proto") == "https" or ".ts.net" in (self.headers.get("Host") or "")) else ""))
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path in ("/", "/index.html"):
            self._raw(STATIC["index.html"][0], "text/html; charset=utf-8", cache="no-cache"); return
        if path.startswith("/static/"):
            f = STATIC.get(path[len("/static/"):])
            if not f:
                self.send_response(404); self.end_headers(); return
            self._raw(f[0], f[1], cache="public, max-age=31536000, immutable" if "v" in q else "no-cache"); return
        if not path.startswith("/api/"):
            self.send_response(404); self.end_headers(); return
        proj = q.get("project", "default")
        try:
            pf = B.pflag(proj)
        except ValueError:
            self._json({"error": "bad project"}, 400); return
        if path == "/api/stream":
            self._stream(proj)
        elif path == "/api/panes":
            with L.PANES_LOCK:
                order = {"orchestrator": 0, "worker": 1}
                items = sorted(L.PANES.values(), key=lambda p: (order.get(p.kind, 9), p.title))
                panes = [{"id": p.id, "title": p.title, "kind": p.kind, "state": p.state, "lines": list(p.lines)} for p in items]
            self._json({"roster": L.ROSTER.snapshot(), "panes": panes})
        elif path in ("/api/foreman", "/api/status"):
            e = B.FOREMAN_BY.get(proj) or {"data": None, "at": 0, "error": "unknown project or not loaded yet"}
            self._json({"foreman": e["data"], "fetched_at": e["at"], "error": e["error"]})
        elif path == "/api/projects":
            self._json({"projects": B.PROJECTS["list"] or [{"name": "default"}]})
        elif path == "/api/diff":
            tid = q.get("task", "")
            if not TASK_ID.fullmatch(tid):
                self._json({"error": "bad task id"}, 400); return
            rc, out, err = foreman(pf, f"diff {tid}", timeout=40)
            self._raw((out if rc == 0 else f"(error: {err.strip()[-300:]})").encode(), "text/plain; charset=utf-8")
        elif path in ("/api/task", "/api/goal"):
            i = q.get("id", "")
            if not (TASK_ID if path == "/api/task" else GOAL_ID).fullmatch(i):
                self._json({"error": "bad id"}, 400); return
            rc, out, err = foreman(pf, f"{path[5:]} {i} show", timeout=60)
            self._json(as_json(out, err, rc))
        elif path == "/api/search":
            words = q.get("q", "").strip()[:200]
            if len(words) < 2:
                self._json([]); return
            rc, out, err = foreman(pf, "search", inp=words, timeout=60)
            self._json(as_json(out, err, rc))
        elif path == "/api/money":
            rc, out, err = foreman(pf, "money", timeout=60)
            self._json(as_json(out, err, rc))
        elif path == "/api/docs":
            rc, out, err = foreman(pf, "doc list", timeout=30)
            self._json(as_json(out, err, rc))
        elif path == "/api/doc":
            name = q.get("name", "")
            if not DOC_NAME.fullmatch(name):
                self._json({"error": "unknown document"}, 400); return
            rc, out, err = foreman(pf, f"doc get {name}", timeout=30)
            self._json(as_json(out, err, rc))
        elif path == "/api/run":
            rid = q.get("run", "")
            if not L.RUN_ID.fullmatch(rid):
                self._json({"error": "bad run id"}, 400); return
            d = f"{B.FORGE_DIR}/runs/{rid}"
            rc, out, err = B.ssh_run_full(f"cat {d}/meta.json 2>/dev/null; echo; echo '<<AF>>'; "
                                          f"cat {d}/stream.jsonl 2>/dev/null || zcat {d}/stream.jsonl.gz 2>/dev/null", timeout=60)
            meta_txt, _, stream = out.partition("<<AF>>\n")
            try:
                meta = json.loads(meta_txt.strip() or "{}")
            except ValueError:
                meta = {}
            self._json({"run": rid, "meta": {k: meta.get(k) for k in ("worker", "task", "runtime", "model", "reason", "tokens", "tool_calls",
                                                                        "duration_s", "cost_usd", "dispatched", "finished", "variant")},
                        "lines": L.render_run(stream), "missing": not stream.strip()})
        elif path == "/api/health":
            rc, out, err = B.ssh_run_full(f"cd {B.FORGE_DIR} && bin/doctor{pf} --json" + ("" if q.get("full") else " --quick"), timeout=300)
            self._json(as_json(out, err, rc))
        elif path == "/api/egress":
            since = q.get("since", "24h")
            if not re.fullmatch(r"\d{1,3}[hd]", since):
                since = "24h"
            rc, out, err = B.ssh_run_full(f"cd {B.FORGE_DIR} && bin/egress-report --json --since {since}", timeout=120)
            self._json(as_json(out, err, rc))
        elif path == "/api/disk":
            rc, out, err = foreman(pf, "disk", timeout=120)
            self._json(as_json(out, err, rc))
        elif path == "/api/settings":
            rc, out, err = foreman(pf, "settings", timeout=60)
            try:
                self._json(json.loads(out))
            except Exception:
                self._json({"error": (err or out)[-300:] or "settings need a newer bin/foreman"})
        elif path.startswith("/api/jobs/"):
            j = B.JOBS.get(path.rsplit("/", 1)[-1])
            self._json(j or {"error": "unknown job"})
        elif path == "/api/login":
            w = q.get("worker", "")
            self._json(B.LOGIN.read(w) if re.fullmatch(r"worker-\d+", w) else {"error": "bad worker"})
        else:
            self.send_response(404)
            self.end_headers()

    def _stream(self, proj):
        """Server-Sent Events for one open page: the project's foreman state (first in full, then only the top-level keys that
        changed) and the live panes (only new transcript lines). A comment every 15 s keeps proxies from closing it."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def send(ev, obj):
            self.wfile.write(f"event: {ev}\ndata: {json.dumps(obj, separators=(',', ':'))}\n\n".encode())
            self.wfile.flush()
        sent, last_at, last_err, panes_state, ping = None, None, None, {}, time.time()
        try:
            send("hello", {"project": proj})
            while True:
                e = B.FOREMAN_BY.get(proj) or {"data": None, "at": 0, "error": "unknown project or not loaded yet"}
                if e["at"] != last_at or e["error"] != last_err:
                    data = e["data"] or {}
                    if sent is None:
                        send("foreman", {"full": data, "fetched_at": e["at"], "error": e["error"]})
                    else:
                        delta = {k: v for k, v in data.items() if sent.get(k) != v}
                        send("foreman", {"delta": delta, "removed": [k for k in sent if k not in data], "fetched_at": e["at"],
                                         "error": e["error"]})
                    sent, last_at, last_err = dict(data), e["at"], e["error"]
                pd = L.pane_delta(panes_state)
                if pd:
                    send("panes", pd)
                if time.time() - ping > 15:
                    self.wfile.write(b": ping\n\n"); self.wfile.flush(); ping = time.time()
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            return


class Server(ThreadingHTTPServer):
    daemon_threads = True            # an open live stream never keeps the process from exiting


def main():
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    atexit.register(shutdown)
    threading.Thread(target=L.discover_loop, daemon=True).start()
    threading.Thread(target=L.quota_loop, daemon=True).start()
    threading.Thread(target=B.foreman_loop, daemon=True).start()
    srv = Server(("127.0.0.1", B.BIND_PORT), Handler)
    srv.serve_forever()


if __name__ == "__main__":
    main()
