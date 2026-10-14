#!/usr/bin/env python3
"""bin/merge-pr against a fake Forgejo: a busy or unreachable Forgejo (405 "try again later", 5xx, no connection) must end
with exit 75 (the foreman retries later and never blocks the task), a real conflict (409) with exit 1, success with 0.
Run: python3 tests/test_merge_pr.py"""
import json, os, subprocess, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
bad = 0
MODE = {"merge": (405, '{"message":"Please try again later"}')}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body):
        self.send_response(code); self.end_headers(); self.wfile.write(body.encode())
    def do_GET(self):
        self._send(200, json.dumps({"merged": False, "state": "open", "mergeable": True, "head": {"ref": "b"}}))
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._send(*MODE["merge"])


def run(url):
    sec = tempfile.mkdtemp()
    open(os.path.join(sec, "orchestrator.token"), "w").write("x")
    env = dict(os.environ, FORGE_URL=url, FORGE_REPO="o/r", SANDBOX_SECRETS=sec, MERGE_PR_RETRY_SECONDS="0")
    return subprocess.run([sys.executable, os.path.join(ROOT, "bin", "merge-pr"), "7"], capture_output=True, text=True, env=env, timeout=60)


def check(name, ok):
    global bad
    bad += not ok
    print(("ok   " if ok else "FAIL ") + name)


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_port}"
check("405 try again later -> exit 75", run(url).returncode == 75)
MODE["merge"] = (503, "down"); check("503 -> exit 75", run(url).returncode == 75)
MODE["merge"] = (409, "conflict"); r = run(url); check("409 -> exit 1, not mergeable", r.returncode == 1 and "not mergeable" in r.stderr)
MODE["merge"] = (200, "null"); check("200 -> exit 0", run(url).returncode == 0)
srv.shutdown()
check("no connection -> exit 75", run("http://127.0.0.1:9").returncode == 75)
print("ALL OK" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
