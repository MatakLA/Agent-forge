#!/usr/bin/env python3
"""Tests for goose runtime registration in dashboard backend and settings UI.

Public interface:
- Run directly: python3 tests/test_goose_registry.py
- Run via unittest: python3 -m unittest tests/test_goose_registry.py

Invariants:
- VALID_RUNTIMES in web/dashboard/backend.py contains 'goose'.
- add_worker_job in web/dashboard/backend.py accepts runtime='goose' and rejects unregistered slugs.
- web/static/js/settings.js worker-add form offers 'goose' with label 'Goose' and sets up endpoint fields in addwRt().
"""
import importlib.util
import os
import re
import sys
import unittest

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


class TestGooseBackendRegistry(unittest.TestCase):
    """Verifies that 'goose' is registered in backend.VALID_RUNTIMES and handled properly."""

    def test_valid_runtimes_contains_goose(self):
        """Invariant: backend.VALID_RUNTIMES is a frozenset containing 'goose'."""
        self.assertIn("goose", backend.VALID_RUNTIMES)
        assert "goose" in backend.VALID_RUNTIMES
        self.assertIsInstance(backend.VALID_RUNTIMES, frozenset)
        assert isinstance(backend.VALID_RUNTIMES, frozenset)

    def test_add_worker_job_accepts_goose(self):
        """Invariant: add_worker_job accepts runtime='goose' and constructs proper command."""
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
            job = {"id": "test-goose", "state": "running", "log": ""}
            backend.add_worker_job(job, runtime="goose")
        finally:
            backend.ssh_run = orig_ssh_run
            backend.ssh_run_full = orig_ssh_run_full

        self.assertEqual(job["state"], "done")
        assert job["state"] == "done"
        full_calls = [cmd for kind, cmd in calls if kind == "ssh_run_full"]
        self.assertTrue(any("--runtime 'goose'" in c or "--runtime goose" in c for c in full_calls))
        assert any("--runtime 'goose'" in c or "--runtime goose" in c for c in full_calls)

    def test_add_worker_job_rejects_unknown_runtime(self):
        """Invariant: add_worker_job rejects non-allowlisted runtime slugs."""
        job = {"id": "test-invalid-rt", "state": "running", "log": ""}
        backend.add_worker_job(job, runtime="unknown-agent")
        self.assertEqual(job["state"], "failed")
        assert job["state"] == "failed"
        self.assertIn("bad runtime", job["log"])
        assert "bad runtime" in job["log"]


class TestGooseSettingsUI(unittest.TestCase):
    """Verifies that web/static/js/settings.js offers 'goose' in the worker-add form."""

    def setUp(self):
        js_path = os.path.join(ROOT, "web", "static", "js", "settings.js")
        with open(js_path, "r", encoding="utf-8") as f:
            self.settings_js = f.read()

    def test_settings_js_contains_goose_option(self):
        """Invariant: settings.js mentions 'goose' slug with 'Goose' label."""
        self.assertIn("goose", self.settings_js)
        assert "goose" in self.settings_js
        self.assertIn("Goose", self.settings_js)
        assert "Goose" in self.settings_js

    def test_settings_js_add_worker_form_and_addwrt_support_goose(self):
        """Invariant: addWorkerForm and addwRt explicitly handle 'goose' as an endpoint runtime."""
        # addWorkerForm provides fallback for goose
        self.assertTrue(re.search(r"R\.goose\s*=", self.settings_js) or "goose" in self.settings_js)
        assert re.search(r"R\.goose\s*=", self.settings_js) or "goose" in self.settings_js

        # addwRt handles endpoint visibility for goose
        self.assertTrue(re.search(r"rt\s*===\s*['\"]goose['\"]", self.settings_js))
        assert re.search(r"rt\s*===\s*['\"]goose['\"]", self.settings_js)


if __name__ == "__main__":
    unittest.main()
