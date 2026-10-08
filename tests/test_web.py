#!/usr/bin/env python3
"""Dashboard server tests without a browser or a running install: access control (Host check, CSRF header, token cookie,
security headers) and the write endpoints' input validation, against a real ThreadingHTTPServer on a free port.
Run: python3 tests/test_web.py   (standard library only)"""
import http.client, importlib.util, json, os, sys, threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["DASHBOARD_TOKEN"] = "s3cret"
spec = importlib.util.spec_from_file_location("srv", os.path.join(ROOT, "web", "server.py"))
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)
calls = []
srv.B.ssh_run_full = lambda cmd, timeout=30, inp=None: (calls.append((cmd, inp)) or (0, "ok", ""))
bad = 0


def check(name, ok):
    global bad
    bad += not ok
    print(("ok   " if ok else "FAIL ") + name)


check("host_allowed: localhost, 127.0.0.1:port, *.ts.net; not other names",
      srv.host_allowed("127.0.0.1:8171") and srv.host_allowed("localhost") and srv.host_allowed("box.tail1234.ts.net:6443")
      and not srv.host_allowed("evil.example") and not srv.host_allowed("127.0.0.1.evil.example") and not srv.host_allowed(""))
check("token_ok: cookie or query token, constant-time compare", srv.token_ok("a=1; af_token=s3cret") and srv.token_ok("", "s3cret")
      and not srv.token_ok("af_token=nope") and not srv.token_ok(None))

httpd = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
port = httpd.server_address[1]


def req(method, path, body=None, headers=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": host or f"127.0.0.1:{port}", "Cookie": "af_token=s3cret", "Content-Type": "application/json"}
    h.update(headers or {})
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read().decode(errors="replace")


s, h, _ = req("GET", "/healthz", headers={"Cookie": ""})
check("healthz needs no token", s == 200)
s, h, _ = req("GET", "/api/projects", headers={"Cookie": ""})
check("API without the token: 401", s == 401)
s, h, _ = req("GET", "/?token=s3cret", headers={"Cookie": ""})
check("?token= sets an HttpOnly SameSite=Strict cookie and redirects", s == 303 and "HttpOnly" in h.get("Set-Cookie", "")
      and "SameSite=Strict" in h.get("Set-Cookie", "") and h.get("Location") == "/")
s, h, body = req("GET", "/")
csp = h.get("Content-Security-Policy", "")
check("page: no framing, CSP without inline scripts, nosniff", s == 200 and h.get("X-Frame-Options") == "DENY" and "frame-ancestors 'none'" in csp
      and "script-src 'self';" in csp and h.get("X-Content-Type-Options") == "nosniff" and "/static/js/core.js?v=" in body and "{{V}}" not in body)
s, h, js = req("GET", "/static/js/core.js")
check("static: the page's scripts are served, and every POST they make carries X-Agent-Forge", s == 200 and "X-Agent-Forge" in js
      and h.get("Content-Type", "").startswith("text/javascript"))
s, _, _ = req("GET", "/static/../server.py")
s2, _, _ = req("GET", "/static/js/../../server.py")
check("static: nothing outside the known files (no path traversal)", s == 404 and s2 == 404)
import glob
inline = [f for f in glob.glob(os.path.join(ROOT, "web", "static", "**", "*.*"), recursive=True) if "onclick=" in open(f).read()]
check("static: no inline event handlers (the CSP would block them)", not inline)
s, _, _ = req("GET", "/api/projects", host="attacker.example")
check("DNS rebinding: a foreign Host is refused", s == 403)
s, _, _ = req("POST", "/api/killswitch", {"action": "on"})
check("CSRF: POST without X-Agent-Forge is refused and runs nothing", s == 403 and not calls)
s, _, out = req("POST", "/api/goal", {"text": "Add CSV export'; rm -rf / #", "mode": "now"}, {"X-Agent-Forge": "1"})
check("goal text goes to stdin, never into the command", s == 200 and calls and calls[-1][0].endswith("bin/foreman goal -")
      and calls[-1][1] == "Add CSV export'; rm -rf / #")
s, _, out = req("POST", "/api/backlog", {"action": "move", "n": "2; reboot", "to": "1"}, {"X-Agent-Forge": "1"})
check("backlog: non-numeric positions are refused", s == 400)
s, _, out = req("POST", "/api/answer", {"n": "1", "answer": "use PostgreSQL"}, {"X-Agent-Forge": "1"})
check("answer: number in the command, text on stdin", s == 200 and calls[-1][0].endswith("bin/foreman answer 1") and calls[-1][1] == "use PostgreSQL")
s, _, out = req("POST", "/api/goal", {"text": "x", "mode": "now", "project": "../etc"}, {"X-Agent-Forge": "1"})
check("a bad project name is refused", s == 400)
s, _, out = req("POST", "/api/secret", {"name": "anthropic.key", "value": "sk-ant-secret-value"}, {"X-Agent-Forge": "1"})
check("secret: the value goes to bin/secret-set on stdin, never into the command",
      s == 200 and calls[-1][0].endswith("bin/secret-set anthropic.key") and calls[-1][1] == "sk-ant-secret-value"
      and "sk-ant" not in calls[-1][0])
n = len(calls)
s, _, out = req("POST", "/api/secret", {"name": "../../.ssh/authorized_keys", "value": "x"}, {"X-Agent-Forge": "1"})
check("secret: only the known names are accepted", s == 400 and len(calls) == n)
s, _, out = req("POST", "/api/workers", {"action": "add", "runtime": "claude; rm -rf /"}, {"X-Agent-Forge": "1"})
check("workers: an unknown runtime is refused", s == 400)
s, _, out = req("POST", "/api/upstream", {"action": "connect", "kind": "github", "repo": "acme/shop; reboot", "url": "https://github.com"},
                {"X-Agent-Forge": "1"})
check("upstream: a bad repository name is refused before anything runs", s == 200 and json.loads(out).get("errors", {}).get("repo") and len(calls) == n)
s, _, out = req("POST", "/api/upstream", {"action": "connect", "kind": "svn"}, {"X-Agent-Forge": "1"})
check("upstream: only forgejo / github / gitlab", s == 400)
httpd.shutdown()
print("web tests: " + ("ALL OK" if not bad else f"{bad} FAILED"))
sys.exit(1 if bad else 0)
