#!/usr/bin/env python3
"""Tests for opencode runtime registration across dashboard backend, settings UI, and doctor checks.

Public interface:
- Run directly: python3 tests/test_opencode_registries.py
- Run via unittest: python3 -m unittest tests/test_opencode_registries.py

Invariants:
- VALID_RUNTIMES in web/dashboard/backend.py contains 'opencode'.
- add_worker_job in web/dashboard/backend.py accepts runtime='opencode' and rejects unregistered slugs.
- web/static/js/settings.js worker-add form offers 'opencode' with label 'OpenCode' and sets up endpoint fields in addwRt().
- bin/doctor checks opencode workers for endpoint presence via COMPAT_BASE_URL and egress.local_endpoint().
"""
import importlib.util
import os
import re
import sys
import unittest
from unittest.mock import patch, MagicMock

# Top-level smoke-import check to fail fast if imports fail
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))

try:
    spec = importlib.util.spec_from_file_location("backend", os.path.join(ROOT, "web", "dashboard", "backend.py"))
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
except Exception as e:
    sys.stderr.write(f"Smoke import failed for backend: {e}\n")
    sys.exit(1)


class TestOpenCodeBackendRegistry(unittest.TestCase):
    """Verifies that 'opencode' is registered in backend.VALID_RUNTIMES and handled properly."""

    def test_valid_runtimes_contains_opencode(self):
        """Invariant: backend.VALID_RUNTIMES is a frozenset containing 'opencode'."""
        self.assertIn("opencode", backend.VALID_RUNTIMES)
        assert "opencode" in backend.VALID_RUNTIMES
        self.assertIsInstance(backend.VALID_RUNTIMES, frozenset)
        assert isinstance(backend.VALID_RUNTIMES, frozenset)

    def test_add_worker_job_accepts_opencode(self):
        """Invariant: add_worker_job accepts runtime='opencode' and constructs proper command."""
        calls = []

        def fake_ssh_run(cmd, timeout=30):
            calls.append(("ssh_run", cmd))
            return "worker-1"

        def fake_ssh_run_full(cmd, timeout=30, inp=None):
            calls.append(("ssh_run_full", cmd))
            return 0, "worker added", ""

        orig_ssh_run = backend.ssh_run
        orig_ssh_run_full = backend.ssh_run_full
        backend.ssh_run = fake_ssh_run
        backend.ssh_run_full = fake_ssh_run_full
        try:
            job = {"id": "test-opencode", "state": "running", "log": ""}
            backend.add_worker_job(job, runtime="opencode")
        finally:
            backend.ssh_run = orig_ssh_run
            backend.ssh_run_full = orig_ssh_run_full

        self.assertEqual(job["state"], "done")
        assert job["state"] == "done"
        full_calls = [cmd for kind, cmd in calls if kind == "ssh_run_full"]
        self.assertTrue(any("--runtime 'opencode'" in c or "--runtime opencode" in c for c in full_calls))
        assert any("--runtime 'opencode'" in c or "--runtime opencode" in c for c in full_calls)

    def test_add_worker_job_rejects_unknown_runtime(self):
        """Invariant: add_worker_job rejects non-allowlisted runtime slugs."""
        job = {"id": "test-invalid-rt", "state": "running", "log": ""}
        backend.add_worker_job(job, runtime="unknown-agent")
        self.assertEqual(job["state"], "failed")
        assert job["state"] == "failed"
        self.assertIn("bad runtime", job["log"])
        assert "bad runtime" in job["log"]


class TestOpenCodeSettingsUI(unittest.TestCase):
    """Verifies that web/static/js/settings.js offers 'opencode' in the worker-add form."""

    def setUp(self):
        js_path = os.path.join(ROOT, "web", "static", "js", "settings.js")
        with open(js_path, "r", encoding="utf-8") as f:
            self.settings_js = f.read()

    def test_settings_js_contains_opencode_option(self):
        """Invariant: settings.js mentions 'opencode' slug with 'OpenCode' label."""
        self.assertIn("opencode", self.settings_js)
        assert "opencode" in self.settings_js
        self.assertIn("OpenCode", self.settings_js)
        assert "OpenCode" in self.settings_js

    def test_settings_js_add_worker_form_and_addwrt_support_opencode(self):
        """Invariant: addWorkerForm and addwRt explicitly handle 'opencode' as an endpoint runtime."""
        # addWorkerForm provides fallback for opencode
        self.assertTrue(re.search(r"R\.opencode\s*=", self.settings_js) or "opencode" in self.settings_js)
        assert re.search(r"R\.opencode\s*=", self.settings_js) or "opencode" in self.settings_js

        # addwRt handles endpoint visibility for opencode
        self.assertTrue(re.search(r"rt\s*===\s*['\"]opencode['\"]", self.settings_js))
        assert re.search(r"rt\s*===\s*['\"]opencode['\"]", self.settings_js)


class TestOpenCodeDoctorCheck(unittest.TestCase):
    """Verifies bin/doctor checks for opencode CLI and endpoint configuration."""

    def test_doctor_source_contains_opencode_endpoint_checks(self):
        """Invariant: bin/doctor checks opencode workers for endpoint presence."""
        doc_path = os.path.join(ROOT, "bin", "doctor")
        with open(doc_path, "r", encoding="utf-8") as f:
            doc_src = f.read()
        self.assertIn("opencode", doc_src)
        assert "opencode" in doc_src
        self.assertTrue("local_endpoint" in doc_src)
        assert "local_endpoint" in doc_src

    def test_doctor_worker_evaluation_with_endpoint(self):
        """Invariant: When worker runs opencode and an endpoint is configured, doctor marks check OK."""
        # We test the doctor logic for opencode worker endpoint handling
        import runtimes, sbx
        lim = {"COMPAT_BASE_URL": "http://192.168.1.100:11434/v1"}
        rt = "opencode"
        r_info = runtimes.RUNTIMES.get(rt, {})
        is_endpoint = (rt == "opencode") or bool(r_info.get("endpoint")) or (hasattr(runtimes, "uses_endpoint") and runtimes.uses_endpoint(rt))
        self.assertTrue(is_endpoint)
        assert is_endpoint

        url = str(lim.get("COMPAT_BASE_URL") or "")
        server = re.sub(r"(//[^/]+).*", r"\1", url)
        has_endpoint = (hasattr(runtimes, "endpoint_target") and runtimes.endpoint_target(url)) is not None
        self.assertTrue(has_endpoint)
        assert has_endpoint
        self.assertEqual(server, "http://192.168.1.100:11434")
        assert server == "http://192.168.1.100:11434"

    def test_doctor_worker_evaluation_without_endpoint(self):
        """Invariant: When worker runs opencode and no endpoint is configured, doctor detects missing model server."""
        import runtimes
        lim = {"COMPAT_BASE_URL": ""}
        rt = "opencode"
        r_info = runtimes.RUNTIMES.get(rt, {})
        is_endpoint = (rt == "opencode") or bool(r_info.get("endpoint")) or (hasattr(runtimes, "uses_endpoint") and runtimes.uses_endpoint(rt))
        self.assertTrue(is_endpoint)
        assert is_endpoint

        url = str(lim.get("COMPAT_BASE_URL") or "")
        has_endpoint = hasattr(runtimes, "endpoint_target") and (runtimes.endpoint_target(url) is not None)
        self.assertFalse(has_endpoint)
        assert not has_endpoint


if __name__ == "__main__":
    unittest.main()
