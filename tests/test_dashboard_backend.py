#!/usr/bin/env python3
"""Tests for web/dashboard/backend.py command injection defences.

Public interface:
- Run directly: python3 tests/test_dashboard_backend.py
- Run via unittest: python3 -m unittest tests/test_dashboard_backend.py

Important invariants:
- Remote commands constructed for ssh_run / ssh_run_full validate all parameters
  (project name, repo, workers, language, runtime, project flag) before formatting.
- Owner vision and secrets travel on stdin, never interpolated in remote shell strings or argv.
- LoginSessions rejects invalid worker names and pty breakout control sequences.
"""
import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

spec = importlib.util.spec_from_file_location("backend", os.path.join(ROOT, "web", "dashboard", "backend.py"))
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


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

    def test_create_project_job_rejects_malicious_name(self):
        """create_project_job must reject crafted project names containing shell metacharacters."""
        job = {"id": "test-1", "state": "running", "log": ""}
        backend.create_project_job(job, "proj; touch /tmp/pwn", "acme/shop", [], "English", "our vision")
        self.assertEqual(job["state"], "failed")
        for cmd, _ in self.calls:
            self.assertNotIn("; touch /tmp/pwn", cmd)
            self.assertNotIn("touch", cmd)
        self.assertEqual(len(self.calls), 0)

    def test_create_project_job_rejects_malicious_repo(self):
        """create_project_job must reject crafted repo names containing shell metacharacters."""
        job = {"id": "test-2", "state": "running", "log": ""}
        backend.create_project_job(job, "shop", "acme/shop; touch /tmp/pwn", [], "English", "our vision")
        self.assertEqual(job["state"], "failed")
        for cmd, _ in self.calls:
            self.assertNotIn("; touch /tmp/pwn", cmd)
            self.assertNotIn("touch", cmd)
        self.assertEqual(len(self.calls), 0)

    def test_create_project_job_rejects_malicious_workers(self):
        """create_project_job must reject crafted worker lists containing shell metacharacters."""
        job = {"id": "test-3", "state": "running", "log": ""}
        backend.create_project_job(job, "shop", "acme/shop", ["worker-1; touch /tmp/pwn"], "English", "our vision")
        self.assertEqual(job["state"], "failed")
        for cmd, _ in self.calls:
            self.assertNotIn("touch", cmd)
        self.assertEqual(len(self.calls), 0)

    def test_create_project_job_rejects_malicious_lang(self):
        """create_project_job must reject crafted language strings containing shell metacharacters."""
        job = {"id": "test-4", "state": "running", "log": ""}
        backend.create_project_job(job, "shop", "acme/shop", [], "English'; touch /tmp/pwn #", "our vision")
        self.assertEqual(job["state"], "failed")
        for cmd, _ in self.calls:
            self.assertNotIn("touch", cmd)
        self.assertEqual(len(self.calls), 0)

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

    def test_add_worker_job_rejects_malicious_runtime(self):
        """add_worker_job must reject crafted runtime parameters containing shell metacharacters."""
        job = {"id": "test-6", "state": "running", "log": ""}
        backend.add_worker_job(job, runtime="claude; touch /tmp/pwn")
        self.assertEqual(job["state"], "failed")
        for cmd, _ in self.calls:
            self.assertNotIn("; touch /tmp/pwn", cmd)
            self.assertNotIn("touch", cmd)

    def test_connect_upstream_job_rejects_malicious_pf(self):
        """connect_upstream_job must reject crafted project flags containing shell metacharacters."""
        job = {"id": "test-7", "state": "running", "log": ""}
        backend.connect_upstream_job(job, pf="; touch /tmp/pwn;", changes={}, upstream=False, force=False)
        self.assertEqual(job["state"], "failed")
        for cmd, _ in self.calls:
            self.assertNotIn("; touch /tmp/pwn;", cmd)
            self.assertNotIn("touch", cmd)
        self.assertEqual(len(self.calls), 0)

    def test_login_sessions_rejects_malicious_worker_name(self):
        """LoginSessions must reject worker names with shell metacharacters."""
        login = backend.LoginSessions()
        self.assertIn("error", login.start("worker-1; touch /tmp/pwn"))
        self.assertIn("error", login.stop("worker-1; touch /tmp/pwn"))
        self.assertIn("error", login.send("worker-1; touch /tmp/pwn", "token"))
        self.assertIn("error", login.read("worker-1; touch /tmp/pwn"))

    def test_login_sessions_send_rejects_control_sequences(self):
        """LoginSessions.send must reject control characters (newlines, escapes, Ctrl-C) to prevent breakout."""
        login = backend.LoginSessions()
        sess_mock = {"proc": MagicMock(), "rfd": 1, "wf": MagicMock(), "buf": ""}
        sess_mock["proc"].poll.return_value = None
        login.s["worker-1"] = sess_mock

        breakout_payloads = [
            "token\nmalicious_cmd",
            "token\rmalicious_cmd",
            "\x03malicious_cmd",
            "token\x1b[2Jmalicious",
            "token\x04",
            "token\x1a",
        ]
        for payload in breakout_payloads:
            with self.subTest(payload=payload):
                res = login.send("worker-1", payload)
                self.assertIn("error", res)
                sess_mock["wf"].write.assert_not_called()

        # Legitimate single-line token is accepted
        res = login.send("worker-1", "4/0AY0e-gValidAuthToken_123")
        self.assertEqual(res, {"ok": True})
        sess_mock["wf"].write.assert_called_once_with(b"4/0AY0e-gValidAuthToken_123\r")

    def test_poll_project_rejects_malicious_name(self):
        """poll_project must reject crafted project names and avoid calling ssh_run."""
        backend.poll_project("shop; id")
        self.assertEqual(len(self.calls), 0)
        self.assertIn("bad project", backend.FOREMAN_BY.get("shop; id", {}).get("error", ""))

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

    def test_ssh_run_type_safety(self):
        """ssh_run and ssh_run_full reject non-string command arguments gracefully."""
        self.assertEqual(self.orig_ssh_run(None), "")
        self.assertEqual(backend.LAST_ERR["msg"], "remote_cmd must be a string")
        rc, out, err = self.orig_ssh_run_full(12345)
        self.assertEqual(rc, 1)
        self.assertIn("must be a string", err)


if __name__ == "__main__":
    unittest.main()

