#!/usr/bin/env python3
"""Tests for DEMO_MODE safety guards and demo compose/script invariants.
Public interface: python3 tests/test_demo_mode.py or unittest test runner.
Invariants:
- In DEMO_MODE, all mutating POST endpoints return HTTP 403 with JSON error body.
- Without DEMO_MODE, mutating POST endpoints are not blocked by the demo guard.
- In DEMO_MODE, backend worker creation and login sessions short-circuit without subprocess calls.
- In DEMO_MODE, read-only GET endpoints remain functional and accessible.
- Demo docker-compose configuration and launch scripts contain no secrets or host sockets.
"""
import http.client
import importlib
import json
import os
import re
import subprocess
import sys
import threading
import unittest
import warnings

# Suppress ResourceWarning for unclosed static files during module reloads
warnings.filterwarnings("ignore", category=ResourceWarning)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Ensure DEMO_MODE=1 is set via os.environ before importing web modules
os.environ["DEMO_MODE"] = "1"

# Top-level smoke-import check that exits non-zero on ImportError
try:
    import web.dashboard.backend as backend
    import web.server as server
    if not getattr(backend, "DEMO_MODE", False) or not getattr(server, "DEMO_MODE", False):
        importlib.reload(backend)
        if hasattr(server, "B"):
            importlib.reload(server.B)
        importlib.reload(server)
except ImportError:
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Invariant assertions after smoke import
assert backend is not None
assert server is not None
assert backend.DEMO_MODE is True
assert server.DEMO_MODE is True


class TestDemoModeSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.orig_ssh = server.B.ssh_run_full
        server.B.ssh_run_full = lambda cmd, timeout=30, inp=None: (0, '{"ok": true}', "")
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        server.B.ssh_run_full = cls.orig_ssh
        cls.httpd.shutdown()
        cls.server_thread.join(timeout=2)
        cls.httpd.server_close()

    def setUp(self):
        # Default each test to DEMO_MODE=1 enabled
        server.DEMO_MODE = True
        server.B.DEMO_MODE = True
        backend.DEMO_MODE = True

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json"}
        h.update(headers or {})
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        raw = r.read().decode(errors="replace")
        try:
            data = json.loads(raw)
        except Exception:
            data = raw
        return r.status, data

    def test_demo_mode_mutating_posts_return_403(self):
        """Invariant: in DEMO_MODE, all mutating POST endpoints return HTTP 403 with error JSON."""
        server.DEMO_MODE = True
        server.B.DEMO_MODE = True
        backend.DEMO_MODE = True

        mutating_endpoints = [
            ("/api/killswitch", {"action": "on"}),
            ("/api/task", {"id": "0001-test", "action": "approve"}),
            ("/api/settings", {"changes": {"AUTOPILOT": "1"}}),
            ("/api/workers", {"action": "add", "runtime": "agy"}),
            ("/api/resume", {}),
            ("/api/projects", {"action": "create", "name": "demo-proj", "repo": "a/b"}),
            ("/api/goalctl", {"id": "G20261008-062000", "action": "pause"}),
            ("/api/goal", {"text": "new goal", "mode": "now"}),
            ("/api/doc", {"name": "vision", "reset": True}),
            ("/api/upstream", {"action": "connect", "kind": "forgejo"}),
            ("/api/login", {"worker": "worker-1", "action": "start"}),
            ("/api/secret", {"name": "test.key", "value": "secret"}),
            ("/api/ask", {"question": "test question"}),
        ]

        all_blocked = True
        last_status = None
        last_error = None
        for path, payload in mutating_endpoints:
            status, body = self.req("POST", path, payload)
            last_status = status
            self.assertEqual(status, 403, f"{path} did not return 403 in DEMO_MODE")
            self.assertIsInstance(body, dict)
            last_error = body.get("error")
            self.assertEqual(last_error, "Demo mode: read-only")
            if status != 403 or last_error != "Demo mode: read-only":
                all_blocked = False

        self.assertTrue(all_blocked)
        assert all_blocked is True
        assert last_status == 403
        assert last_error == "Demo mode: read-only"

    def test_without_demo_mode_posts_do_not_return_403(self):
        """Invariant: without DEMO_MODE set, mutating POST requests do NOT return 403."""
        server.DEMO_MODE = False
        server.B.DEMO_MODE = False
        backend.DEMO_MODE = False

        endpoints = [
            ("/api/killswitch", {"action": "on"}),
            ("/api/task", {"id": "0001-test", "action": "approve"}),
            ("/api/settings", {"changes": {"AUTOPILOT": "1"}}),
        ]

        h = {"X-Agent-Forge": "1"}
        all_allowed = True
        last_status = None
        for path, payload in endpoints:
            status, body = self.req("POST", path, payload, headers=h)
            last_status = status
            self.assertNotEqual(status, 403, f"{path} incorrectly returned 403 when DEMO_MODE is off")
            self.assertEqual(status, 200, f"{path} expected 200 when DEMO_MODE is off, got {status}: {body}")
            if status == 403 or status != 200:
                all_allowed = False

        self.assertTrue(all_allowed)
        assert all_allowed is True
        assert last_status == 200
        assert last_status != 403

    def test_demo_mode_add_worker_and_login_short_circuit_without_subprocess(self):
        """Invariant: backend.add_worker_job and LoginSessions.start short-circuit under DEMO_MODE without subprocess invocation."""
        server.DEMO_MODE = True
        server.B.DEMO_MODE = True
        backend.DEMO_MODE = True

        subproc_called = []

        def bomb_popen(*a, **kw):
            subproc_called.append(("popen", a))
            raise AssertionError("Subprocess.Popen called under DEMO_MODE!")

        def bomb_run(*a, **kw):
            subproc_called.append(("run", a))
            raise AssertionError("Subprocess.run called under DEMO_MODE!")

        orig_popen = subprocess.Popen
        orig_run = subprocess.run
        subprocess.Popen = bomb_popen
        subprocess.run = bomb_run

        try:
            # 1. add_worker_job must short-circuit with error dict and failed job state
            job = {"id": "w-1", "state": "running", "log": "", "started": 0}
            worker_res = backend.add_worker_job(job)
            self.assertEqual(worker_res, {"error": "Demo mode: read-only, no real workers"})
            self.assertEqual(job["state"], "failed")
            self.assertIn("Demo mode: read-only, no real workers", job["log"])

            # 2. LoginSessions.start, send, stop must short-circuit with error dict
            login = backend.LoginSessions()
            login_res = login.start("worker-1")
            self.assertEqual(login_res, {"error": "Demo mode: read-only, no real workers"})

            send_res = login.send("worker-1", "command")
            self.assertEqual(send_res, {"error": "Demo mode: read-only, no real workers"})

            stop_res = login.stop("worker-1")
            self.assertEqual(stop_res, {"error": "Demo mode: read-only, no real workers"})

            # Verify no subprocess was called
            self.assertEqual(len(subproc_called), 0)
        finally:
            subprocess.Popen = orig_popen
            subprocess.run = orig_run

        assert len(subproc_called) == 0
        assert worker_res.get("error") == "Demo mode: read-only, no real workers"
        assert login_res.get("error") == "Demo mode: read-only, no real workers"
        assert job["state"] == "failed"

    def test_demo_mode_backend_jobs_and_ssh_assertions(self):
        """Invariant: create_project_job and connect_upstream_job short-circuit, and ssh_run asserts under DEMO_MODE."""
        server.DEMO_MODE = True
        server.B.DEMO_MODE = True
        backend.DEMO_MODE = True

        # create_project_job short-circuits
        proj_job = {"id": "p-1", "state": "running", "log": "", "started": 0}
        proj_res = backend.create_project_job(proj_job, "demo-proj", "owner/repo", [], "English", None)
        self.assertEqual(proj_res, {"error": "Demo mode: read-only, no real workers"})
        self.assertEqual(proj_job["state"], "failed")
        self.assertIn("Demo mode: read-only, no real workers", proj_job["log"])

        # connect_upstream_job short-circuits
        up_job = {"id": "u-1", "state": "running", "log": "", "started": 0}
        up_res = backend.connect_upstream_job(up_job, "", {}, False, False)
        self.assertEqual(up_res, {"error": "Demo mode: read-only, no real workers"})
        self.assertEqual(up_job["state"], "failed")
        self.assertIn("Demo mode: read-only, no real workers", up_job["log"])

        # ssh_run and ssh_run_full assert immediately in DEMO_MODE
        with self.assertRaises(AssertionError) as cm_ssh:
            backend.ssh_run("echo safe")
        self.assertIn("Demo mode: ssh_run must never be called", str(cm_ssh.exception))

        with self.assertRaises(AssertionError) as cm_full:
            backend.ssh_run_full("echo safe")
        self.assertIn("Demo mode: ssh_run_full must never be called", str(cm_full.exception))

        assert proj_res.get("error") == "Demo mode: read-only, no real workers"
        assert up_res.get("error") == "Demo mode: read-only, no real workers"
        assert "Demo mode" in str(cm_ssh.exception)
        assert "Demo mode" in str(cm_full.exception)

    def test_demo_mode_read_endpoints_remain_accessible(self):
        """Invariant: in DEMO_MODE, read-only GET endpoints return 200 and serve status data."""
        server.DEMO_MODE = True
        server.B.DEMO_MODE = True
        backend.DEMO_MODE = True

        mock_data = {"alive": True, "goals": [{"id": "G1", "text": "synthetic demo goal"}]}
        backend.FOREMAN_BY["default"] = {"data": mock_data, "at": 123456.0, "error": None}
        server.B.FOREMAN_BY["default"] = {"data": mock_data, "at": 123456.0, "error": None}

        s_stat, d_stat = self.req("GET", "/api/status")
        s_dash, d_dash = self.req("GET", "/api/dashboard")
        s_proj, d_proj = self.req("GET", "/api/projects")
        s_hlth, d_hlth = self.req("GET", "/healthz")

        self.assertEqual(s_stat, 200)
        self.assertEqual(s_dash, 200)
        self.assertEqual(s_proj, 200)
        self.assertEqual(s_hlth, 200)
        self.assertEqual(d_stat.get("foreman"), mock_data)
        self.assertEqual(d_dash.get("foreman"), mock_data)

        assert s_stat == 200
        assert s_dash == 200
        assert s_proj == 200
        assert s_hlth == 200
        assert d_stat.get("foreman") == mock_data

    def test_demo_mode_import_reload_behavior(self):
        """Invariant: setting os.environ[DEMO_MODE] toggles backend and server constants on reload."""
        # 1. Reload with DEMO_MODE=0
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            os.environ["DEMO_MODE"] = "0"
            importlib.reload(backend)
            if hasattr(server, "B"):
                importlib.reload(server.B)
            importlib.reload(server)
            off_backend = backend.DEMO_MODE
            off_server = server.DEMO_MODE

        self.assertFalse(off_backend)
        self.assertFalse(off_server)

        # 2. Reload with DEMO_MODE=1
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            os.environ["DEMO_MODE"] = "1"
            importlib.reload(backend)
            if hasattr(server, "B"):
                importlib.reload(server.B)
            importlib.reload(server)
            on_backend = backend.DEMO_MODE
            on_server = server.DEMO_MODE

        self.assertTrue(on_backend)
        self.assertTrue(on_server)

        assert off_backend is False
        assert off_server is False
        assert on_backend is True
        assert on_server is True

    def test_demo_compose_and_script_contents_safety(self):
        """Invariant: demo/docker-compose.yml and scripts/demo.sh contain no docker.sock, secrets, or remote downloads."""
        compose_path = os.path.join(ROOT, "demo", "docker-compose.yml")
        demo_sh_path = os.path.join(ROOT, "scripts", "demo.sh")

        self.assertTrue(os.path.isfile(compose_path))
        self.assertTrue(os.path.isfile(demo_sh_path))

        with open(compose_path, "r", encoding="utf-8") as f:
            compose_text = f.read()

        with open(demo_sh_path, "r", encoding="utf-8") as f:
            demo_sh_text = f.read()

        # Compose safety checks
        self.assertNotIn("docker.sock", compose_text)
        self.assertNotIn("DASHBOARD_TOKEN=", compose_text)
        self.assertFalse(re.search(r"(_KEY=|_SECRET=|_TOKEN=|API_KEY|AUTH_TOKEN)", compose_text, re.IGNORECASE))

        # Script safety checks
        self.assertNotIn(".config/sandbox/secrets", demo_sh_text)
        self.assertNotIn("SANDBOX_SECRETS", demo_sh_text)
        self.assertFalse(re.search(r"\b(curl|wget)\s+", demo_sh_text))

        assert "docker.sock" not in compose_text
        assert "DASHBOARD_TOKEN=" not in compose_text
        assert ".config/sandbox/secrets" not in demo_sh_text
        assert not re.search(r"\b(curl|wget)\s+", demo_sh_text)


if __name__ == "__main__":
    unittest.main()
