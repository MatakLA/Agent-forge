#!/usr/bin/env python3
"""Tests for DEMO_MODE safety guards and static demo configuration checks.
Public interface: python3 -m unittest tests/test_demo_mode.py or python3 tests/test_demo_mode.py.
Invariants:
- In DEMO_MODE, all mutating POST endpoints return HTTP 403 with JSON error body.
- Without DEMO_MODE, mutating POST endpoints are not blocked by the demo guard.
- In DEMO_MODE, backend worker creation and login sessions short-circuit without subprocess calls.
- In DEMO_MODE, read-only GET endpoints remain functional and accessible.
- demo/docker-compose.yml defines only the 'demo' service on port 8171 with DEMO_MODE=1.
- demo/Dockerfile sets DEMO_MODE=1 and invokes tools/demo_state.py and web/server.py.
- Neither compose nor Dockerfile references secrets or .config/sandbox paths.
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

            # 2. LoginSessions.start, write, stop must short-circuit with error dict
            login = backend.LoginSessions()
            login_res = login.start("worker-1")
            self.assertEqual(login_res, {"error": "Demo mode: read-only, no real workers"})

            send_res = login.write("worker-1", "AAAA")
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


def extract_compose_services(text):
    """Extract top-level service names under services: in compose YAML without pyyaml."""
    services = []
    in_services = False
    for line in text.splitlines():
        line_clean = line.split("#", 1)[0].rstrip()
        if not line_clean:
            continue
        if re.match(r"^\s*services:\s*$", line_clean):
            in_services = True
            continue
        if in_services and not line_clean.startswith(" ") and not line_clean.startswith("\t") and ":" in line_clean:
            in_services = False
            continue
        if in_services:
            m = re.match(r"^[ \t]{2}([a-zA-Z0-9_-]+):\s*$", line_clean)
            if m:
                services.append(m.group(1))
    return services


def extract_compose_published_ports(text):
    """Extract published port specs under ports: in compose YAML without pyyaml."""
    ports = []
    in_ports = False
    for line in text.splitlines():
        line_clean = line.split("#", 1)[0].rstrip()
        if not line_clean:
            continue
        if re.match(r"^\s+ports:\s*$", line_clean):
            in_ports = True
            continue
        if in_ports:
            m = re.match(r"^\s+-\s+[\"']?([0-9.:]+)[\"']?\s*$", line_clean)
            if m:
                ports.append(m.group(1))
            elif (not line_clean.startswith(" ") and not line_clean.startswith("\t")) or re.match(r"^\s+[a-zA-Z0-9_-]+:", line_clean):
                in_ports = False
    return ports


def validate_demo_compose_text(text):
    """Static validation of demo/docker-compose.yml text against required invariants.
    Raises AssertionError if any invariant is violated.
    """
    services = extract_compose_services(text)
    if services != ["demo"]:
        raise AssertionError(f"Expected only 'demo' service, got: {services}")

    for forbidden in ("egress", "worker-", "forgejo"):
        if forbidden in text.lower():
            raise AssertionError(f"Forbidden string '{forbidden}' found in compose config")

    ports = extract_compose_published_ports(text)
    if not ports:
        raise AssertionError("No published ports found in compose config")
    for p in ports:
        parts = p.split(":")
        if len(parts) < 2 or parts[-2] != "8171" or parts[-1] != "8171":
            raise AssertionError(f"Port mapping '{p}' publishes a port other than 8171")

    if not re.search(r"\bDEMO_MODE=1\b", text):
        raise AssertionError("DEMO_MODE=1 missing from compose config")

    if "secrets" in text.lower() or ".config/sandbox" in text.lower():
        raise AssertionError("Reference to secrets or .config/sandbox in compose config")


def extract_dockerfile_entrypoint_and_cmd(text):
    """Extract ENTRYPOINT and CMD instructions from Dockerfile text, supporting backslash continuations."""
    normalized_lines = []
    buf = ""
    for line in text.splitlines():
        line_clean = line.split("#", 1)[0].rstrip()
        if buf:
            line_clean = (buf + " " + line_clean.lstrip()).rstrip()
            buf = ""
        if line_clean.endswith("\\"):
            buf = line_clean[:-1].rstrip()
        else:
            if line_clean:
                normalized_lines.append(line_clean)
    if buf:
        normalized_lines.append(buf)

    commands = []
    for line in normalized_lines:
        m = re.match(r"^(?:ENTRYPOINT|CMD)\s+(.*)", line.strip(), re.IGNORECASE)
        if m:
            commands.append(m.group(1))
    return " ".join(commands)


def validate_demo_dockerfile_text(text):
    """Static validation of demo/Dockerfile text against required invariants.
    Raises AssertionError if any invariant is violated.
    """
    if not re.search(r"\bDEMO_MODE=1\b", text):
        raise AssertionError("DEMO_MODE=1 missing from Dockerfile")

    cmd_text = extract_dockerfile_entrypoint_and_cmd(text)
    if "tools/demo_state.py" not in cmd_text:
        raise AssertionError("tools/demo_state.py is not invoked by Dockerfile ENTRYPOINT/CMD")
    if "web/server.py" not in cmd_text:
        raise AssertionError("web/server.py is not invoked by Dockerfile ENTRYPOINT/CMD")

    if "secrets" in text.lower() or ".config/sandbox" in text.lower():
        raise AssertionError("Reference to secrets or .config/sandbox in Dockerfile")


class TestDemoStaticConfig(unittest.TestCase):
    """Static configuration and invariant tests for demo compose, Dockerfile, and launch script.
    Zero Docker and zero network requirements.
    """

    def setUp(self):
        self.compose_path = os.path.join(ROOT, "demo", "docker-compose.yml")
        self.dockerfile_path = os.path.join(ROOT, "demo", "Dockerfile")
        self.demo_sh_path = os.path.join(ROOT, "scripts", "demo.sh")
        with open(self.compose_path, "r", encoding="utf-8") as f:
            self.compose_text = f.read()
        with open(self.dockerfile_path, "r", encoding="utf-8") as f:
            self.dockerfile_text = f.read()
        with open(self.demo_sh_path, "r", encoding="utf-8") as f:
            self.demo_sh_text = f.read()

    def test_compose_defines_only_demo_service(self):
        """Invariant: demo/docker-compose.yml defines only 'demo' service and no worker, egress, or forgejo."""
        services = extract_compose_services(self.compose_text)
        self.assertEqual(services, ["demo"])
        for forbidden in ("egress", "worker-", "forgejo"):
            self.assertNotIn(forbidden, self.compose_text.lower())
        assert services == ["demo"]
        assert "egress" not in self.compose_text.lower()
        assert "worker-" not in self.compose_text.lower()
        assert "forgejo" not in self.compose_text.lower()

    def test_compose_publishes_port_8171_only(self):
        """Invariant: demo/docker-compose.yml publishes port 8171 and no other port."""
        ports = extract_compose_published_ports(self.compose_text)
        self.assertTrue(len(ports) > 0, "No published ports found in compose")
        for p in ports:
            parts = p.split(":")
            self.assertGreaterEqual(len(parts), 2, f"Port spec '{p}' is not host:container")
            self.assertEqual(parts[-2], "8171")
            self.assertEqual(parts[-1], "8171")
        assert len(ports) > 0
        assert all(len(p.split(":")) >= 2 and p.split(":")[-2] == "8171" and p.split(":")[-1] == "8171" for p in ports)

    def test_demo_mode_env_in_compose_and_dockerfile(self):
        """Invariant: DEMO_MODE=1 appears in both demo/docker-compose.yml and demo/Dockerfile."""
        self.assertIn("DEMO_MODE=1", self.compose_text)
        self.assertIn("DEMO_MODE=1", self.dockerfile_text)
        assert "DEMO_MODE=1" in self.compose_text
        assert "DEMO_MODE=1" in self.dockerfile_text

    def test_dockerfile_entrypoint_invokes_demo_state_and_web_server(self):
        """Invariant: demo/Dockerfile ENTRYPOINT or CMD invokes both tools/demo_state.py and web/server.py."""
        cmd_text = extract_dockerfile_entrypoint_and_cmd(self.dockerfile_text)
        self.assertIn("tools/demo_state.py", cmd_text)
        self.assertIn("web/server.py", cmd_text)
        assert "tools/demo_state.py" in cmd_text
        assert "web/server.py" in cmd_text

    def test_compose_and_dockerfile_contain_no_secrets_or_sandbox_config(self):
        """Invariant: neither demo/docker-compose.yml, demo/Dockerfile, nor scripts/demo.sh reference secrets or .config/sandbox."""
        self.assertNotIn("secrets", self.compose_text.lower())
        self.assertNotIn(".config/sandbox", self.compose_text.lower())
        self.assertNotIn("secrets", self.dockerfile_text.lower())
        self.assertNotIn(".config/sandbox", self.dockerfile_text.lower())
        self.assertNotIn("secrets", self.demo_sh_text.lower())
        self.assertNotIn(".config/sandbox", self.demo_sh_text.lower())
        assert "secrets" not in self.compose_text.lower()
        assert ".config/sandbox" not in self.compose_text.lower()
        assert "secrets" not in self.dockerfile_text.lower()
        assert ".config/sandbox" not in self.dockerfile_text.lower()
        assert "secrets" not in self.demo_sh_text.lower()
        assert ".config/sandbox" not in self.demo_sh_text.lower()

    def test_compose_validation_rejects_worker_egress_forgejo_services(self):
        """Invariant: compose validation fails if a worker, egress, or forgejo service is added."""
        failures = 0
        for forbidden in ("worker-1", "egress", "forgejo"):
            mutated = self.compose_text + f"\n  {forbidden}:\n    image: test\n"
            try:
                validate_demo_compose_text(mutated)
            except AssertionError:
                failures += 1
        self.assertEqual(failures, 3)
        assert failures == 3

    def test_compose_validation_rejects_non_demo_service(self):
        """Invariant: compose validation fails if an extra service like 'database' or 'web' is added."""
        mutated = self.compose_text + "\n  database:\n    image: postgres:alpine\n"
        caught = False
        err_msg = ""
        try:
            validate_demo_compose_text(mutated)
        except AssertionError as e:
            caught = True
            err_msg = str(e)
        self.assertTrue(caught)
        assert caught is True
        assert "Expected only 'demo' service" in err_msg

    def test_compose_validation_rejects_empty_services(self):
        """Invariant: compose validation fails if no services or no demo service is defined."""
        mutated = "name: demo\nservices:\n"
        caught = False
        try:
            validate_demo_compose_text(mutated)
        except AssertionError:
            caught = True
        self.assertTrue(caught)
        assert caught is True

    def test_compose_validation_rejects_dropping_demo_mode(self):
        """Invariant: compose validation fails if DEMO_MODE=1 is omitted."""
        mutated = self.compose_text.replace("DEMO_MODE=1", "DEMO_MODE=0")
        caught = False
        err_msg = ""
        try:
            validate_demo_compose_text(mutated)
        except AssertionError as e:
            caught = True
            err_msg = str(e)
        self.assertTrue(caught)
        assert caught is True
        assert "DEMO_MODE=1" in err_msg

    def test_compose_validation_rejects_missing_ports(self):
        """Invariant: compose validation fails if ports section is removed."""
        mutated = self.compose_text.replace('ports:\n      - "8171:8171"', "# no ports")
        caught = False
        err_msg = ""
        try:
            validate_demo_compose_text(mutated)
        except AssertionError as e:
            caught = True
            err_msg = str(e)
        self.assertTrue(caught)
        assert caught is True
        assert "No published ports found" in err_msg

    def test_compose_validation_rejects_ports_other_than_8171(self):
        """Invariant: compose validation fails if a port other than 8171 is published."""
        failures = 0
        bad_ports = ("8080:8080", "8171:8000", "9000:8171", "3000")
        for bad_port in bad_ports:
            mutated = self.compose_text.replace("8171:8171", bad_port)
            try:
                validate_demo_compose_text(mutated)
            except AssertionError:
                failures += 1
        self.assertEqual(failures, len(bad_ports))
        assert failures == len(bad_ports)

    def test_dockerfile_validation_rejects_dropping_demo_mode(self):
        """Invariant: Dockerfile validation fails if DEMO_MODE=1 is omitted."""
        mutated = self.dockerfile_text.replace("DEMO_MODE=1", "DEMO_MODE=0")
        caught = False
        err_msg = ""
        try:
            validate_demo_dockerfile_text(mutated)
        except AssertionError as e:
            caught = True
            err_msg = str(e)
        self.assertTrue(caught)
        assert caught is True
        assert "DEMO_MODE=1 missing" in err_msg

    def test_dockerfile_validation_rejects_missing_entrypoint_commands(self):
        """Invariant: Dockerfile validation fails if demo_state.py or web/server.py is missing from CMD."""
        mutated1 = self.dockerfile_text.replace("tools/demo_state.py", "echo skip")
        caught1 = False
        err1 = ""
        try:
            validate_demo_dockerfile_text(mutated1)
        except AssertionError as e1:
            caught1 = True
            err1 = str(e1)

        mutated2 = self.dockerfile_text.replace("web/server.py", "echo skip")
        caught2 = False
        err2 = ""
        try:
            validate_demo_dockerfile_text(mutated2)
        except AssertionError as e2:
            caught2 = True
            err2 = str(e2)

        self.assertTrue(caught1 and caught2)
        assert caught1 is True
        assert caught2 is True
        assert "tools/demo_state.py" in err1
        assert "web/server.py" in err2

    def test_extract_dockerfile_entrypoint_with_multiline_continuation(self):
        """Invariant: extract_dockerfile_entrypoint_and_cmd handles backslash line continuations."""
        dockerfile = 'CMD ["sh", "-c", \\\n     "python3 tools/demo_state.py /app && exec python3 web/server.py"]'
        extracted = extract_dockerfile_entrypoint_and_cmd(dockerfile)
        self.assertIn("tools/demo_state.py", extracted)
        self.assertIn("web/server.py", extracted)
        assert "tools/demo_state.py" in extracted
        assert "web/server.py" in extracted

    def test_config_validation_rejects_secrets_and_sandbox_references(self):
        """Invariant: validation fails if secrets or .config/sandbox references are introduced."""
        failures = 0
        bad_configs = [
            (validate_demo_compose_text, self.compose_text + "\n# secrets\n"),
            (validate_demo_compose_text, self.compose_text + "\n# .config/sandbox\n"),
            (validate_demo_dockerfile_text, self.dockerfile_text + "\n# secrets\n"),
            (validate_demo_dockerfile_text, self.dockerfile_text + "\n# .config/sandbox\n"),
        ]
        for fn, text in bad_configs:
            try:
                fn(text)
            except AssertionError:
                failures += 1
        self.assertEqual(failures, len(bad_configs))
        assert failures == len(bad_configs)

    def test_real_compose_and_dockerfile_pass_validation(self):
        """Invariant: real demo/docker-compose.yml and demo/Dockerfile pass all validation checks without error."""
        validate_demo_compose_text(self.compose_text)
        validate_demo_dockerfile_text(self.dockerfile_text)
        services = extract_compose_services(self.compose_text)
        ports = extract_compose_published_ports(self.compose_text)
        self.assertEqual(services, ["demo"])
        self.assertEqual(len(ports), 1)
        assert services == ["demo"]
        assert len(ports) == 1


if __name__ == "__main__":
    unittest.main()

