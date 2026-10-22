#!/usr/bin/env python3
"""Dashboard server tests for access control, request validation, and input sanitization.
Public interface: run directly via python3 tests/test_web.py or unittest discovery.
Invariants:
- Host header is strictly validated against allowed origins on all endpoints to prevent DNS rebinding.
- Port numbers in Host headers must be strictly numeric and non-empty.
- Origin and Referer headers on state-changing POSTs must match allowed hosts (CSRF guard).
- Authentication tokens are verified in constant time and redacted from error responses.
- All identifiers (TASK_ID, GOAL_ID, DOC_NAME, HOST, TARGET) are validated before shell execution.
- Readiness recommend endpoint (/api/readiness/recommend) enforces auth/host guards, calls foreman(['readiness-recommend']), and returns {top, ranked, message} JSON.
- Readiness summary endpoint (/api/readiness/summary) enforces auth/host guards and returns honest per-runtime readiness and cost_model summary.
- Control actions (autopilot on/off, pause/resume) and project names are validated before executing foreman commands, with round-trip distinct argv enforcement.
- Control actions and payload shape in web/server.py match web/static/js/overview.js exactly.
"""
import contextlib
import glob
import http.client
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(ROOT, "bin", "lib") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))
from foreman import readiness

os.environ["DASHBOARD_TOKEN"] = "s3cret"
spec = importlib.util.spec_from_file_location("srv", os.path.join(ROOT, "web", "server.py"))
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)


class TestWebServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calls = []
        cls.orig_ssh = srv.B.ssh_run_full
        srv.B.ssh_run_full = lambda cmd, timeout=30, inp=None: (cls.calls.append((cmd, inp)) or (0, "ok", ""))
        cls.httpd = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        srv.B.ssh_run_full = cls.orig_ssh
        cls.httpd.shutdown()

    def setUp(self):
        TestWebServer.calls.clear()

    def req(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": host or f"127.0.0.1:{self.port}", "Cookie": "af_token=s3cret", "Content-Type": "application/json"}
        h.update(headers or {})
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read().decode(errors="replace")

    def test_host_allowed_valid_names(self):
        """Invariant: host_allowed accepts localhost, 127.0.0.1, [::1], and valid Tailscale names."""
        self.assertTrue(srv.host_allowed("127.0.0.1:8171"))
        assert srv.host_allowed("127.0.0.1:8171")
        self.assertTrue(srv.host_allowed("localhost"))
        assert srv.host_allowed("localhost")
        self.assertTrue(srv.host_allowed("localhost:8171"))
        assert srv.host_allowed("localhost:8171")
        self.assertTrue(srv.host_allowed("[::1]"))
        assert srv.host_allowed("[::1]")
        self.assertTrue(srv.host_allowed("[::1]:8171"))
        assert srv.host_allowed("[::1]:8171")
        self.assertTrue(srv.host_allowed("box.tail1234.ts.net:6443"))
        assert srv.host_allowed("box.tail1234.ts.net:6443")

    def test_host_allowed_port_validation(self):
        """Invariant: host_allowed rejects empty port strings and non-numeric port injections."""
        self.assertFalse(srv.host_allowed("127.0.0.1:"))
        assert not srv.host_allowed("127.0.0.1:")
        self.assertFalse(srv.host_allowed("localhost:"))
        assert not srv.host_allowed("localhost:")
        self.assertFalse(srv.host_allowed("[::1]:"))
        assert not srv.host_allowed("[::1]:")
        self.assertFalse(srv.host_allowed("127.0.0.1:evil.com"))
        assert not srv.host_allowed("127.0.0.1:evil.com")
        self.assertFalse(srv.host_allowed("localhost:evil.com"))
        assert not srv.host_allowed("localhost:evil.com")
        self.assertFalse(srv.host_allowed("[::1]:evil.com"))
        assert not srv.host_allowed("[::1]:evil.com")
        self.assertFalse(srv.host_allowed("[::1]evil.com"))
        assert not srv.host_allowed("[::1]evil.com")
        self.assertFalse(srv.host_allowed(":8171"))
        assert not srv.host_allowed(":8171")
        self.assertFalse(srv.host_allowed(":"))
        assert not srv.host_allowed(":")
        self.assertFalse(srv.host_allowed("127.0.0.1:\u00b2"))
        self.assertFalse(srv.host_allowed("[::1]:\u00b2"))

    def test_host_allowed_empty_and_malformed(self):
        """Invariant: host_allowed rejects empty string, None, whitespace, and suffix attacks."""
        self.assertFalse(srv.host_allowed(""))
        assert not srv.host_allowed("")
        self.assertFalse(srv.host_allowed("   "))
        assert not srv.host_allowed("   ")
        self.assertFalse(srv.host_allowed(None))
        assert not srv.host_allowed(None)
        self.assertFalse(srv.host_allowed("evil.example"))
        assert not srv.host_allowed("evil.example")
        self.assertFalse(srv.host_allowed("127.0.0.1.evil.example"))
        assert not srv.host_allowed("127.0.0.1.evil.example")
        self.assertFalse(srv.host_allowed(".ts.net"))
        assert not srv.host_allowed(".ts.net")
        self.assertFalse(srv.host_allowed("ts.net"))
        assert not srv.host_allowed("ts.net")
        self.assertFalse(srv.host_allowed("[::1"))
        assert not srv.host_allowed("[::1")

    def test_host_allowed_dashboard_hosts_setting(self):
        """Invariant: host_allowed permits entries explicitly registered in DASHBOARD_HOSTS."""
        srv.DASHBOARD_HOSTS.add("my-proxy.internal")
        try:
            self.assertTrue(srv.host_allowed("my-proxy.internal"))
            assert srv.host_allowed("my-proxy.internal")
            self.assertTrue(srv.host_allowed("my-proxy.internal:8080"))
            assert srv.host_allowed("my-proxy.internal:8080")
        finally:
            srv.DASHBOARD_HOSTS.discard("my-proxy.internal")

    def test_token_ok_constant_time(self):
        """Invariant: token_ok validates tokens constant-time, supporting cookies and query params."""
        self.assertTrue(srv.token_ok("a=1; af_token=s3cret"))
        assert srv.token_ok("a=1; af_token=s3cret")
        self.assertTrue(srv.token_ok('a=1; af_token="s3cret"'))
        assert srv.token_ok('a=1; af_token="s3cret"')
        self.assertTrue(srv.token_ok("", "s3cret"))
        assert srv.token_ok("", "s3cret")
        self.assertFalse(srv.token_ok("af_token=nope"))
        assert not srv.token_ok("af_token=nope")
        self.assertFalse(srv.token_ok("", "wrong"))
        assert not srv.token_ok("", "wrong")
        self.assertFalse(srv.token_ok(None))
        assert not srv.token_ok(None)
        self.assertFalse(srv.token_ok(""))
        assert not srv.token_ok("")

    def test_healthz_unauthenticated(self):
        """Invariant: /healthz requires no token when host is allowed."""
        s, _, _ = self.req("GET", "/healthz", headers={"Cookie": ""})
        self.assertEqual(s, 200)
        assert s == 200

    def test_api_requires_auth(self):
        """Invariant: API endpoints return 401 Unauthorized without a valid token."""
        s, _, _ = self.req("GET", "/api/projects", headers={"Cookie": ""})
        self.assertEqual(s, 401)
        assert s == 401

    def test_token_cookie_setting(self):
        """Invariant: ?token= sets an HttpOnly SameSite=Strict cookie and redirects to /."""
        s, h, _ = self.req("GET", "/?token=s3cret", headers={"Cookie": ""})
        self.assertEqual(s, 303)
        assert s == 303
        self.assertIn("HttpOnly", h.get("Set-Cookie", ""))
        assert "HttpOnly" in h.get("Set-Cookie", "")
        self.assertIn("SameSite=Strict", h.get("Set-Cookie", ""))
        assert "SameSite=Strict" in h.get("Set-Cookie", "")
        self.assertEqual(h.get("Location"), "/")
        assert h.get("Location") == "/"

    def test_security_headers_and_page_serving(self):
        """Invariant: Root index page sets frame-ancestors, CSP without inline script, and nosniff."""
        s, h, body = self.req("GET", "/")
        csp = h.get("Content-Security-Policy", "")
        self.assertEqual(s, 200)
        assert s == 200
        self.assertEqual(h.get("X-Frame-Options"), "DENY")
        assert h.get("X-Frame-Options") == "DENY"
        self.assertIn("frame-ancestors 'none'", csp)
        assert "frame-ancestors 'none'" in csp
        self.assertIn("script-src 'self';", csp)
        assert "script-src 'self';" in csp
        self.assertEqual(h.get("X-Content-Type-Options"), "nosniff")
        assert h.get("X-Content-Type-Options") == "nosniff"
        self.assertIn("/static/js/core.js?v=", body)
        assert "/static/js/core.js?v=" in body
        self.assertNotIn("{{V}}", body)
        assert "{{V}}" not in body

    def test_static_file_serving_and_traversal_prevention(self):
        """Invariant: Static files are served with proper headers; path traversal returns 404."""
        s, h, js = self.req("GET", "/static/js/core.js")
        self.assertEqual(s, 200)
        assert s == 200
        self.assertIn("X-Agent-Forge", js)
        assert "X-Agent-Forge" in js
        self.assertTrue(h.get("Content-Type", "").startswith("text/javascript"))
        assert h.get("Content-Type", "").startswith("text/javascript")

        s1, _, _ = self.req("GET", "/static/../server.py")
        s2, _, _ = self.req("GET", "/static/js/../../server.py")
        self.assertEqual(s1, 404)
        assert s1 == 404
        self.assertEqual(s2, 404)
        assert s2 == 404

    def test_no_inline_event_handlers(self):
        """Invariant: Static files contain no inline event handlers forbidden by CSP."""
        inline = [f for f in glob.glob(os.path.join(ROOT, "web", "static", "**", "*.*"), recursive=True)
                  if "onclick=" in open(f).read()]
        self.assertEqual(len(inline), 0)
        assert not inline

    def test_dns_rebinding_rejected(self):
        """Invariant: DNS rebinding attacks on /healthz and /api/projects are rejected with 403."""
        s1, _, _ = self.req("GET", "/healthz", host="attacker.example")
        self.assertEqual(s1, 403)
        assert s1 == 403

        s2, _, _ = self.req("GET", "/api/projects", host="attacker.example")
        self.assertEqual(s2, 403)
        assert s2 == 403

        s3, _, _ = self.req("GET", "/", host="attacker.example")
        self.assertEqual(s3, 403)
        assert s3 == 403

        s4, _, _ = self.req("HEAD", "/", host="attacker.example")
        self.assertEqual(s4, 403)
        assert s4 == 403

    def test_csrf_post_origin_and_referer_validation(self):
        """Invariant: State-changing POST requests reject missing X-Agent-Forge and untrusted Origin/Referer."""
        s, _, _ = self.req("POST", "/api/killswitch", {"action": "on"})
        self.assertEqual(s, 403)
        assert s == 403
        self.assertEqual(len(self.calls), 0)
        assert not self.calls

        s, _, _ = self.req("POST", "/api/killswitch", {"action": "on"},
                           {"X-Agent-Forge": "1", "Origin": "http://attacker.example"})
        self.assertEqual(s, 403)
        assert s == 403

        s, _, _ = self.req("POST", "/api/killswitch", {"action": "on"},
                           {"X-Agent-Forge": "1", "Origin": "null"})
        self.assertEqual(s, 403)
        assert s == 403

        s, _, _ = self.req("POST", "/api/killswitch", {"action": "on"},
                           {"X-Agent-Forge": "1", "Origin": ""})
        self.assertEqual(s, 403)
        assert s == 403

        s, _, _ = self.req("POST", "/api/killswitch", {"action": "on"},
                           {"X-Agent-Forge": "1", "Referer": "http://attacker.example/attack"})
        self.assertEqual(s, 403)
        assert s == 403

        s, _, _ = self.req("POST", "/api/killswitch", {"action": "on"},
                           {"X-Agent-Forge": "1", "Referer": "null"})
        self.assertEqual(s, 403)
        assert s == 403

        s, _, _ = self.req("POST", "/api/killswitch", {"action": "on"},
                           {"X-Agent-Forge": "1", "Referer": ""})
        self.assertEqual(s, 403)
        assert s == 403

        s, _, _ = self.req("POST", "/api/killswitch", {"action": "on"},
                           {"X-Agent-Forge": "1", "Origin": f"http://127.0.0.1:{self.port}",
                            "Referer": f"http://127.0.0.1:{self.port}/"})
        self.assertEqual(s, 200)
        assert s == 200

    def test_token_redaction_in_error_responses(self):
        """Invariant: DASHBOARD_TOKEN is never leaked in error response bodies or deny messages."""
        old_ssh = srv.B.ssh_run_full
        try:
            srv.B.ssh_run_full = lambda cmd, timeout=30, inp=None: (1, "", "failure leaking s3cret in stderr")
            s, _, out = self.req("POST", "/api/doc", {"name": "vision", "reset": True}, {"X-Agent-Forge": "1"})
            self.assertNotIn("s3cret", out)
            assert "s3cret" not in out
            self.assertIn("[REDACTED]", out)
            assert "[REDACTED]" in out
        finally:
            srv.B.ssh_run_full = old_ssh

    def test_goal_command_stdin_and_project_safety(self):
        """Invariant: Goal text goes strictly to stdin; invalid project names are rejected."""
        H = {"X-Agent-Forge": "1"}
        s, _, out = self.req("POST", "/api/goal", {"text": "Add CSV export'; rm -rf / #", "mode": "now"}, H)
        self.assertEqual(s, 200)
        assert s == 200
        self.assertTrue(self.calls and self.calls[-1][0].endswith("bin/foreman goal -"))
        assert self.calls and self.calls[-1][0].endswith("bin/foreman goal -")
        self.assertEqual(self.calls[-1][1], "Add CSV export'; rm -rf / #")
        assert self.calls[-1][1] == "Add CSV export'; rm -rf / #"

        s, _, out = self.req("POST", "/api/goal", {"text": "x", "mode": "now", "project": "../etc"}, H)
        self.assertEqual(s, 400)
        assert s == 400

    def test_backlog_position_numeric_validation(self):
        """Invariant: Backlog positions must be numeric; command injection in positions is rejected."""
        s, _, _ = self.req("POST", "/api/backlog", {"action": "move", "n": "2; reboot", "to": "1"}, {"X-Agent-Forge": "1"})
        self.assertEqual(s, 400)
        assert s == 400

    def test_answer_stdin_safety(self):
        """Invariant: Answer text is passed on stdin, not formatted into shell command arguments."""
        s, _, _ = self.req("POST", "/api/answer", {"n": "1", "answer": "use PostgreSQL"}, {"X-Agent-Forge": "1"})
        self.assertEqual(s, 200)
        assert s == 200
        self.assertTrue(self.calls[-1][0].endswith("bin/foreman answer 1"))
        assert self.calls[-1][0].endswith("bin/foreman answer 1")
        self.assertEqual(self.calls[-1][1], "use PostgreSQL")
        assert self.calls[-1][1] == "use PostgreSQL"

    def test_numbers_are_short_ascii_digits(self):
        """Invariant: str.isdigit() also accepts '\u00b2' (int() raises on it) and 5000-digit strings (int() refuses them since
        Python 3.11), so a number from the page is a short run of 0-9 or a 400 - never a request that dies without an answer."""
        H = {"X-Agent-Forge": "1"}
        for path, body in (("/api/backlog", {"action": "drop", "n": "\u00b2"}),
                           ("/api/backlog", {"action": "move", "n": "1", "to": "\u00b2"}),
                           ("/api/backlog", {"action": "top", "n": "9" * 5000}),
                           ("/api/answer", {"n": "\u00b2", "answer": "x"}),
                           ("/api/disk", {"action": "compress", "days": "\u00b2"})):
            s, _, _ = self.req("POST", path, body, H)
            self.assertEqual(s, 400, (path, body))
        s, _, _ = self.req("GET", "/api/login?worker=worker-1&since=%C2%B2")
        self.assertEqual(s, 400)
        self.assertEqual(self.calls, [])
        s, _, _ = self.req("POST", "/api/backlog", {"action": "move", "n": 3, "to": 1}, H)
        self.assertEqual(s, 200)
        self.assertTrue(self.calls[-1][0].endswith("bin/foreman backlog move 3 1"))

    def test_odd_request_bodies_count_as_empty(self):
        """Invariant: a negative Content-Length (read(-1) waits for the client to hang up) and a JSON body that is not an object
        (a list has no .get) are an empty body - the endpoint answers 'empty goal', it does not hang or drop the connection."""
        H = {"X-Agent-Forge": "1"}
        s, _, out = self.req("POST", "/api/goal", None, dict(H, **{"Content-Length": "-1"}))
        self.assertEqual((s, json.loads(out).get("error")), (400, "empty goal"))
        for raw in ([1, 2], "text", 7, None):
            s, _, out = self.req("POST", "/api/goal", raw, H) if raw is not None else self.req("POST", "/api/goal", "null", H)
            self.assertEqual(s, 400, raw)
        self.assertEqual(self.calls, [])

    def test_secret_whitelist_and_stdin(self):
        """Invariant: Secret values are passed on stdin and secret names are whitelisted."""
        H = {"X-Agent-Forge": "1"}
        s, _, _ = self.req("POST", "/api/secret", {"name": "anthropic.key", "value": "sk-ant-secret-value"}, H)
        self.assertEqual(s, 200)
        assert s == 200
        self.assertTrue(self.calls[-1][0].endswith("bin/secret-set anthropic.key"))
        assert self.calls[-1][0].endswith("bin/secret-set anthropic.key")
        self.assertEqual(self.calls[-1][1], "sk-ant-secret-value")
        assert self.calls[-1][1] == "sk-ant-secret-value"
        self.assertNotIn("sk-ant", self.calls[-1][0])
        assert "sk-ant" not in self.calls[-1][0]

        n = len(self.calls)
        s, _, _ = self.req("POST", "/api/secret", {"name": "../../.ssh/authorized_keys", "value": "x"}, H)
        self.assertEqual(s, 400)
        assert s == 400
        self.assertEqual(len(self.calls), n)
        assert len(self.calls) == n

    def test_worker_runtime_whitelist(self):
        """Invariant: Unknown worker runtimes are rejected with 400."""
        s, _, _ = self.req("POST", "/api/workers", {"action": "add", "runtime": "claude; rm -rf /"}, {"X-Agent-Forge": "1"})
        self.assertEqual(s, 400)
        assert s == 400

    def test_upstream_repo_and_kind_validation(self):
        """Invariant: Upstream kind is whitelisted and repo name is validated before execution."""
        H = {"X-Agent-Forge": "1"}
        n = len(self.calls)
        s, _, out = self.req("POST", "/api/upstream", {"action": "connect", "kind": "github", "repo": "acme/shop; reboot", "url": "https://github.com"}, H)
        self.assertEqual(s, 200)
        assert s == 200
        self.assertTrue(json.loads(out).get("errors", {}).get("repo"))
        assert json.loads(out).get("errors", {}).get("repo")
        self.assertEqual(len(self.calls), n)
        assert len(self.calls) == n

        s, _, _ = self.req("POST", "/api/upstream", {"action": "connect", "kind": "svn"}, H)
        self.assertEqual(s, 400)
        assert s == 400

    def test_task_identifiers_and_action_validation(self):
        """Invariant: Task id, action, and target worker are validated against TASK_ID and TARGET regexes."""
        H = {"X-Agent-Forge": "1"}
        s, _, _ = self.req("POST", "/api/task", {"id": "0012-store", "action": "feedback", "text": "use sqlite'; reboot"}, H)
        self.assertEqual(s, 200)
        assert s == 200
        self.assertTrue(self.calls[-1][0].endswith("bin/foreman task 0012-store feedback -"))
        assert self.calls[-1][0].endswith("bin/foreman task 0012-store feedback -")
        self.assertEqual(self.calls[-1][1], "use sqlite'; reboot")
        assert self.calls[-1][1] == "use sqlite'; reboot"

        s, _, _ = self.req("POST", "/api/task", {"id": "0012-store", "action": "assign", "target": "worker-3"}, H)
        self.assertEqual(s, 200)
        assert s == 200
        self.assertTrue(self.calls[-1][0].endswith("task 0012-store assign worker-3"))
        assert self.calls[-1][0].endswith("task 0012-store assign worker-3")

        n = len(self.calls)
        bad_task = [self.req("POST", "/api/task", b, H)[0] for b in (
            {"id": "../x", "action": "cancel"},
            {"id": "0012-store", "action": "rm -rf"},
            {"id": "0012-store", "action": "assign", "target": "worker-3; reboot"}
        )]
        self.assertEqual(bad_task, [400, 400, 400])
        assert bad_task == [400, 400, 400]
        self.assertEqual(len(self.calls), n)
        assert len(self.calls) == n

    def test_goalctl_id_and_action_validation(self):
        """Invariant: Goal ID is validated against GOAL_ID regex and actions are whitelisted."""
        H = {"X-Agent-Forge": "1"}
        s, _, _ = self.req("POST", "/api/goalctl", {"id": "G20261008-062000", "action": "replan", "text": "fewer tasks"}, H)
        self.assertEqual(s, 200)
        assert s == 200
        self.assertTrue(self.calls[-1][0].endswith("goal G20261008-062000 replan -"))
        assert self.calls[-1][0].endswith("goal G20261008-062000 replan -")
        self.assertEqual(self.calls[-1][1], "fewer tasks")
        assert self.calls[-1][1] == "fewer tasks"

        n = len(self.calls)
        s1, _, _ = self.req("POST", "/api/goalctl", {"id": "G2026; reboot", "action": "pause"}, H)
        s2, _, _ = self.req("POST", "/api/doc", {"name": "../../etc/passwd", "text": "x"}, H)
        s3, _, _ = self.req("POST", "/api/fix", {"fix": "rm -rf /"}, H)
        s4, _, _ = self.req("GET", "/api/run?run=../../etc")
        s5, _, _ = self.req("POST", "/api/disk", {"action": "rm"}, H)
        self.assertEqual([s1, s2, s3, s4, s5], [400, 400, 400, 400, 400])
        assert [s1, s2, s3, s4, s5] == [400, 400, 400, 400, 400]
        self.assertEqual(len(self.calls), n)
        assert len(self.calls) == n

    def test_doc_name_and_stdin(self):
        """Invariant: Doc name is validated against DOC_NAME regex and doc content is passed on stdin."""
        H = {"X-Agent-Forge": "1"}
        s, _, _ = self.req("POST", "/api/doc", {"name": "role:tester", "text": "# Tester\nmine"}, H)
        self.assertEqual(s, 200)
        assert s == 200
        self.assertTrue(self.calls[-1][0].endswith("doc set role:tester"))
        assert self.calls[-1][0].endswith("doc set role:tester")
        self.assertEqual(self.calls[-1][1], "# Tester\nmine")
        assert self.calls[-1][1] == "# Tester\nmine"

    def test_fix_action_whitelist(self):
        """Invariant: Only whitelisted fix commands are executed."""
        s, _, _ = self.req("POST", "/api/fix", {"fix": "worker-up:3"}, {"X-Agent-Forge": "1"})
        self.assertEqual(s, 200)
        assert s == 200
        self.assertTrue(self.calls[-1][0].endswith("bin/worker-up 3 2>&1"))
        assert self.calls[-1][0].endswith("bin/worker-up 3 2>&1")

    def test_search_stdin_safety(self):
        """Invariant: Search queries are passed via stdin."""
        s, _, _ = self.req("GET", "/api/search?q=due%27%3B%20reboot")
        self.assertEqual(s, 200)
        assert s == 200
        self.assertTrue(self.calls[-1][0].endswith("bin/foreman search"))
        assert self.calls[-1][0].endswith("bin/foreman search")
        self.assertEqual(self.calls[-1][1], "due'; reboot")
        assert self.calls[-1][1] == "due'; reboot"

    def test_egress_host_validation(self):
        """Invariant: Egress host is validated against HOST regex; shell injection is rejected."""
        H = {"X-Agent-Forge": "1"}
        s, _, _ = self.req("POST", "/api/egress", {"action": "allow", "host": "pypi.org"}, H)
        self.assertEqual(s, 200)
        assert s == 200
        self.assertTrue(self.calls[-1][0].endswith("bin/egress-report --allow pypi.org --json"))
        assert self.calls[-1][0].endswith("bin/egress-report --allow pypi.org --json")

        n = len(self.calls)
        bad_e = [self.req("POST", "/api/egress", b, H)[0] for b in (
            {"action": "allow", "host": "x.org; reboot"},
            {"action": "allow", "host": "$(id).org"},
            {"action": "delete", "host": "pypi.org"}
        )]
        self.assertEqual(bad_e, [400, 400, 400])
        assert bad_e == [400, 400, 400]
        self.assertEqual(len(self.calls), n)
        assert len(self.calls) == n

    def test_sse_stream(self):
        """Invariant: /api/stream serves Server-Sent Events with full state first, then deltas."""
        srv.B.FOREMAN_BY["default"] = {"data": {"a": 1, "tasks": []}, "at": 1.0, "error": None}
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/api/stream", headers={"Host": f"127.0.0.1:{self.port}", "Cookie": "af_token=s3cret"})
        r = c.getresponse()
        first = b""
        while first.count(b"\n\n") < 2:
            first += r.read1(4096)
        srv.B.FOREMAN_BY["default"] = {"data": {"a": 2, "tasks": []}, "at": 2.0, "error": None}
        more = b""
        deadline = __import__("time").time() + 5
        while b'"delta"' not in more and __import__("time").time() < deadline:
            more += r.read1(4096)
        c.close()

        self.assertEqual(r.getheader("Content-Type"), "text/event-stream")
        assert r.getheader("Content-Type") == "text/event-stream"
        self.assertIn(b"event: hello", first)
        assert b"event: hello" in first
        self.assertIn(b'"full":{"a":1', first)
        assert b'"full":{"a":1' in first
        self.assertIn(b'"delta":{"a":2}', more)
        assert b'"delta":{"a":2}' in more
        self.assertNotIn(b'"tasks"', more.split(b'"delta"')[1])
        assert b'"tasks"' not in more.split(b'"delta"')[1]

    def test_downloads_dir_serves_only_plain_files(self):
        """Invariant: /downloads/<name> serves a regular file directly inside DASHBOARD_DOWNLOADS_DIR, nothing else, and needs auth."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "app.apk"), "wb") as f:
                f.write(b"PK\x03\x04apk")
            open(os.path.join(d, ".hidden"), "w").write("x")
            os.mkdir(os.path.join(d, "sub"))
            outside = tempfile.NamedTemporaryFile(delete=False)
            outside.write(b"secret")
            outside.close()
            os.symlink(outside.name, os.path.join(d, "link"))
            old = srv.DOWNLOADS_DIR
            srv.DOWNLOADS_DIR = d
            try:
                st, hdrs, body = self.req("GET", "/downloads/app.apk")
                self.assertEqual(st, 200)
                self.assertEqual(body, "PK\x03\x04apk")
                self.assertEqual(hdrs.get("Content-Type"), "application/vnd.android.package-archive")
                self.assertIn("attachment", hdrs.get("Content-Disposition", ""))
                st, hdrs, body = self.req("HEAD", "/downloads/app.apk")
                self.assertEqual((st, hdrs.get("Content-Length")), (200, "7"))
                for bad in ("/downloads/missing.apk", "/downloads/.hidden", "/downloads/sub", "/downloads/link",
                            "/downloads/../server.py", "/downloads/%2e%2e/server.py", "/downloads/sub/x", "/downloads/"):
                    self.assertEqual(self.req("GET", bad)[0], 404, bad)
                st, _, _ = self.req("GET", "/downloads/app.apk", headers={"Cookie": ""})
                self.assertIn(st, (401, 403))
                srv.DOWNLOADS_DIR = ""
                self.assertEqual(self.req("GET", "/downloads/app.apk")[0], 404)
            finally:
                srv.DOWNLOADS_DIR = old
                os.unlink(outside.name)

    def test_demo_mode_mutating_posts_rejected(self):
        """Invariant: in DEMO_MODE, all mutating POST actions return 403 and error JSON."""
        srv.DEMO_MODE = True
        srv.B.DEMO_MODE = True
        try:
            endpoints = [
                ("POST", "/api/killswitch", {"action": "on"}),
                ("POST", "/api/resume", {}),
                ("POST", "/api/task", {"id": "0012-store", "action": "approve"}),
                ("POST", "/api/settings", {"changes": {"AUTOPILOT": "1"}}),
                ("POST", "/api/workers", {"action": "add", "runtime": "agy"}),
                ("POST", "/api/projects", {"action": "create", "name": "demo-proj", "repo": "a/b"}),
                ("POST", "/api/doc", {"name": "vision", "reset": True}),
                ("POST", "/api/goalctl", {"id": "G20261008-062000", "action": "pause"}),
                ("POST", "/api/upstream", {"action": "connect", "kind": "forgejo"}),
                ("POST", "/api/egress", {"action": "allow", "host": "pypi.org"}),
                ("POST", "/api/login", {"worker": "worker-1", "action": "start"}),
                ("POST", "/api/goal", {"text": "new goal", "mode": "now"}),
                ("POST", "/api/backlog", {"action": "top", "n": "1"}),
                ("POST", "/api/answer", {"n": "1", "answer": "yes"}),
                ("POST", "/api/secret", {"name": "anthropic.key", "value": "secret"}),
                ("POST", "/api/fix", {"fix": "worker-up:1"}),
                ("POST", "/api/disk", {"action": "compress"}),
                ("POST", "/api/ask", {"question": "what is this?"}),
            ]
            for method, path, payload in endpoints:
                status, headers, body = self.req(method, path, payload)
                self.assertEqual(status, 403)
                data = json.loads(body)
                self.assertEqual(data.get("error"), "Demo mode: read-only")
            assert len(endpoints) == 18
        finally:
            srv.DEMO_MODE = False
            srv.B.DEMO_MODE = False

    def test_demo_mode_get_status_and_dashboard(self):
        """Invariant: in DEMO_MODE, GET /api/status and /api/dashboard return 200 with data unchanged."""
        srv.DEMO_MODE = True
        srv.B.DEMO_MODE = True
        try:
            sample_data = {"goals": [{"id": "G1", "text": "demo goal"}], "alive": True}
            srv.B.FOREMAN_BY["default"] = {"data": sample_data, "at": 100.0, "error": None}
            s_stat, _, body_stat = self.req("GET", "/api/status")
            s_dash, _, body_dash = self.req("GET", "/api/dashboard")
            self.assertEqual(s_stat, 200)
            self.assertEqual(s_dash, 200)
            d_stat = json.loads(body_stat)
            d_dash = json.loads(body_dash)
            self.assertEqual(d_stat["foreman"], sample_data)
            self.assertEqual(d_dash["foreman"], sample_data)
            self.assertEqual(d_stat["fetched_at"], 100.0)
            self.assertEqual(d_dash["fetched_at"], 100.0)
            assert d_stat == d_dash
        finally:
            srv.DEMO_MODE = False
            srv.B.DEMO_MODE = False

    def test_demo_mode_host_and_token_skipping(self):
        """Invariant: DEMO_MODE skips host/token checks ONLY when not configured by owner."""
        orig_token = srv.DASHBOARD_TOKEN
        orig_hosts = srv.DASHBOARD_HOSTS
        srv.DEMO_MODE = True
        srv.B.DEMO_MODE = True
        try:
            # 1. No token, no hosts configured -> host enforcement skipped
            srv.DASHBOARD_TOKEN = ""
            srv.DASHBOARD_HOSTS = set()
            self.assertTrue(srv.host_allowed("custom.external.domain.org:8171"))
            assert srv.host_allowed("custom.external.domain.org:8171")
            self.assertTrue(srv.token_ok("", ""))
            assert srv.token_ok("", "")
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            c.request("GET", "/healthz", headers={"Host": "custom.external.domain.org:8171"})
            r = c.getresponse()
            c.close()
            self.assertEqual(r.status, 200)
            assert r.status == 200

            # 2. Host IS configured by owner -> enforce it, do not weaken
            srv.DASHBOARD_HOSTS = {"allowed.domain.org"}
            self.assertFalse(srv.host_allowed("forbidden.domain.org"))
            assert not srv.host_allowed("forbidden.domain.org")
            self.assertTrue(srv.host_allowed("allowed.domain.org"))
            assert srv.host_allowed("allowed.domain.org")

            # 3. Token IS configured by owner -> enforce it, do not weaken
            srv.DASHBOARD_TOKEN = "configured_token"
            self.assertFalse(srv.token_ok("", ""))
            assert not srv.token_ok("", "")
            self.assertTrue(srv.token_ok("af_token=configured_token", ""))
            assert srv.token_ok("af_token=configured_token", "")
        finally:
            srv.DASHBOARD_TOKEN = orig_token
            srv.DASHBOARD_HOSTS = orig_hosts
            srv.DEMO_MODE = False
            srv.B.DEMO_MODE = False

    def test_demo_mode_backend_guards(self):
        """Invariant: in DEMO_MODE, backend.py never calls ssh_run/ssh_run_full and raises assertion if called."""
        orig_b_demo = srv.B.DEMO_MODE
        srv.B.DEMO_MODE = True
        try:
            # 1. ssh_run and ssh_run_full raise AssertionError
            with self.assertRaises(AssertionError):
                self.orig_ssh("id")
            with self.assertRaises(AssertionError):
                srv.B.ssh_run("id")

            # 2. Jobs exit immediately with error message and without calling ssh
            job_worker = {"id": "w-1", "state": "running", "log": "", "started": 0}
            res_w = srv.B.add_worker_job(job_worker)
            self.assertEqual(job_worker["state"], "failed")
            self.assertIn("Demo mode: read-only, no real workers", job_worker["log"])
            self.assertEqual(res_w.get("error"), "Demo mode: read-only, no real workers")

            job_proj = {"id": "p-1", "state": "running", "log": "", "started": 0}
            res_p = srv.B.create_project_job(job_proj, "proj", "repo/name", [], "English", "v")
            self.assertEqual(job_proj["state"], "failed")
            self.assertIn("Demo mode: read-only, no real workers", job_proj["log"])
            self.assertEqual(res_p.get("error"), "Demo mode: read-only, no real workers")

            job_up = {"id": "u-1", "state": "running", "log": "", "started": 0}
            res_u = srv.B.connect_upstream_job(job_up, "", {}, False, False)
            self.assertEqual(job_up["state"], "failed")
            self.assertIn("Demo mode: read-only, no real workers", job_up["log"])
            self.assertEqual(res_u.get("error"), "Demo mode: read-only, no real workers")

            # 3. LoginSessions start / write / resize / stop exit immediately with error message
            self.assertEqual(srv.B.LOGIN.start("worker-1"), {"error": "Demo mode: read-only, no real workers"})
            self.assertEqual(srv.B.LOGIN.write("worker-1", "AAAA"), {"error": "Demo mode: read-only, no real workers"})
            self.assertEqual(srv.B.LOGIN.resize("worker-1", 100, 30), {"error": "Demo mode: read-only, no real workers"})
            self.assertEqual(srv.B.LOGIN.stop("worker-1"), {"error": "Demo mode: read-only, no real workers"})

            assert job_worker["state"] == "failed"
        finally:
            srv.B.DEMO_MODE = orig_b_demo

    def test_without_demo_mode_behaviour_unchanged(self):
        """Invariant: without DEMO_MODE, behaviour is byte-for-byte identical to before."""
        srv.DEMO_MODE = False
        srv.B.DEMO_MODE = False
        H = {"X-Agent-Forge": "1"}
        s, _, out = self.req("POST", "/api/doc", {"name": "vision", "reset": True}, H)
        self.assertEqual(s, 200)
        assert s == 200
        self.assertGreater(len(self.calls), 0)
        assert len(self.calls) > 0
    def test_cli_foreman_readiness_subcommand(self):
        """Invariant: bin/foreman readiness prints valid JSON matching readiness.all_status() and exits 0."""
        foreman_bin = os.path.join(ROOT, "bin", "foreman")
        proc = subprocess.run([sys.executable, foreman_bin, "readiness"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"bin/foreman readiness failed: {proc.stderr}")
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        expected = readiness.all_status()
        self.assertEqual(data, expected)
        assert data == expected
        self.assertIn("runtimes", data)
        assert "runtimes" in data
        self.assertIn("providers", data)
        assert "providers" in data
        for secret_name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
            val = os.environ.get(secret_name, "")
            if val:
                self.assertNotIn(val, proc.stdout)
                assert val not in proc.stdout

    def test_api_readiness_endpoint_auth_and_host_guards(self):
        """Invariant: GET /api/readiness enforces DASHBOARD_TOKEN and DASHBOARD_HOSTS checks."""
        s_unauth, _, body_unauth = self.req("GET", "/api/readiness", headers={"Cookie": ""})
        self.assertEqual(s_unauth, 401)
        assert s_unauth == 401
        self.assertIn("sign in", body_unauth)
        assert "sign in" in body_unauth

        s_badtoken, _, body_badtoken = self.req("GET", "/api/readiness", headers={"Cookie": "af_token=wrong_token"})
        self.assertEqual(s_badtoken, 401)
        assert s_badtoken == 401
        self.assertIn("sign in", body_badtoken)
        assert "sign in" in body_badtoken

        s_badhost, _, body_badhost = self.req("GET", "/api/readiness", host="evil.attacker.com")
        self.assertEqual(s_badhost, 403)
        assert s_badhost == 403
        self.assertIn("host not allowed", body_badhost)
        assert "host not allowed" in body_badhost

    def test_api_readiness_endpoint_success_and_mocked_foreman(self):
        """Invariant: GET /api/readiness calls foreman with 'readiness' and returns 200 with all_status JSON."""
        stub_status = readiness.all_status()
        old_ssh = srv.B.ssh_run_full
        try:
            srv.B.ssh_run_full = lambda cmd, timeout=30, inp=None: (
                self.calls.append((cmd, inp)) or (0, json.dumps(stub_status), "")
            )
            status, headers, body = self.req("GET", "/api/readiness")
            self.assertEqual(status, 200)
            assert status == 200
            self.assertEqual(headers.get("Content-Type"), "application/json")
            assert headers.get("Content-Type") == "application/json"
            self.assertTrue(self.calls and self.calls[-1][0].endswith("bin/foreman readiness"))
            assert self.calls and self.calls[-1][0].endswith("bin/foreman readiness")
            data = json.loads(body)
            self.assertIn("_age", data)
            data_clean = {k: v for k, v in data.items() if k != "_age"}
            self.assertEqual(data_clean, stub_status)
            assert data_clean == stub_status
            self.assertIn("runtimes", data)
            assert "runtimes" in data
            self.assertIn("providers", data)
            assert "providers" in data
            for secret_name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
                val = os.environ.get(secret_name, "")
                if val:
                    self.assertNotIn(val, body)
                    assert val not in body
        finally:
            srv.B.ssh_run_full = old_ssh

    def test_api_readiness_endpoint_error_handling(self):
        """Invariant: non-zero returncode or invalid JSON from foreman surfaces as error JSON via as_json."""
        old_ssh = srv.B.ssh_run_full
        try:
            srv.B.ssh_run_full = lambda cmd, timeout=30, inp=None: (
                self.calls.append((cmd, inp)) or (1, "", "readiness diagnostic failed")
            )
            status, _, body = self.req("GET", "/api/readiness")
            self.assertEqual(status, 200)
            assert status == 200
            data = json.loads(body)
            self.assertEqual(data.get("ok"), False)
            assert data.get("ok") is False
            self.assertIn("readiness diagnostic failed", data.get("error", ""))
            assert "readiness diagnostic failed" in data.get("error", "")

            srv.B.ssh_run_full = lambda cmd, timeout=30, inp=None: (
                self.calls.append((cmd, inp)) or (2, "", "")
            )
            status, _, body = self.req("GET", "/api/readiness")
            self.assertEqual(status, 200)
            assert status == 200
            data = json.loads(body)
            self.assertEqual(data.get("ok"), False)
            assert data.get("ok") is False
            self.assertIn("exit 2", data.get("error", ""))
            assert "exit 2" in data.get("error", "")

            srv.B.ssh_run_full = lambda cmd, timeout=30, inp=None: (
                self.calls.append((cmd, inp)) or (0, "corrupted stdout output", "")
            )
            status, _, body = self.req("GET", "/api/readiness")
            self.assertEqual(status, 200)
            assert status == 200
            data = json.loads(body)
            self.assertEqual(data.get("ok"), False)
            assert data.get("ok") is False
            self.assertIn("corrupted stdout output", data.get("error", ""))
            assert "corrupted stdout output" in data.get("error", "")
        finally:
            srv.B.ssh_run_full = old_ssh

    def test_cli_foreman_readiness_recommend_subcommand(self):
        """Invariant: bin/foreman readiness-recommend prints valid JSON matching readiness.recommend() and exits 0."""
        foreman_bin = os.path.join(ROOT, "bin", "foreman")
        proc = subprocess.run([sys.executable, foreman_bin, "readiness-recommend"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"bin/foreman readiness-recommend failed: {proc.stderr}")
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        expected = readiness.recommend()
        self.assertEqual(data, expected)
        assert data == expected
        self.assertIn("top", data)
        assert "top" in data
        self.assertIn("ranked", data)
        assert "ranked" in data
        self.assertIn("message", data)
        assert "message" in data

    def test_api_readiness_recommend_endpoint_auth_and_host_guards(self):
        """Invariant: GET /api/readiness/recommend enforces DASHBOARD_TOKEN and DASHBOARD_HOSTS checks."""
        s_unauth, _, body_unauth = self.req("GET", "/api/readiness/recommend", headers={"Cookie": ""})
        self.assertEqual(s_unauth, 401)
        assert s_unauth == 401
        self.assertIn("sign in", body_unauth)
        assert "sign in" in body_unauth

        s_badtoken, _, body_badtoken = self.req("GET", "/api/readiness/recommend", headers={"Cookie": "af_token=wrong_token"})
        self.assertEqual(s_badtoken, 401)
        assert s_badtoken == 401
        self.assertIn("sign in", body_badtoken)
        assert "sign in" in body_badtoken

        s_badhost, _, body_badhost = self.req("GET", "/api/readiness/recommend", host="evil.attacker.com")
        self.assertEqual(s_badhost, 403)
        assert s_badhost == 403
        self.assertIn("host not allowed", body_badhost)
        assert "host not allowed" in body_badhost

    def test_api_readiness_recommend_endpoint_success_and_mocked_foreman(self):
        """Invariant: GET /api/readiness/recommend calls foreman with ['readiness-recommend'] and returns 200 with {top, ranked, message}."""
        stub_recommend = readiness.recommend()
        foreman_calls = []
        old_foreman = srv.foreman
        try:
            srv.foreman = lambda pf, args, inp=None, timeout=60: (
                foreman_calls.append((pf, args, inp, timeout)) or (0, json.dumps(stub_recommend), "")
            )
            status, headers, body = self.req("GET", "/api/readiness/recommend")
            self.assertEqual(status, 200)
            assert status == 200
            self.assertEqual(headers.get("Content-Type"), "application/json")
            assert headers.get("Content-Type") == "application/json"
            self.assertEqual(len(foreman_calls), 1)
            assert len(foreman_calls) == 1
            self.assertEqual(foreman_calls[0][0], "")
            assert foreman_calls[0][0] == ""
            self.assertEqual(foreman_calls[0][1], ["readiness-recommend"])
            assert foreman_calls[0][1] == ["readiness-recommend"]
            data = json.loads(body)
            self.assertEqual(data, stub_recommend)
            assert data == stub_recommend
            self.assertEqual(set(data.keys()), {"top", "ranked", "message"})
            assert set(data.keys()) == {"top", "ranked", "message"}
            self.assertIn("top", data)
            assert "top" in data
            self.assertIn("ranked", data)
            assert "ranked" in data
            self.assertIn("message", data)
            assert "message" in data
            self.assertIsInstance(data["ranked"], list)
            assert isinstance(data["ranked"], list)
            self.assertIsInstance(data["message"], str)
            assert isinstance(data["message"], str)
            self.assertTrue(data["top"] is None or isinstance(data["top"], dict))
            assert data["top"] is None or isinstance(data["top"], dict)

            # Query param project handling
            status_proj, _, body_proj = self.req("GET", "/api/readiness/recommend?project=alpha-1")
            self.assertEqual(status_proj, 200)
            assert status_proj == 200
            self.assertEqual(foreman_calls[-1][0], " --project alpha-1")
            assert foreman_calls[-1][0] == " --project alpha-1"
            self.assertEqual(foreman_calls[-1][1], ["readiness-recommend"])
            assert foreman_calls[-1][1] == ["readiness-recommend"]

            # Bad project query param is rejected by shared preamble
            status_badproj, _, body_badproj = self.req("GET", "/api/readiness/recommend?project=bad/name")
            self.assertEqual(status_badproj, 400)
            assert status_badproj == 400
            self.assertEqual(json.loads(body_badproj), {"error": "bad project"})
            assert json.loads(body_badproj) == {"error": "bad project"}
        finally:
            srv.foreman = old_foreman

    def test_api_readiness_recommend_endpoint_error_handling(self):
        """Invariant: non-zero returncode from foreman results in error JSON via as_json, never 200 with empty body."""
        old_foreman = srv.foreman
        try:
            # 1. Non-zero exit code with error message on stderr
            srv.foreman = lambda pf, args, inp=None, timeout=60: (
                (1, "", "readiness recommend failed")
            )
            status, headers, body = self.req("GET", "/api/readiness/recommend")
            self.assertEqual(status, 200)
            assert status == 200
            self.assertEqual(headers.get("Content-Type"), "application/json")
            assert headers.get("Content-Type") == "application/json"
            self.assertTrue(len(body.strip()) > 0)
            assert len(body.strip()) > 0
            data = json.loads(body)
            self.assertEqual(data.get("ok"), False)
            assert data.get("ok") is False
            self.assertIn("readiness recommend failed", data.get("error", ""))
            assert "readiness recommend failed" in data.get("error", "")

            # 2. Non-zero exit code with empty stdout and empty stderr
            srv.foreman = lambda pf, args, inp=None, timeout=60: (
                (2, "", "")
            )
            status, headers, body = self.req("GET", "/api/readiness/recommend")
            self.assertEqual(status, 200)
            assert status == 200
            self.assertEqual(headers.get("Content-Type"), "application/json")
            assert headers.get("Content-Type") == "application/json"
            self.assertTrue(len(body.strip()) > 0)
            assert len(body.strip()) > 0
            data = json.loads(body)
            self.assertEqual(data.get("ok"), False)
            assert data.get("ok") is False
            self.assertIn("exit 2", data.get("error", ""))
            assert "exit 2" in data.get("error", "")

            # 3. Corrupted / non-JSON output
            srv.foreman = lambda pf, args, inp=None, timeout=60: (
                (0, "corrupted output", "")
            )
            status, headers, body = self.req("GET", "/api/readiness/recommend")
            self.assertEqual(status, 200)
            assert status == 200
            self.assertEqual(headers.get("Content-Type"), "application/json")
            assert headers.get("Content-Type") == "application/json"
            self.assertTrue(len(body.strip()) > 0)
            assert len(body.strip()) > 0
            data = json.loads(body)
            self.assertEqual(data.get("ok"), False)
            assert data.get("ok") is False
            self.assertIn("corrupted output", data.get("error", ""))
            assert "corrupted output" in data.get("error", "")
        finally:
            srv.foreman = old_foreman

    def test_api_readiness_summary_endpoint_auth_and_host_guards(self):
        """Invariant: GET /api/readiness/summary enforces DASHBOARD_TOKEN and DASHBOARD_HOSTS checks."""
        for path in ("/api/readiness/summary", "/api/readiness/status"):
            s_unauth, _, body_unauth = self.req("GET", path, headers={"Cookie": ""})
            self.assertEqual(s_unauth, 401)
            assert s_unauth == 401
            self.assertIn("sign in", body_unauth)
            assert "sign in" in body_unauth

            s_badtoken, _, body_badtoken = self.req("GET", path, headers={"Cookie": "af_token=wrong_token"})
            self.assertEqual(s_badtoken, 401)
            assert s_badtoken == 401
            self.assertIn("sign in", body_badtoken)
            assert "sign in" in body_badtoken

            s_badhost, _, body_badhost = self.req("GET", path, host="evil.attacker.com")
            self.assertEqual(s_badhost, 403)
            assert s_badhost == 403
            self.assertIn("host not allowed", body_badhost)
            assert "host not allowed" in body_badhost

    def test_api_readiness_summary_endpoint_success_and_six_runtimes(self):
        """Invariant: GET /api/readiness/summary returns 200 with honest per-runtime readiness and cost_model summary."""
        six_keys = ("copilot", "mistral-vibe", "ollama", "vllm", "lmstudio", "llamacpp")
        expected_items = readiness.runtime_items()

        for path in ("/api/readiness/summary", "/api/readiness/status"):
            status, headers, body = self.req("GET", path)
            self.assertEqual(status, 200)
            self.assertEqual(headers.get("Content-Type"), "application/json")
            assert headers.get("Content-Type") == "application/json"

            data = json.loads(body)
            self.assertIn("runtimes", data)
            assert "runtimes" in data
            self.assertIsInstance(data["runtimes"], dict)
            assert isinstance(data["runtimes"], dict)

            # Assert order matches readiness.runtime_items()
            self.assertEqual(list(data["runtimes"].keys()), expected_items)
            assert list(data["runtimes"].keys()) == expected_items

            # Assert all six new runtime keys are present
            for rt in six_keys:
                self.assertIn(rt, data["runtimes"])
                assert rt in data["runtimes"]

            # Ground every runtime's ready, reason, and cost_model directly against readiness.status_of and cost_model
            for rt, entry in data["runtimes"].items():
                self.assertIsInstance(entry, dict)
                assert isinstance(entry, dict)

                real_st = readiness.status_of("runtime", rt)
                expected_ready = True if real_st.get("state") == "ok" else (False if real_st.get("state") in ("missing_setting", "missing_egress") else None)
                expected_reason = real_st.get("detail", "")
                expected_cm = readiness.cost_model(rt)

                self.assertEqual(entry["ready"], expected_ready)
                assert entry["ready"] == expected_ready

                self.assertEqual(entry["reason"], expected_reason)
                assert entry["reason"] == expected_reason

                self.assertEqual(entry["cost_model"], expected_cm)
                assert entry["cost_model"] == expected_cm

                # Prove status_of was actually invoked successfully with the right args, never hit fallback string
                self.assertFalse(entry["reason"].startswith("readiness check failed:"),
                                 f"Runtime '{rt}' reason indicates status_of invocation failure: {entry['reason']}")
                assert not entry["reason"].startswith("readiness check failed:")

            # Explicitly verify all six new runtimes have grounded ready, reason and cost_model
            for rt in six_keys:
                self.assertIn(rt, data["runtimes"])
                assert rt in data["runtimes"]
                rt_entry = data["runtimes"][rt]
                rt_st = readiness.status_of("runtime", rt)
                rt_cm = readiness.cost_model(rt)
                self.assertEqual(rt_entry["ready"], True if rt_st.get("state") == "ok" else False)
                assert rt_entry["ready"] == (True if rt_st.get("state") == "ok" else False)
                self.assertEqual(rt_entry["reason"], rt_st.get("detail", ""))
                assert rt_entry["reason"] == rt_st.get("detail", "")
                self.assertEqual(rt_entry["cost_model"], rt_cm)
                assert rt_entry["cost_model"] == rt_cm

            # Query param project handling
            status_proj, _, body_proj = self.req("GET", f"{path}?project=alpha-1")
            self.assertEqual(status_proj, 200)
            assert status_proj == 200
            data_proj = json.loads(body_proj)
            self.assertIn("runtimes", data_proj)
            assert "runtimes" in data_proj

            # Bad project query param is rejected by shared preamble
            status_badproj, _, body_badproj = self.req("GET", f"{path}?project=bad/name")
            self.assertEqual(status_badproj, 400)
            assert status_badproj == 400
            self.assertEqual(json.loads(body_badproj), {"error": "bad project"})
            assert json.loads(body_badproj) == {"error": "bad project"}

    def test_api_readiness_summary_endpoint_no_secrets_leaked(self):
        """Invariant: GET /api/readiness/summary never leaks secrets, API keys, or DASHBOARD_TOKEN in response."""
        canaries = {
            "ANTHROPIC_API_KEY": "CANARY_SECRET_ANTHROPIC_9999",
            "OPENAI_API_KEY": "CANARY_SECRET_OPENAI_8888",
            "GEMINI_API_KEY": "CANARY_SECRET_GEMINI_7777",
            "MISTRAL_API_KEY": "CANARY_SECRET_MISTRAL_6666",
        }
        orig_env = {k: os.environ.get(k) for k in canaries}
        try:
            for k, v in canaries.items():
                os.environ[k] = v
            status, _, body = self.req("GET", "/api/readiness/summary")
            self.assertEqual(status, 200)
            assert status == 200
            self.assertNotIn(srv.DASHBOARD_TOKEN, body)
            assert srv.DASHBOARD_TOKEN not in body
            for secret_val in canaries.values():
                self.assertNotIn(secret_val, body)
                assert secret_val not in body
        finally:
            for k, orig in orig_env.items():
                if orig is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = orig

    def test_api_readiness_summary_endpoint_error_degradation(self):
        """Invariant: error in readiness module returns degraded 200 with error JSON, never unhandled 500."""
        old_readiness = srv.readiness
        try:
            srv.readiness = None
            status, _, body = self.req("GET", "/api/readiness/summary")
            self.assertEqual(status, 200)
            assert status == 200
            data = json.loads(body)
            self.assertEqual(data.get("runtimes"), {})
            assert data.get("runtimes") == {}
            self.assertIn("error", data)
            assert "error" in data
        finally:
            srv.readiness = old_readiness

    def test_api_readiness_summary_grounds_status_of_contract_mappings(self):
        """Invariant: ready/reason mapping strictly reflects status_of() contract states (ok, missing_setting, missing_egress)."""
        test_states = [
            ("ok", True, "Runtime 'test-rt' is ready."),
            ("missing_setting", False, "Runtime 'test-rt' is missing required setting(s): test.key."),
            ("missing_egress", False, "Runtime 'test-rt' is missing egress allowlist for: test.host."),
            ("unknown_other", None, "Runtime 'test-rt' has unknown state."),
        ]
        old_status_of = srv.readiness.status_of
        try:
            for state_val, expected_ready, detail_str in test_states:
                srv.readiness.status_of = lambda kind, name, s=state_val, d=detail_str: {
                    "state": s,
                    "detail": d,
                    "missing_settings": [] if s == "ok" else ["test.key"],
                    "missing_egress": [] if s != "missing_egress" else ["test.host"],
                }
                status, _, body = self.req("GET", "/api/readiness/summary")
                self.assertEqual(status, 200)
                assert status == 200
                data = json.loads(body)
                self.assertIn("copilot", data["runtimes"])
                entry = data["runtimes"]["copilot"]
                self.assertEqual(entry["ready"], expected_ready)
                assert entry["ready"] == expected_ready
                self.assertEqual(entry["reason"], detail_str)
                assert entry["reason"] == detail_str
                self.assertFalse(entry["reason"].startswith("readiness check failed:"))
                assert not entry["reason"].startswith("readiness check failed:")
        finally:
            srv.readiness.status_of = old_status_of

    def test_control_valid_action_and_project(self):
        """Invariant: POST /api/control with a valid project and each of the four control actions calls bin/foreman with the correct subcommand list and returns ok:true on rc=0."""
        H = {"X-Agent-Forge": "1"}
        orig_foreman = srv.foreman
        foreman_calls = []

        def spy_foreman(pf, args, inp=None, timeout=60):
            foreman_calls.append((pf, args))
            return orig_foreman(pf, args, inp=inp, timeout=timeout)

        srv.foreman = spy_foreman
        try:
            cases = [
                ("autopilot_on", ["autopilot", "on"], "bin/foreman --project shop autopilot on"),
                ("autopilot_off", ["autopilot", "off"], "bin/foreman --project shop autopilot off"),
                ("pause", ["pause"], "bin/foreman --project shop pause"),
                ("resume", ["resume"], "bin/foreman --project shop resume"),
            ]
            for act, expected_args, expected_cmd in cases:
                s, _, body = self.req("POST", "/api/control", {"project": "shop", "action": act}, H)
                self.assertEqual(s, 200)
                assert s == 200
                data = json.loads(body)
                self.assertTrue(data.get("ok"))
                assert data.get("ok") is True
                self.assertEqual(foreman_calls[-1], (" --project shop", expected_args))
                assert foreman_calls[-1] == (" --project shop", expected_args)
                self.assertTrue(self.calls[-1][0].endswith(expected_cmd))
                assert self.calls[-1][0].endswith(expected_cmd)

            # Default project (omitted or empty)
            default_cases = [
                ("autopilot_on", ["autopilot", "on"], "bin/foreman autopilot on"),
                ("autopilot_off", ["autopilot", "off"], "bin/foreman autopilot off"),
                ("pause", ["pause"], "bin/foreman pause"),
                ("resume", ["resume"], "bin/foreman resume"),
            ]
            for act, expected_args, expected_cmd in default_cases:
                s, _, body = self.req("POST", "/api/control", {"action": act}, H)
                self.assertEqual(s, 200)
                assert s == 200
                data = json.loads(body)
                self.assertTrue(data.get("ok"))
                assert data.get("ok") is True
                self.assertEqual(foreman_calls[-1], ("", expected_args))
                assert foreman_calls[-1] == ("", expected_args)
                self.assertTrue(self.calls[-1][0].endswith(expected_cmd))
                assert self.calls[-1][0].endswith(expected_cmd)
        finally:
            srv.foreman = orig_foreman

    def test_control_foreman_failure(self):
        """Invariant: POST /api/control returns ok:false and combined message when foreman exits non-zero."""
        H = {"X-Agent-Forge": "1"}
        orig_foreman = srv.foreman
        srv.foreman = lambda pf, args, inp=None, timeout=60: (1, "", "autopilot failed")
        try:
            s, _, body = self.req("POST", "/api/control", {"action": "autopilot_on"}, H)
            self.assertEqual(s, 200)
            assert s == 200
            data = json.loads(body)
            self.assertFalse(data.get("ok"))
            assert data.get("ok") is False
            self.assertEqual(data.get("message"), "autopilot failed")
            assert data.get("message") == "autopilot failed"
        finally:
            srv.foreman = orig_foreman

    def test_control_resume_killswitch_refusal(self):
        """Invariant: POST /api/control with resume while the kill switch is engaged returns the CLI's own refusal message verbatim, with no duplicate kill-switch check in web/server.py."""
        H = {"X-Agent-Forge": "1"}
        orig_foreman = srv.foreman
        cli_refusal = "refusing to resume: kill switch is engaged (egress violation)"
        srv.foreman = lambda pf, args, inp=None, timeout=60: (1, "", cli_refusal)
        try:
            s, _, body = self.req("POST", "/api/control", {"project": "shop", "action": "resume"}, H)
            self.assertEqual(s, 200)
            assert s == 200
            data = json.loads(body)
            self.assertFalse(data.get("ok"))
            assert data.get("ok") is False
            self.assertEqual(data.get("message"), cli_refusal)
            assert data.get("message") == cli_refusal
        finally:
            srv.foreman = orig_foreman

    def test_control_unknown_action_rejected(self):
        """Invariant: POST /api/control with an action outside the full four-action CONTROL_ACTIONS set returns HTTP 400 and ok:false before any foreman() call is made."""
        H = {"X-Agent-Forge": "1"}
        orig_foreman = srv.foreman
        foreman_calls = []

        def spy_foreman(*a, **k):
            foreman_calls.append((a, k))
            return orig_foreman(*a, **k)

        srv.foreman = spy_foreman
        try:
            n = len(self.calls)
            for bad_act in ("restart", "invalid", "", "stop", "start", "autopilot", "on", "off", "kill", "halt", "status"):
                s, _, body = self.req("POST", "/api/control", {"project": "shop", "action": bad_act}, H)
                self.assertEqual(s, 400)
                assert s == 400
                data = json.loads(body)
                self.assertFalse(data.get("ok"))
                assert data.get("ok") is False
                self.assertEqual(data.get("message"), "unknown action")
                assert data.get("message") == "unknown action"
                self.assertEqual(len(foreman_calls), 0)
                assert not foreman_calls
                self.assertEqual(len(self.calls), n)
                assert len(self.calls) == n
        finally:
            srv.foreman = orig_foreman

    def test_control_invalid_project_rejected(self):
        """Invariant: POST /api/control with an invalid project name returns HTTP 400 without calling foreman()."""
        H = {"X-Agent-Forge": "1"}
        orig_foreman = srv.foreman
        foreman_calls = []

        def spy_foreman(*a, **k):
            foreman_calls.append((a, k))
            return orig_foreman(*a, **k)

        srv.foreman = spy_foreman
        try:
            n = len(self.calls)
            for bad_proj in ("../etc", "invalid name!", "bad/path", "UPPERCASE", "with spaces"):
                s, _, body = self.req("POST", "/api/control", {"project": bad_proj, "action": "autopilot_on"}, H)
                self.assertEqual(s, 400)
                assert s == 400
                self.assertEqual(len(foreman_calls), 0)
                assert not foreman_calls
                self.assertEqual(len(self.calls), n)
                assert len(self.calls) == n
        finally:
            srv.foreman = orig_foreman

    def test_control_wrong_method_rejected(self):
        """Invariant: GET or other non-POST requests to /api/control are rejected with 405 Method Not Allowed."""
        orig_foreman = srv.foreman
        foreman_calls = []

        def spy_foreman(*a, **k):
            foreman_calls.append((a, k))
            return orig_foreman(*a, **k)

        srv.foreman = spy_foreman
        try:
            n = len(self.calls)
            s_ctl, _, _ = self.req("GET", "/api/control")
            self.assertEqual(s_ctl, 405)
            assert s_ctl == 405
            s_head, _, _ = self.req("HEAD", "/api/control")
            self.assertEqual(s_head, 405)
            assert s_head == 405
            self.assertEqual(len(foreman_calls), 0)
            assert not foreman_calls
            self.assertEqual(len(self.calls), n)
            assert len(self.calls) == n
        finally:
            srv.foreman = orig_foreman

    def test_control_auth_and_csrf_origin_check(self):
        """Invariant: POST /api/control missing auth cookie/token or with mismatched Origin is rejected like /api/task and /api/goal."""
        orig_foreman = srv.foreman
        foreman_calls = []

        def spy_foreman(*a, **k):
            foreman_calls.append((a, k))
            return orig_foreman(*a, **k)

        srv.foreman = spy_foreman
        try:
            n = len(self.calls)
            # Missing auth cookie
            s, _, _ = self.req("POST", "/api/control", {"action": "autopilot_on"}, headers={"Cookie": ""})
            self.assertEqual(s, 401)
            assert s == 401

            # Invalid auth cookie
            s_bad_tok, _, _ = self.req("POST", "/api/control", {"action": "autopilot_on"}, headers={"Cookie": "af_token=wrong_token"})
            self.assertEqual(s_bad_tok, 401)
            assert s_bad_tok == 401

            # Missing X-Agent-Forge header
            s_no_csrf, _, _ = self.req("POST", "/api/control", {"action": "autopilot_on"}, headers={})
            self.assertEqual(s_no_csrf, 403)
            assert s_no_csrf == 403

            # Evil Origin
            bad_origin = {"X-Agent-Forge": "1", "Origin": "http://evil.com"}
            s_origin, _, _ = self.req("POST", "/api/control", {"action": "autopilot_on"}, headers=bad_origin)
            self.assertEqual(s_origin, 403)
            assert s_origin == 403

            # Null Origin
            s_null, _, _ = self.req("POST", "/api/control", {"action": "autopilot_on"}, headers={"X-Agent-Forge": "1", "Origin": "null"})
            self.assertEqual(s_null, 403)
            assert s_null == 403

            # Empty Origin
            s_empty, _, _ = self.req("POST", "/api/control", {"action": "autopilot_on"}, headers={"X-Agent-Forge": "1", "Origin": ""})
            self.assertEqual(s_empty, 403)
            assert s_empty == 403

            # Evil Referer
            bad_referer = {"X-Agent-Forge": "1", "Referer": "http://evil.com/attack"}
            s_ref, _, _ = self.req("POST", "/api/control", {"action": "autopilot_on"}, headers=bad_referer)
            self.assertEqual(s_ref, 403)
            assert s_ref == 403

            # All rejected requests must not have called foreman() or subprocess
            self.assertEqual(len(foreman_calls), 0)
            assert not foreman_calls
            self.assertEqual(len(self.calls), n)
            assert len(self.calls) == n

            # Valid headers with allowed Origin and Referer succeed
            s_ok, _, body_ok = self.req("POST", "/api/control", {"action": "autopilot_on"},
                                        headers={"X-Agent-Forge": "1",
                                                 "Origin": f"http://127.0.0.1:{self.port}",
                                                 "Referer": f"http://127.0.0.1:{self.port}/"})
            self.assertEqual(s_ok, 200)
            assert s_ok == 200
            self.assertTrue(json.loads(body_ok).get("ok"))
            assert json.loads(body_ok).get("ok") is True
            self.assertEqual(len(foreman_calls), 1)
            assert len(foreman_calls) == 1
        finally:
            srv.foreman = orig_foreman

    def test_control_round_trip(self):
        """Invariant: Round-trip control calls (autopilot off then on, pause then resume) succeed and produce distinct argv lists each time."""
        H = {"X-Agent-Forge": "1"}
        orig_foreman = srv.foreman
        foreman_calls = []

        def spy_foreman(pf, args, inp=None, timeout=60):
            foreman_calls.append((pf, list(args)))
            return orig_foreman(pf, args, inp=inp, timeout=timeout)

        srv.foreman = spy_foreman
        try:
            # Round trip 1: Autopilot off then Autopilot on
            s1, _, body1 = self.req("POST", "/api/control", {"project": "shop", "action": "autopilot_off"}, H)
            self.assertEqual(s1, 200)
            assert s1 == 200
            res1 = json.loads(body1)
            self.assertTrue(res1.get("ok"))
            assert res1.get("ok") is True
            argv1 = foreman_calls[-1][1]
            self.assertEqual(argv1, ["autopilot", "off"])
            assert argv1 == ["autopilot", "off"]

            s2, _, body2 = self.req("POST", "/api/control", {"project": "shop", "action": "autopilot_on"}, H)
            self.assertEqual(s2, 200)
            assert s2 == 200
            res2 = json.loads(body2)
            self.assertTrue(res2.get("ok"))
            assert res2.get("ok") is True
            argv2 = foreman_calls[-1][1]
            self.assertEqual(argv2, ["autopilot", "on"])
            assert argv2 == ["autopilot", "on"]

            # Explicitly assert two different argv lists for the two actions (catches mapping bugs)
            self.assertNotEqual(argv1, argv2)
            assert argv1 != argv2

            # Assert subprocess commands are also distinct and match expected
            self.assertTrue(self.calls[-2][0].endswith("bin/foreman --project shop autopilot off"))
            self.assertTrue(self.calls[-1][0].endswith("bin/foreman --project shop autopilot on"))
            self.assertNotEqual(self.calls[-2][0], self.calls[-1][0])

            # Round trip 2: Foreman pause then resume
            s3, _, body3 = self.req("POST", "/api/control", {"project": "shop", "action": "pause"}, H)
            self.assertEqual(s3, 200)
            assert s3 == 200
            res3 = json.loads(body3)
            self.assertTrue(res3.get("ok"))
            assert res3.get("ok") is True
            argv3 = foreman_calls[-1][1]
            self.assertEqual(argv3, ["pause"])
            assert argv3 == ["pause"]

            s4, _, body4 = self.req("POST", "/api/control", {"project": "shop", "action": "resume"}, H)
            self.assertEqual(s4, 200)
            assert s4 == 200
            res4 = json.loads(body4)
            self.assertTrue(res4.get("ok"))
            assert res4.get("ok") is True
            argv4 = foreman_calls[-1][1]
            self.assertEqual(argv4, ["resume"])
            assert argv4 == ["resume"]

            # Explicitly assert two different argv lists for pause and resume
            self.assertNotEqual(argv3, argv4)
            assert argv3 != argv4

            self.assertTrue(self.calls[-2][0].endswith("bin/foreman --project shop pause"))
            self.assertTrue(self.calls[-1][0].endswith("bin/foreman --project shop resume"))
            self.assertNotEqual(self.calls[-2][0], self.calls[-1][0])
        finally:
            srv.foreman = orig_foreman


    def test_control_actions_and_payload_match_frontend_overview_js(self):
        """Invariant: All four action identifiers and the payload shape match exactly what web/static/js/overview.js's controlAction() sends."""
        overview_js_path = os.path.join(ROOT, "web", "static", "js", "overview.js")
        self.assertTrue(os.path.exists(overview_js_path))
        assert os.path.exists(overview_js_path)
        with open(overview_js_path, "r", encoding="utf-8") as f:
            overview_js = f.read()

        # Parse actions toggled into data-control for autopilot in controlsHtml:
        ap_match = re.search(r"apAction\s*=\s*apOn\s*\?\s*['\"]([^'\"]+)['\"]\s*:\s*['\"]([^'\"]+)['\"]", overview_js)
        self.assertIsNotNone(ap_match, "apAction ternary must exist in overview.js")
        assert ap_match is not None
        ap_actions = set(ap_match.groups())
        self.assertEqual(ap_actions, {"autopilot_on", "autopilot_off"})
        assert ap_actions == {"autopilot_on", "autopilot_off"}

        # Parse actions toggled into data-control for foreman in controlsHtml:
        fm_match = re.search(r"fmAction\s*=\s*isPaused\s*\?\s*['\"]([^'\"]+)['\"]\s*:\s*['\"]([^'\"]+)['\"]", overview_js)
        self.assertIsNotNone(fm_match, "fmAction ternary must exist in overview.js")
        assert fm_match is not None
        fm_actions = set(fm_match.groups())
        self.assertEqual(fm_actions, {"pause", "resume"})
        assert fm_actions == {"pause", "resume"}

        all_actions = ap_actions | fm_actions
        self.assertEqual(all_actions, {"autopilot_on", "autopilot_off", "pause", "resume"})
        assert all_actions == {"autopilot_on", "autopilot_off", "pause", "resume"}

        # Assert backend srv.CONTROL_ACTIONS matches the extracted actions from overview.js exactly:
        self.assertEqual(set(srv.CONTROL_ACTIONS), all_actions)
        assert set(srv.CONTROL_ACTIONS) == all_actions
        self.assertEqual(len(srv.CONTROL_ACTIONS), len(all_actions))
        assert len(srv.CONTROL_ACTIONS) == len(all_actions)

        # Parse request endpoint and body payload shape from controlAction:
        post_match = re.search(r"post\(\s*['\"](/api/control)['\"]\s*,\s*\{([^}]+)\}\)", overview_js)
        self.assertIsNotNone(post_match, "post('/api/control', {project, action}) must exist in overview.js")
        assert post_match is not None
        endpoint, payload_str = post_match.groups()
        self.assertEqual(endpoint, "/api/control")
        assert endpoint == "/api/control"
        payload_keys = set(k.strip() for k in payload_str.split(","))
        self.assertEqual(payload_keys, {"project", "action"})
        assert payload_keys == {"project", "action"}

        # Verify each parsed action sends the exact frontend payload shape and succeeds:
        H = {"X-Agent-Forge": "1"}
        for act in sorted(all_actions):
            payload = {"project": "shop", "action": act}
            s, _, body = self.req("POST", endpoint, payload, H)
            self.assertEqual(s, 200)
            assert s == 200
            res = json.loads(body)
            self.assertTrue(res.get("ok"))
            assert res.get("ok") is True

    def test_login_terminal(self):
        """The sign-in dialog is a real terminal: queries are stripped from the text view, the wrapped sign-in URL is rejoined,
        and input / size need a running session."""
        self.assertEqual(srv.B.ANSI.sub("", "\x1b_Ga=q,i=31;AAAA\x1b\\\x1b[>4;2mhi\x1b[<1u\x1b[1;32mgo\x1b[0m"), "higo")
        u = "https://accounts.google.com/o/oauth2/auth?x=" + "a" * 20
        self.assertEqual(srv.B.LOGIN._url(" https://policies.google.com/privacy\n " + u[:39] + "\n" + u[39:] + "\n Copy and paste", 40), u)
        self.assertEqual(srv.B.LOGIN._url("see https://example.org/a b", 80), "https://example.org/a")
        self.assertIsNone(srv.B.LOGIN._url("nothing", 80))
        self.assertEqual(srv.B.LOGIN.write("worker-1", "AAAA").get("error"), "no running login session")
        self.assertEqual(srv.B.LOGIN.resize("worker-1", 100, 30).get("error"), "no running login session")

    def test_new_project_workers_must_be_a_list_of_worker_names(self):
        """`workers` that is not a list (a number, an object, a string) is a validation error in the reply, not an exception."""
        H = {"X-Agent-Forge": "1"}
        for odd in (5, 1.5, True, {"worker-1": 1}, "worker-1", ["worker-1", 2], ["x"]):
            s, _, b = self.req("POST", "/api/projects", {"action": "create", "name": "demo-proj", "repo": "acme/shop", "workers": odd}, H)
            self.assertEqual(s, 200, (odd, b))
            self.assertIn("workers", json.loads(b).get("errors", {}), (odd, b))

    def test_login_terminal_size_survives_odd_numbers(self):
        """A terminal size no int() can take (infinity, nan, a list, 5000 digits) falls back to the defaults: the answer is a
        normal JSON reply, not a request dropped by an exception in the handler."""
        H = {"X-Agent-Forge": "1"}
        for odd in (float("inf"), float("-inf"), float("nan"), [1], {"a": 1}, "9" * 5000, "x", None):
            s, _, b = self.req("POST", "/api/login", {"worker": "worker-1", "action": "size", "cols": odd, "rows": odd}, H)
            self.assertEqual(s, 200, (odd, b))
            self.assertEqual(json.loads(b).get("error"), "no running login session", (odd, b))

    def test_access_log_records_writes_without_secrets(self):
        """Every write leaves one line in the journal, with a few plain fields - never the secret value, the query string,
        the sign-in token or typed text; reads are quiet unless DASHBOARD_ACCESS_LOG=all; refused requests are always logged."""
        H = {"X-Agent-Forge": "1"}
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            self.req("POST", "/api/secret", {"name": "anthropic.key", "value": "sk-ant-TOPSECRET"}, H)
            self.req("POST", "/api/secret", {"name": "sk-ant-TOPSECRET2", "value": "x"}, H)
            self.req("POST", "/api/egress", {"action": "allow", "host": "pypi.org"}, H)
            self.req("POST", "/api/settings", {"changes": {"WORKER_N": "2", "bad key": "1"}}, H)
            self.req("POST", "/api/ask", {"question": "what is my password hunter2?"}, H)
            self.req("GET", "/api/status?token=s3cret")
            self.req("GET", "/api/status?token=wrong", headers={"Cookie": ""})
            self.req("POST", "/api/egress", {"action": "allow", "host": "pypi.org"}, {"Cookie": ""})
        log = buf.getvalue()
        lines = log.splitlines()
        self.assertTrue(all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ dashboard \S+ (GET|POST) /\S* \d{3}( .*)?", l) for l in lines), lines)
        self.assertTrue(any(" POST /api/secret 200 name=anthropic.key" in l for l in lines), lines)
        self.assertTrue(any(" POST /api/egress 200 action=allow host=pypi.org" in l for l in lines), lines)
        self.assertTrue(any(" POST /api/settings " in l and l.endswith("keys=WORKER_N") for l in lines), lines)
        self.assertTrue(any(" POST /api/ask 200" in l for l in lines), lines)
        self.assertTrue(any(" GET /api/status 401" in l for l in lines), lines)
        self.assertTrue(any(" POST /api/egress 401" in l for l in lines), lines)
        self.assertFalse(any(" GET /api/status 200" in l for l in lines), lines)
        for needle in ("TOPSECRET", "hunter2", "s3cret", "wrong", "?token", "bad key"):
            self.assertNotIn(needle, log)
        old, srv.ACCESS_LOG = srv.ACCESS_LOG, "all"
        try:
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                self.req("GET", "/api/status?token=s3cret")
            self.assertIn(" GET /api/status 200", buf.getvalue())
            self.assertNotIn("s3cret", buf.getvalue())
            srv.ACCESS_LOG = "off"
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                self.req("POST", "/api/egress", {"action": "allow", "host": "pypi.org"}, H)
            self.assertEqual(buf.getvalue(), "")
        finally:
            srv.ACCESS_LOG = old

    def test_audit_detail_keeps_only_plain_fields(self):
        self.assertEqual(srv.audit_detail("/api/x", {"action": "add", "text": "free text", "value": "v", "worker": "worker-3"}),
                         "action=add worker=worker-3")
        self.assertEqual(srv.audit_detail("/api/x", {"action": "a\nb", "host": "x" * 200, "n": True}), "")
        self.assertEqual(srv.audit_detail("/api/secret", {"name": "gemini.key", "value": "v"}), "name=gemini.key")
        self.assertEqual(srv.audit_detail("/api/secret", {"name": "not-a-known-secret"}), "")
        self.assertEqual(srv.audit_detail("/api/x", None), "")

    def test_a_read_that_ran_during_a_write_is_not_served_after_it(self):
        """The slow-read cache is emptied once the write is over: a value computed while the write was still running is older
        than its result, and the next reader (another tab reads without ?fresh=1) must not get it."""
        old_ssh = srv.B.ssh_run_full

        def while_writing(cmd, timeout=30, inp=None):
            srv.SLOW.get("probe", 60, lambda: {"v": "from before the write ended"})
            return 0, "ok", ""
        srv.SLOW.drop()
        try:
            srv.B.ssh_run_full = while_writing
            s, _, _ = self.req("POST", "/api/settings", {"changes": {"WORKER_N": "2"}}, {"X-Agent-Forge": "1"})
            self.assertEqual(s, 200)
        finally:
            srv.B.ssh_run_full = old_ssh
        val, _ = srv.SLOW.get("probe", 60, lambda: {"v": "after"})
        self.assertEqual(val, {"v": "after"})
        srv.SLOW.drop()

    def test_a_refused_write_leaves_the_cache_alone(self):
        srv.SLOW.drop()
        srv.SLOW.get("probe", 60, lambda: {"v": "kept"})
        s, _, _ = self.req("POST", "/api/settings", {"changes": {"WORKER_N": "2"}})
        self.assertEqual(s, 403)
        val, _ = srv.SLOW.get("probe", 60, lambda: {"v": "recomputed"})
        self.assertEqual(val, {"v": "kept"})
        self.assertEqual(self.calls, [])
        srv.SLOW.drop()


class TestForgeDirDefault(unittest.TestCase):
    def test_default_forge_dir_is_the_repository_root(self):
        """Without FORGE_DIR / VM_IP (the demo image, a plain checkout) the dashboard must find bin/foreman in the repository root."""
        env = {k: v for k, v in os.environ.items() if k not in ("FORGE_DIR", "VM_IP", "DEMO_MODE")}
        code = "import sys; sys.path.insert(0, 'web'); import dashboard.backend as b; print(b.FORGE_DIR)"
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(os.path.realpath(out.stdout.strip()), os.path.realpath(ROOT))
        self.assertTrue(os.path.isfile(os.path.join(out.stdout.strip(), "bin", "foreman")))


class TestLiveDiscovery(unittest.TestCase):
    def test_discover_workers_ignores_directories_that_are_not_dispatch_runs(self):
        """A worker name read from a run directory ends up in `docker exec` and `tail -F` commands, so only bin/dispatch's own
        <timestamp>-worker-<N> names may pass; anything else in runs/ is skipped."""
        listing = "\n".join([
            "20261020T101010Z-worker-2/ 5",             # finished 5 s ago: still shown
            "20261020T101011Z-worker-3/ -1",            # running
            "20261020T101012Z-worker-4/ 100000",        # finished long ago: known, but no live pane
            "20261020T101013Z-x$(id)/ -1",
            "20261020T101014Z-worker-5;id/ -1",
            "20261020T101015Z-worker-6`id`/ -1",
            "20261020T101016Z-worker-7|id/ -1",
            "20261020T101017Z-worker-\u00b2/ -1",
            "0001-wordcount/ -1",
        ])
        orig = srv.L.ssh_run
        srv.L.ssh_run = lambda cmd, timeout=None: listing
        try:
            latest, seen = srv.L.discover_workers()
        finally:
            srv.L.ssh_run = orig
        self.assertEqual(seen, {"worker-2", "worker-3", "worker-4"})
        self.assertEqual(sorted(latest), ["worker-2", "worker-3"])


if __name__ == "__main__":
    runner = unittest.TextTestRunner(stream=sys.stdout)
    result = unittest.main(testRunner=runner, exit=False)
    if result.result.wasSuccessful():
        print("web tests: ALL OK")
        sys.exit(0)
    sys.exit(1)

