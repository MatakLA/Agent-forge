#!/usr/bin/env python3
"""Agent-Forge web dashboard and admin console: the team live (orchestrator, every worker), goals with their acceptance criteria,
the task board, timeline, dependency graphs, diffs, metrics, events; and the owner's controls - new goals and backlog, answers to
the planner's questions, kill switch per project or for all, settings (models, providers, worker pools, limits), new workers with
their Antigravity login in a browser terminal, and new projects.

Python standard library only: this file is the HTTP side, web/dashboard/ the backend, web/static/ the page. Runs next to
bin/foreman (LOCAL mode, default) or on another machine reading it over ssh (REMOTE mode: set VM_IP, VM_USER, VM_KEY,
VM_KNOWN_HOSTS, and FORGE_DIR if Agent-Forge is not in ~/sandbox there). Binds 127.0.0.1 only - publish it with Tailscale serve or a reverse proxy (then list its host name in DASHBOARD_HOSTS) and keep it private: it
is an admin console. Every write goes through bin/foreman / bin/killswitch / bin/worker-add, which validate their input.

Config (environment): BIND_PORT (8171), FORGE_DIR, VM_IP / VM_USER / VM_KEY / VM_KNOWN_HOSTS for remote mode, DASHBOARD_HOSTS,
DASHBOARD_TOKEN (optional sign-in), FOREMAN_SERVICE / FOREMAN_SERVICE_PROJECT (systemd unit names), CLAUDE_PROJECT.
"""
import atexit, json, os, re, signal, sys, threading, time
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
#  - the page cannot be framed by another site (clickjacking);
#  - optional DASHBOARD_TOKEN: open the page once as /?token=<token>; a SameSite=Strict, HttpOnly cookie keeps you signed in.
DASHBOARD_HOSTS = {h.strip().lower() for h in os.environ.get("DASHBOARD_HOSTS", "").split(",") if h.strip()}


DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")


