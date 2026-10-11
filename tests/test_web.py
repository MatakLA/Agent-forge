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
import sys
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


if __name__ == "__main__":
    runner = unittest.TextTestRunner(stream=sys.stdout)
    result = unittest.main(testRunner=runner, exit=False)
    if result.result.wasSuccessful():
        print("web tests: ALL OK")
        sys.exit(0)
    sys.exit(1)
