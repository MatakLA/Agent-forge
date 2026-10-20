#!/usr/bin/env python3
"""Tests for web/dashboard/backend.py command injection defences and readiness API.

Public interface:
- Run directly: python3 tests/test_dashboard_backend.py
- Run via unittest: python3 -m unittest tests/test_dashboard_backend.py

Important invariants:
- Remote commands constructed for ssh_run / ssh_run_full validate all parameters
  (project name, repo, workers, language, runtime, project flag) before formatting.
- Owner vision and secrets travel on stdin, never interpolated in remote shell strings or argv.
- LoginSessions rejects invalid worker names and pty breakout control sequences.
- readiness_summary exposes runtimes and providers status dictionaries without leaking secrets.
- broken readiness import degrades gracefully to error JSON without crashing.
- GET /api/readiness exposes readiness_summary and enforces host and token authentication.
- GET /api/readiness/summary exposes all six new runtimes with non-empty cost/quota classifications.
"""
import http.client
import importlib.util
import json
import os
import sys
import threading
import unittest
from unittest.mock import patch, MagicMock

# Top-level smoke-import check to fail loudly if import breaks
try:
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)

    spec_b = importlib.util.spec_from_file_location("backend", os.path.join(ROOT, "web", "dashboard", "backend.py"))
    backend = importlib.util.module_from_spec(spec_b)
    spec_b.loader.exec_module(backend)

    # Ensure srv uses the exact same backend module instance
    sys.modules["dashboard.backend"] = backend

    spec_s = importlib.util.spec_from_file_location("srv", os.path.join(ROOT, "web", "server.py"))
    srv = importlib.util.module_from_spec(spec_s)
    spec_s.loader.exec_module(srv)
    srv.B = backend
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)


