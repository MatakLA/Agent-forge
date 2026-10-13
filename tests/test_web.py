#!/usr/bin/env python3
"""Dashboard server tests for access control, request validation, and input sanitization.
Public interface: run directly via python3 tests/test_web.py or unittest discovery.
Invariants:
- Host header is strictly validated against allowed origins on all endpoints to prevent DNS rebinding.
- Port numbers in Host headers must be strictly numeric and non-empty.
- Origin and Referer headers on state-changing POSTs must match allowed hosts (CSRF guard).
- Authentication tokens are verified in constant time and redacted from error responses.
- All identifiers (TASK_ID, GOAL_ID, DOC_NAME, HOST, TARGET) are validated before shell execution.
"""
import glob
import http.client
import importlib.util
import json
import os
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

            # 3. LoginSessions start / send / stop exit immediately with error message
            self.assertEqual(srv.B.LOGIN.start("worker-1"), {"error": "Demo mode: read-only, no real workers"})
            self.assertEqual(srv.B.LOGIN.send("worker-1", "test"), {"error": "Demo mode: read-only, no real workers"})
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
        data = json.loads(proc.stdout)
        expected = readiness.all_status()
        self.assertEqual(data, expected)
        self.assertIn("runtimes", data)
        self.assertIn("providers", data)
        for secret_name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
            val = os.environ.get(secret_name, "")
            if val:
                self.assertNotIn(val, proc.stdout)

    def test_api_readiness_endpoint_auth_and_host_guards(self):
        """Invariant: GET /api/readiness enforces DASHBOARD_TOKEN and DASHBOARD_HOSTS checks."""
        s_unauth, _, body_unauth = self.req("GET", "/api/readiness", headers={"Cookie": ""})
        self.assertEqual(s_unauth, 401)
        self.assertIn("sign in", body_unauth)

        s_badtoken, _, body_badtoken = self.req("GET", "/api/readiness", headers={"Cookie": "af_token=wrong_token"})
        self.assertEqual(s_badtoken, 401)
        self.assertIn("sign in", body_badtoken)

        s_badhost, _, body_badhost = self.req("GET", "/api/readiness", host="evil.attacker.com")
        self.assertEqual(s_badhost, 403)
        self.assertIn("host not allowed", body_badhost)

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
            self.assertEqual(headers.get("Content-Type"), "application/json")
            self.assertTrue(self.calls and self.calls[-1][0].endswith("bin/foreman readiness"))
            data = json.loads(body)
            self.assertEqual(data, stub_status)
            self.assertIn("runtimes", data)
            self.assertIn("providers", data)
            for secret_name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
                val = os.environ.get(secret_name, "")
                if val:
                    self.assertNotIn(val, body)
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
            data = json.loads(body)
            self.assertEqual(data.get("ok"), False)
            self.assertIn("readiness diagnostic failed", data.get("error", ""))

            srv.B.ssh_run_full = lambda cmd, timeout=30, inp=None: (
                self.calls.append((cmd, inp)) or (2, "", "")
            )
            status, _, body = self.req("GET", "/api/readiness")
            self.assertEqual(status, 200)
            data = json.loads(body)
            self.assertEqual(data.get("ok"), False)
            self.assertIn("exit 2", data.get("error", ""))

            srv.B.ssh_run_full = lambda cmd, timeout=30, inp=None: (
                self.calls.append((cmd, inp)) or (0, "corrupted stdout output", "")
            )
            status, _, body = self.req("GET", "/api/readiness")
            self.assertEqual(status, 200)
            data = json.loads(body)
            self.assertEqual(data.get("ok"), False)
            self.assertIn("corrupted stdout output", data.get("error", ""))
        finally:
            srv.B.ssh_run_full = old_ssh


if __name__ == "__main__":
    runner = unittest.TextTestRunner(stream=sys.stdout)
    result = unittest.main(testRunner=runner, exit=False)
    if result.result.wasSuccessful():
        print("web tests: ALL OK")
        sys.exit(0)
    sys.exit(1)