SEC_HEADERS = {"X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
               "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                          "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}


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

    def _raw(self, raw, ctype, cache="no-store"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", cache)
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
            pf = B.pflag(proj)
        except ValueError:
            self._json({"error": "bad project"}, 400); return
        penv = f"FORGE_PROJECT={proj} " if pf else ""
        if self.path == "/api/ask":
            q = str(body.get("question", "")).strip()[:4000]
            if not q:
                self._json({"error": "empty question"}, 400); return
            if not B.ASK_LOCK.acquire(blocking=False):
                self._json({"error": "another question is being answered - wait for it"}, 429); return
            try:
                rc, out, err = B.ssh_run_full(f"cd {B.FORGE_DIR} && bin/foreman{pf} ask", timeout=900, inp=q)
            finally:
                B.ASK_LOCK.release()
            self._json({"answer": out.strip() or (err.strip()[-500:] if rc else "(empty answer)")})
        elif self.path in ("/api/killswitch", "/api/resume"):
            action = "off" if self.path == "/api/resume" else str(body.get("action"))
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
                svc = B.FOREMAN_SERVICE_PROJECT.format(project=proj) if pf else B.FOREMAN_SERVICE
                rc, active, _ = B.ssh_run_full(f"systemctl is-active {svc}", timeout=10)
                if active.strip() != "active":
                    run(f"restart {svc}", f"sudo systemctl restart {svc}", timeout=60)
            else:
                self._json({"error": "action must be on or off"}, 400); return
            self._json({"ok": ok, "steps": steps})
        elif self.path == "/api/settings":
            if body.get("refresh_models"):
                rc, out, err = B.ssh_run_full(f"cd {B.FORGE_DIR} && bin/foreman{pf} settings --refresh-models", timeout=200)
                try:
                    self._json(json.loads(out) if rc == 0 else {"error": err[-300:]})
                except ValueError:
                    self._json({"error": (err or out)[-300:]})
                return
            changes = body.get("changes") or {}
            if not isinstance(changes, dict) or not all(isinstance(k, str) and isinstance(v, (str, int)) for k, v in changes.items()):
                self._json({"error": "changes must be an object of strings"}, 400); return
            rc, out, err = B.ssh_run_full(f"cd {B.FORGE_DIR} && bin/foreman{pf} settings apply", timeout=60,
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
        elif self.path == "/api/goal":
            # the owner's text goes to bin/foreman on stdin, never into the command line
            text, mode = str(body.get("text", "")).strip()[:6000], str(body.get("mode", "now"))
            if not text:
                self._json({"error": "empty goal"}, 400); return
            cmd = {"now": "goal -", "backlog": "backlog add -", "roadmap": "project -"}.get(mode)
            if not cmd:
                self._json({"error": "mode must be now, backlog or roadmap"}, 400); return
            rc, out, err = B.ssh_run_full(f"cd {B.FORGE_DIR} && bin/foreman{pf} {cmd}", timeout=30, inp=text)
            self._json({"ok": rc == 0, "message": (out or err).strip()[-400:]})
        elif self.path == "/api/backlog":
            act, n, to = str(body.get("action", "")), str(body.get("n", "")), str(body.get("to", ""))
            if act not in ("drop", "move") or not n.isdigit() or (act == "move" and not to.isdigit()):
                self._json({"error": "action drop <n> | move <n> <to>"}, 400); return
            rc, out, err = B.ssh_run_full(f"cd {B.FORGE_DIR} && bin/foreman{pf} backlog {act} {int(n)}" + (f" {int(to)}" if act == "move" else ""),
                                        timeout=30)
            self._json({"ok": rc == 0, "message": (err or "").strip()[-300:]})
        elif self.path == "/api/answer":
            n = str(body.get("n", ""))
            if not n.isdigit():
                self._json({"error": "bad question number"}, 400); return
            rc, out, err = B.ssh_run_full(f"cd {B.FORGE_DIR} && bin/foreman{pf} answer {int(n)}", timeout=30,
                                        inp=str(body.get("answer", ""))[:4000])
            self._json({"ok": rc == 0, "message": (out or err).strip()[-300:]})
        elif self.path == "/api/workers":
            rt = str(body.get("runtime") or "agy")
            if body.get("action") != "add" or rt not in ("agy", "claude", "codex", "gemini"):
                self._json({"error": "action add, runtime agy|claude|codex|gemini"}, 400); return
            self._json(B.start_job("add-worker", lambda job: B.add_worker_job(job, rt)))
        elif self.path == "/api/secret":
            # an API key or forge token typed by the owner: goes to bin/secret-set on stdin, never into a command line or a log
            name, value = str(body.get("name", "")), str(body.get("value", "")).strip()
            if name not in ("anthropic.key", "openai.key", "gemini.key", "github.token", "gitlab.token", "notify.token") or not value:
                self._json({"error": "unknown secret name or empty value"}, 400); return
            rc, out, err = B.ssh_run_full(f"cd {B.FORGE_DIR} && bin/secret-set {name}", timeout=30, inp=value)
            self._json({"ok": rc == 0, "message": (out or err).strip()[-200:]})
        elif self.path == "/api/upstream":
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
        elif self.path == "/api/login":
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
            with L.PANES_LOCK:
                order = {"orchestrator": 0, "worker": 1}
                items = sorted(L.PANES.values(), key=lambda p: (order.get(p.kind, 9), p.title))
                panes = [{"id": p.id, "title": p.title, "kind": p.kind, "state": p.state,
                          "lines": list(p.lines)} for p in items]
            self._json({"roster": L.ROSTER.snapshot(), "panes": panes})
        elif self.path.startswith("/api/diff?"):
            import urllib.parse
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            tid, proj = qs.get("task", [""])[0], qs.get("project", ["default"])[0]
            try:
                pf = B.pflag(proj)
            except ValueError:
                self._json({"error": "bad project"}, 400); return
            if not re.fullmatch(r"\d{4}-[\w-]+", tid):
                self._json({"error": "bad task id"}, 400); return
            rc, out, err = B.ssh_run_full(f"cd {B.FORGE_DIR} && bin/foreman{pf} diff {tid}", timeout=40)
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
                pf = B.pflag(proj)
            except ValueError:
                self._json({"error": "bad project"}, 400); return
            rc, out, err = B.ssh_run_full(f"cd {B.FORGE_DIR} && bin/foreman{pf} settings", timeout=60)
            try:
                self._json(json.loads(out))
            except Exception:
                self._json({"error": (err or out)[-300:] or "settings need a newer bin/foreman"})
        elif self.path.startswith("/api/jobs/"):
            j = B.JOBS.get(self.path.rsplit("/", 1)[-1])
            self._json(j or {"error": "unknown job"})
        elif self.path.startswith("/api/login?"):
            import urllib.parse
            w = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("worker", [""])[0]
            self._json(B.LOGIN.read(w) if re.fullmatch(r"worker-\d+", w) else {"error": "bad worker"})
        elif self.path == "/api/projects":
            self._json({"projects": B.PROJECTS["list"] or [{"name": "default"}]})
        elif self.path.split("?")[0] == "/api/foreman":
            import urllib.parse
            proj = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("project", ["default"])[0]
            e = B.FOREMAN_BY.get(proj) or {"data": None, "at": 0, "error": "unknown project or not loaded yet"}
            self._json({"foreman": e["data"], "fetched_at": e["at"], "error": e["error"]})
        elif self.path.split("?")[0] in ("/", "/index.html"):
            self._raw(STATIC["index.html"][0], "text/html; charset=utf-8", cache="no-cache")
        elif self.path.startswith("/static/"):
            f = STATIC.get(self.path.split("?")[0][len("/static/"):])
            if not f:
                self.send_response(404); self.end_headers(); return
            self._raw(f[0], f[1], cache="public, max-age=31536000, immutable" if "?v=" in self.path else "no-cache")
        elif self.path == "/healthz":
            self._json({"ok": True})
        else:
            self.send_response(404)
            self.end_headers()


def main():
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    atexit.register(shutdown)
    threading.Thread(target=L.discover_loop, daemon=True).start()
    threading.Thread(target=L.quota_loop, daemon=True).start()
    threading.Thread(target=B.foreman_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", B.BIND_PORT), Handler)
    srv.serve_forever()


if __name__ == "__main__":
    main()