class TestDashboardBackendSecurity(unittest.TestCase):
    """Test security constraints and command injection defenses in web/dashboard/backend.py."""

    def setUp(self):
        self.calls = []

        def fake_ssh_run_full(cmd, timeout=30, inp=None):
            self.calls.append((cmd, inp))
            return 0, "ok", ""

        def fake_ssh_run(cmd, timeout=backend.SSH_TIMEOUT):
            self.calls.append((cmd, None))
            return "ok"

        self.orig_ssh_run_full = backend.ssh_run_full
        self.orig_ssh_run = backend.ssh_run
        backend.ssh_run_full = fake_ssh_run_full
        backend.ssh_run = fake_ssh_run

    def tearDown(self):
        backend.ssh_run_full = self.orig_ssh_run_full
        backend.ssh_run = self.orig_ssh_run

    def test_pflag_validation(self):
        """pflag must accept only bare validated project names and reject shell metacharacters."""
        self.assertEqual(backend.pflag(""), "")
        self.assertEqual(backend.pflag(None), "")
        self.assertEqual(backend.pflag("default"), "")
        self.assertEqual(backend.pflag("shop"), " --project shop")
        self.assertEqual(backend.pflag("my-project-01"), " --project my-project-01")

        bad_projects = [
            "shop; id",
            "shop$(whoami)",
            "shop`id`",
            "shop && touch /tmp/pwn",
            "shop || exit 1",
            "shop|cat",
            "shop\nid",
            "shop\rid",
            "../etc",
            "/bin/sh",
            " default",
            "default; id",
            "-flag",
            "shop bar",
        ]
        for bad in bad_projects:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    backend.pflag(bad)
        assert backend.pflag("") == "" and backend.pflag("shop") == " --project shop"

    def test_create_project_job_rejects_malicious_name(self):
        """create_project_job must reject crafted project names containing shell metacharacters."""
        job = {"id": "test-1", "state": "running", "log": ""}
        backend.create_project_job(job, "proj; touch /tmp/pwn", "acme/shop", [], "English", "our vision")
        self.assertEqual(job["state"], "failed")
        for cmd, _ in self.calls:
            self.assertNotIn("; touch /tmp/pwn", cmd)
            self.assertNotIn("touch", cmd)
        self.assertEqual(len(self.calls), 0)
        assert job["state"] == "failed" and len(self.calls) == 0

    def test_create_project_job_rejects_malicious_repo(self):
        """create_project_job must reject crafted repo names containing shell metacharacters."""
        job = {"id": "test-2", "state": "running", "log": ""}
        backend.create_project_job(job, "shop", "acme/shop; touch /tmp/pwn", [], "English", "our vision")
        self.assertEqual(job["state"], "failed")
        for cmd, _ in self.calls:
            self.assertNotIn("; touch /tmp/pwn", cmd)
            self.assertNotIn("touch", cmd)
        self.assertEqual(len(self.calls), 0)
        assert job["state"] == "failed" and len(self.calls) == 0

    def test_create_project_job_rejects_malicious_workers(self):
        """create_project_job must reject crafted worker lists containing shell metacharacters."""
        job = {"id": "test-3", "state": "running", "log": ""}
        backend.create_project_job(job, "shop", "acme/shop", ["worker-1; touch /tmp/pwn"], "English", "our vision")
        self.assertEqual(job["state"], "failed")
        for cmd, _ in self.calls:
            self.assertNotIn("touch", cmd)
        self.assertEqual(len(self.calls), 0)
        assert job["state"] == "failed" and len(self.calls) == 0

    def test_create_project_job_rejects_malicious_lang(self):
        """create_project_job must reject crafted language strings containing shell metacharacters."""
        job = {"id": "test-4", "state": "running", "log": ""}
        backend.create_project_job(job, "shop", "acme/shop", [], "English'; touch /tmp/pwn #", "our vision")
        self.assertEqual(job["state"], "failed")
        for cmd, _ in self.calls:
            self.assertNotIn("touch", cmd)
        self.assertEqual(len(self.calls), 0)
        assert job["state"] == "failed" and len(self.calls) == 0

    def test_create_project_job_vision_safely_on_stdin(self):
        """create_project_job passes vision with shell metacharacters on stdin, never in command line."""
        job = {"id": "test-5", "state": "running", "log": ""}
        evil_vision = "Vision: $(touch /tmp/pwn) `reboot` ; rm -rf /"
        backend.create_project_job(job, "shop", "acme/shop", [], "English", evil_vision)
        self.assertGreater(len(self.calls), 0)
        init_call = self.calls[0]
        cmd, inp = init_call
        self.assertIn("--vision -", cmd)
        self.assertNotIn("$(touch /tmp/pwn)", cmd)
        self.assertNotIn("rm -rf", cmd)
        self.assertEqual(inp, evil_vision)
        assert "--vision -" in cmd and inp == evil_vision

    def test_add_worker_job_rejects_malicious_runtime(self):
        """add_worker_job must reject crafted runtime parameters containing shell metacharacters."""
        job = {"id": "test-6", "state": "running", "log": ""}
        backend.add_worker_job(job, runtime="claude; touch /tmp/pwn")
        self.assertEqual(job["state"], "failed")
        for cmd, _ in self.calls:
            self.assertNotIn("; touch /tmp/pwn", cmd)
            self.assertNotIn("touch", cmd)
        assert job["state"] == "failed"

    def test_connect_upstream_job_rejects_malicious_pf(self):
        """connect_upstream_job must reject crafted project flags containing shell metacharacters."""
        job = {"id": "test-7", "state": "running", "log": ""}
        backend.connect_upstream_job(job, pf="; touch /tmp/pwn;", changes={}, upstream=False, force=False)
        self.assertEqual(job["state"], "failed")
        for cmd, _ in self.calls:
            self.assertNotIn("; touch /tmp/pwn;", cmd)
            self.assertNotIn("touch", cmd)
        self.assertEqual(len(self.calls), 0)
        assert job["state"] == "failed" and len(self.calls) == 0

    def test_login_sessions_rejects_malicious_worker_name(self):
        """LoginSessions must reject worker names with shell metacharacters, and never open a session for one."""
        login = backend.LoginSessions()
        bad = "worker-1; touch /tmp/pwn"
        with patch.object(backend.subprocess, "Popen") as popen:
            self.assertEqual(login.start(bad), {"error": "bad worker"})
            self.assertEqual(login.stop(bad), {"error": "bad worker"})
            popen.assert_not_called()
        self.assertIn("error", login.write(bad, "dG9rZW4="))
        self.assertIn("error", login.resize(bad, 80, 24))
        self.assertFalse(login.read(bad)["running"])
        self.assertEqual(login.s, {})
        assert "error" in login.start(bad)

    def test_login_write_goes_to_the_session_terminal_only(self):
        """The browser terminal sends raw keystrokes (control keys are legitimate there); they reach only that worker's pty,
        as validated base64 of bounded size - never a shell."""
        import base64
        login = backend.LoginSessions()
        proc = MagicMock()
        proc.poll.return_value = None
        login.s["worker-1"] = {"proc": proc, "rfd": 99, "buf": "", "raw": "", "out": b"", "base": 0, "cols": 80, "started": 0}
        with patch.object(backend.os, "write", return_value=1) as w:
            self.assertEqual(login.write("worker-1", base64.b64encode(b"4/0AY0e-gValidAuthToken_123\r").decode()), {"ok": True})
            w.assert_called_once_with(99, b"4/0AY0e-gValidAuthToken_123\r")
            w.reset_mock()
            self.assertEqual(login.write("worker-1", "not base64!!"), {"error": "bad data"})
            self.assertEqual(login.write("worker-1", base64.b64encode(b"x" * 8193).decode()), {"error": "too much at once"})
            self.assertEqual(login.write("worker-2", "dG9rZW4="), {"error": "no running login session"})
            w.assert_not_called()

    def test_login_stop_counts_a_weekly_only_account_as_signed_in(self):
        """A fresh account's /usage has no "Five Hour" row; it is still signed in. An error text is not."""
        login = backend.LoginSessions()
        weekly = "Gemini Models\tWeekly Limit Remaining\t100%\t2026-10-22T20:27:30Z\n"
        with patch.object(backend, "ssh_run", return_value=weekly):
            self.assertTrue(login.stop("worker-8")["logged_in"])
        with patch.object(backend, "ssh_run", return_value="Error: not logged in\n"):
            self.assertFalse(login.stop("worker-8")["logged_in"])

    def test_poll_project_rejects_malicious_name(self):
        """poll_project must reject crafted project names and avoid calling ssh_run."""
        backend.poll_project("shop; id")
        self.assertEqual(len(self.calls), 0)
        self.assertIn("bad project", backend.FOREMAN_BY.get("shop; id", {}).get("error", ""))
        assert len(self.calls) == 0 and "bad project" in backend.FOREMAN_BY.get("shop; id", {}).get("error", "")

    def test_connect_upstream_job_passes_secrets_over_stdin_not_argv(self):
        """connect_upstream_job passes settings/tokens over stdin (json), never formatted into argv or cmd."""
        job = {"id": "test-8", "state": "running", "log": ""}
        sensitive_changes = {"UPSTREAM_TOKEN": "ghp_secret_token_12345", "UPSTREAM_URL": "https://github.com"}
        backend.connect_upstream_job(job, pf=" --project shop", changes=sensitive_changes, upstream=True, force=False)
        self.assertEqual(job["state"], "done")
        self.assertGreater(len(self.calls), 0)
        for cmd, inp in self.calls:
            self.assertNotIn("ghp_secret_token_12345", cmd)
        settings_call = next((cmd, inp) for cmd, inp in self.calls if "settings apply" in cmd)
        self.assertIn("ghp_secret_token_12345", settings_call[1])
        assert job["state"] == "done" and len(settings_call[1]) > 0

    def test_ssh_run_type_safety(self):
        """ssh_run and ssh_run_full reject non-string command arguments gracefully."""
        self.assertEqual(self.orig_ssh_run(None), "")
        self.assertEqual(backend.LAST_ERR["msg"], "remote_cmd must be a string")
        rc, out, err = self.orig_ssh_run_full(12345)
        self.assertEqual(rc, 1)
        self.assertIn("must be a string", err)
        assert self.orig_ssh_run(None) == "" and rc == 1


class TestDashboardReadinessApi(unittest.TestCase):
    """Test backend.readiness_summary() and GET /api/readiness routing, shape, auth, and secrets defenses."""

    @classmethod
    def setUpClass(cls):
        cls.orig_demo = srv.DEMO_MODE
        cls.orig_token = srv.DASHBOARD_TOKEN
        cls.orig_hosts = srv.DASHBOARD_HOSTS
        srv.DASHBOARD_TOKEN = "test-secret-token"
        srv.DASHBOARD_HOSTS = {"127.0.0.1", "localhost"}
        cls.httpd = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        srv.DEMO_MODE = cls.orig_demo
        srv.DASHBOARD_TOKEN = cls.orig_token
        srv.DASHBOARD_HOSTS = cls.orig_hosts
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def req(self, path, headers=None, host=None, token="test-secret-token"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        if token is not None:
            h["Cookie"] = f"af_token={token}"
        if headers:
            h.update(headers)
        c.request("GET", path, headers=h)
        r = c.getresponse()
        resp_headers = {k.lower(): v for k, v in r.getheaders()}
        body = r.read().decode(errors="replace")
        c.close()
        return r.status, resp_headers, body

    def test_readiness_summary_contract_shape(self):
        """readiness_summary returns documented JSON shape with runtimes and providers."""
        res = backend.readiness_summary()
        self.assertIsInstance(res, dict)
        self.assertIn("runtimes", res)
        self.assertIn("providers", res)
        self.assertIsInstance(res["runtimes"], dict)
        self.assertIsInstance(res["providers"], dict)
        self.assertGreater(len(res["runtimes"]), 0)
        self.assertGreater(len(res["providers"]), 0)

        for name, item in res["runtimes"].items():
            with self.subTest(runtime=name):
                self.assertIn("state", item)
                self.assertIn(item["state"], ("ok", "missing_setting", "missing_egress"))
                self.assertIn("detail", item)
                self.assertIsInstance(item["detail"], str)
                self.assertIn("missing_settings", item)
                self.assertIsInstance(item["missing_settings"], list)
                self.assertIn("missing_egress", item)
                self.assertIsInstance(item["missing_egress"], list)

        for name, item in res["providers"].items():
            with self.subTest(provider=name):
                self.assertIn("state", item)
                self.assertIn(item["state"], ("ok", "missing_setting", "missing_egress"))
                self.assertIn("detail", item)
                self.assertIsInstance(item["detail"], str)
                self.assertIn("missing_settings", item)
                self.assertIsInstance(item["missing_settings"], list)
                self.assertIn("missing_egress", item)
                self.assertIsInstance(item["missing_egress"], list)

        assert "runtimes" in res and "providers" in res and len(res["runtimes"]) > 0 and len(res["providers"]) > 0

    def test_readiness_summary_no_secrets_leaked(self):
        """readiness_summary never leaks secret values from environment into return payload."""
        canaries = {
            "ANTHROPIC_API_KEY": "CANARY_SECRET_ANTHROPIC_VALUE_1111",
            "OPENAI_API_KEY": "CANARY_SECRET_OPENAI_VALUE_2222",
            "GEMINI_API_KEY": "CANARY_SECRET_GEMINI_VALUE_3333",
            "QWEN_API_KEY": "CANARY_SECRET_QWEN_VALUE_4444",
            "DASHBOARD_TOKEN": "CANARY_SECRET_DASHBOARD_VALUE_5555",
            "UPSTREAM_TOKEN": "CANARY_SECRET_UPSTREAM_VALUE_6666",
        }
        with patch.dict(os.environ, canaries):
            res = backend.readiness_summary()
            payload = json.dumps(res)
            for secret_key, secret_val in canaries.items():
                self.assertNotIn(secret_val, payload, f"Secret {secret_key} leaked in readiness_summary JSON!")
        assert all(secret_val not in payload for secret_val in canaries.values())

    def test_readiness_summary_broken_import_degrades_gracefully(self):
        """When readiness import is unavailable, readiness_summary returns empty/error dict."""
        with patch.object(backend, "readiness", None):
            res = backend.readiness_summary()
            self.assertEqual(res.get("runtimes"), {})
            self.assertEqual(res.get("providers"), {})
            self.assertIn("error", res)
            self.assertTrue(len(res["error"]) > 0)
        assert res.get("runtimes") == {} and res.get("providers") == {} and bool(res.get("error"))

    def test_readiness_summary_exception_degrades_gracefully(self):
        """When readiness.all_status raises an exception, readiness_summary catches and returns error."""
        mock_readiness = MagicMock()
        mock_readiness.all_status.side_effect = RuntimeError("unexpected storage failure")
        with patch.object(backend, "readiness", mock_readiness):
            res = backend.readiness_summary()
            self.assertEqual(res.get("runtimes"), {})
            self.assertEqual(res.get("providers"), {})
            self.assertIn("error", res)
            self.assertIn("unexpected storage failure", res["error"])
        assert res.get("runtimes") == {} and res.get("providers") == {} and "unexpected storage failure" in res.get("error", "")

    def test_api_readiness_endpoint_success(self):
        """GET /api/readiness returns 200 with documented JSON shape."""
        status, headers, body = self.req("/api/readiness")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers.get("content-type", "").lower())
        data = json.loads(body)
        self.assertIn("runtimes", data)
        self.assertIn("providers", data)
        self.assertGreater(len(data["runtimes"]), 0)
        self.assertGreater(len(data["providers"]), 0)
        assert status == 200 and "runtimes" in data and "providers" in data

    def test_api_readiness_endpoint_host_check_gated(self):
        """GET /api/readiness rejects disallowed host headers with 403."""
        status, headers, body = self.req("/api/readiness", host="evil.attacker.com")
        self.assertEqual(status, 403)
        self.assertIn("host not allowed", body)
        assert status == 403 and "host not allowed" in body

    def test_api_readiness_endpoint_token_check_gated(self):
        """GET /api/readiness rejects requests with invalid or missing auth token with 401."""
        status, headers, body = self.req("/api/readiness", token=None)
        self.assertEqual(status, 401)
        self.assertIn("sign in", body)

        status_bad, _, _ = self.req("/api/readiness", token="wrong-invalid-token")
        self.assertEqual(status_bad, 401)
        assert status == 401 and status_bad == 401

    def test_api_readiness_broken_backend_returns_degraded_200_not_500(self):
        """When the in-process readiness module is unusable, GET /api/readiness/summary returns degraded 200 instead of 500."""
        with patch.object(srv, "readiness", None):
            status, headers, body = self.req("/api/readiness/summary")
            self.assertEqual(status, 200)
            data = json.loads(body)
            self.assertEqual(data.get("runtimes"), {})
            self.assertIn("error", data)
        assert status == 200 and data.get("runtimes") == {} and "error" in data

    def test_api_readiness_no_secret_leaks_in_http_response(self):
        """GET /api/readiness never leaks DASHBOARD_TOKEN or canary secrets in HTTP response."""
        canaries = {
            "ANTHROPIC_API_KEY": "CANARY_SECRET_ANTHROPIC_HTTP_7890",
            "OPENAI_API_KEY": "CANARY_SECRET_OPENAI_HTTP_7890",
        }
        with patch.dict(os.environ, canaries):
            status, _, body = self.req("/api/readiness", token=srv.DASHBOARD_TOKEN)
            self.assertEqual(status, 200)
            self.assertNotIn(srv.DASHBOARD_TOKEN, body)
            for secret_val in canaries.values():
                self.assertNotIn(secret_val, body)
        assert status == 200 and srv.DASHBOARD_TOKEN not in body and all(val not in body for val in canaries.values())

    def test_api_readiness_summary_endpoint_lists_all_six_new_runtimes_with_non_empty_cost_quota(self):
        """Invariant: GET /api/readiness/summary response lists all six new runtimes

        (copilot, mistral-vibe, ollama, vllm, lmstudio, llamacpp) with a non-empty cost/quota
        cost_model classification for each runtime.
        """
        six_new_runtimes = ["copilot", "mistral-vibe", "ollama", "vllm", "lmstudio", "llamacpp"]

        # 1. Exercise the readiness-summary HTTP API endpoint added by task 0223
        status, headers, body = self.req("/api/readiness/summary")
        self.assertEqual(status, 200)
        assert status == 200
        self.assertIn("application/json", headers.get("content-type", "").lower())
        assert "application/json" in headers.get("content-type", "").lower()

        data = json.loads(body)
        self.assertIsInstance(data, dict)
        assert isinstance(data, dict)
        self.assertIn("runtimes", data)
        assert "runtimes" in data
        self.assertIsInstance(data["runtimes"], dict)
        assert isinstance(data["runtimes"], dict)

        # 2. Verify all six new runtimes are present and have non-empty cost/quota classifications
        for rt in six_new_runtimes:
            with self.subTest(runtime=rt):
                # Fail if any runtime is missing
                self.assertIn(rt, data["runtimes"], f"Runtime {rt!r} missing from readiness summary response")
                assert rt in data["runtimes"]

                entry = data["runtimes"][rt]
                self.assertIsInstance(entry, dict, f"Runtime {rt!r} entry must be a dictionary")
                assert isinstance(entry, dict)

                # Cost/quota field must exist and be non-null/non-empty
                self.assertIn("cost_model", entry, f"Runtime {rt!r} must have a cost_model field")
                assert "cost_model" in entry
                cm = entry["cost_model"]
                self.assertIsInstance(cm, dict, f"Runtime {rt!r} cost_model must be a dictionary, got {type(cm)}")
                assert isinstance(cm, dict)

                self.assertIn("kind", cm, f"Runtime {rt!r} cost_model missing 'kind' key")
                assert "kind" in cm
                self.assertIn("detail", cm, f"Runtime {rt!r} cost_model missing 'detail' key")
                assert "detail" in cm

                # Fail if kind or detail returns a null or empty classification
                self.assertIsNotNone(cm["kind"], f"Runtime {rt!r} cost_model kind must not be None")
                assert cm["kind"] is not None
                self.assertIsInstance(cm["kind"], str)
                assert isinstance(cm["kind"], str)
                self.assertTrue(len(cm["kind"].strip()) > 0, f"Runtime {rt!r} cost_model kind must not be empty")
                assert len(cm["kind"].strip()) > 0

                self.assertIsNotNone(cm["detail"], f"Runtime {rt!r} cost_model detail must not be None")
                assert cm["detail"] is not None
                self.assertIsInstance(cm["detail"], str)
                assert isinstance(cm["detail"], str)
                self.assertTrue(len(cm["detail"].strip()) > 0, f"Runtime {rt!r} cost_model detail must not be empty")
                assert len(cm["detail"].strip()) > 0

                # Also verify contract status fields (ready and reason)
                self.assertIn("ready", entry)
                assert "ready" in entry
                self.assertIn(entry["ready"], (True, False, None))
                assert entry["ready"] in (True, False, None)
                self.assertIn("reason", entry)
                assert "reason" in entry
                self.assertIsInstance(entry["reason"], str)
                assert isinstance(entry["reason"], str)
                self.assertFalse(entry["reason"].startswith("readiness check failed:"),
                                 f"Runtime {rt!r} reason indicates check failure: {entry['reason']}")
                assert not entry["reason"].startswith("readiness check failed:")

        # In aggregate, all six runtimes present
        self.assertTrue(all(rt in data["runtimes"] for rt in six_new_runtimes))
        assert all(rt in data["runtimes"] for rt in six_new_runtimes)


if __name__ == "__main__":
    unittest.main()
