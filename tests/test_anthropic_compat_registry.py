#!/usr/bin/env python3
"""Tests for anthropic-compat runtime registration in dashboard backend, settings UI, and docs.

Public interface:
- Run directly: python3 tests/test_anthropic_compat_registry.py
- Run via unittest: python3 -m unittest tests/test_anthropic_compat_registry.py

Invariants:
- VALID_RUNTIMES in web/dashboard/backend.py contains 'anthropic-compat'.
- add_worker_job in web/dashboard/backend.py accepts runtime='anthropic-compat' and rejects unregistered slugs.
- web/static/js/settings.js worker-add form offers 'anthropic-compat' with human-readable label and handles endpoint fields.
- docs/RUNTIMES.md documents 'anthropic-compat' setup, pinned-endpoint requirements, and cost model.
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


class TestAnthropicCompatBackendRegistry(unittest.TestCase):
    """Verifies that 'anthropic-compat' is registered in backend.VALID_RUNTIMES and handled properly."""

    def test_valid_runtimes_contains_anthropic_compat(self):
        """Invariant: backend.VALID_RUNTIMES is a frozenset containing 'anthropic-compat'."""
        self.assertIn("anthropic-compat", backend.VALID_RUNTIMES)
        assert "anthropic-compat" in backend.VALID_RUNTIMES
        self.assertIsInstance(backend.VALID_RUNTIMES, frozenset)
        assert isinstance(backend.VALID_RUNTIMES, frozenset)

    def test_add_worker_job_accepts_anthropic_compat(self):
        """Invariant: add_worker_job accepts runtime='anthropic-compat' and constructs proper command."""
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
            job = {"id": "test-anthropic-compat", "state": "running", "log": ""}
            backend.add_worker_job(job, runtime="anthropic-compat")
        finally:
            backend.ssh_run = orig_ssh_run
            backend.ssh_run_full = orig_ssh_run_full

        self.assertEqual(job["state"], "done")
        assert job["state"] == "done"
        full_calls = [cmd for kind, cmd in calls if kind == "ssh_run_full"]
        self.assertTrue(any("--runtime 'anthropic-compat'" in c or "--runtime anthropic-compat" in c for c in full_calls))
        assert any("--runtime 'anthropic-compat'" in c or "--runtime anthropic-compat" in c for c in full_calls)

    def test_add_worker_job_rejects_unknown_runtime(self):
        """Invariant: add_worker_job rejects non-allowlisted runtime slugs."""
        job = {"id": "test-invalid-rt", "state": "running", "log": ""}
        backend.add_worker_job(job, runtime="unknown-agent")
        self.assertEqual(job["state"], "failed")
        assert job["state"] == "failed"
        self.assertIn("bad runtime", job["log"])
        assert "bad runtime" in job["log"]


class TestAnthropicCompatSettingsUI(unittest.TestCase):
    """Verifies that web/static/js/settings.js offers 'anthropic-compat' in the worker-add form."""

    def setUp(self):
        js_path = os.path.join(ROOT, "web", "static", "js", "settings.js")
        with open(js_path, "r", encoding="utf-8") as f:
            self.settings_js = f.read()

    def test_settings_js_contains_anthropic_compat_option(self):
        """Invariant: settings.js mentions 'anthropic-compat' slug with human-readable label."""
        self.assertIn("anthropic-compat", self.settings_js)
        assert "anthropic-compat" in self.settings_js
        self.assertIn("Anthropic-compatible endpoint", self.settings_js)
        assert "Anthropic-compatible endpoint" in self.settings_js

    def test_settings_js_add_worker_form_and_addwrt_support_anthropic_compat(self):
        """Invariant: addWorkerForm and addwRt explicitly handle 'anthropic-compat' as an endpoint runtime."""
        self.assertTrue("anthropic-compat" in self.settings_js)
        assert "anthropic-compat" in self.settings_js
        self.assertTrue(re.search(r"rt\s*===\s*['\"]anthropic-compat['\"]", self.settings_js) is not None)
        assert re.search(r"rt\s*===\s*['\"]anthropic-compat['\"]", self.settings_js) is not None


class TestAnthropicCompatDocs(unittest.TestCase):
    """Verifies that docs/RUNTIMES.md documents setup, endpoint requirements, credentials and cost model."""

    def setUp(self):
        doc_path = os.path.join(ROOT, "docs", "RUNTIMES.md")
        with open(doc_path, "r", encoding="utf-8") as f:
            self.doc_content = f.read()

    def test_docs_contain_anthropic_compat_section_and_requirements(self):
        """Invariant: docs/RUNTIMES.md has '## Anthropic-compatible endpoint' section with setup, egress, and cost model."""
        self.assertIn("## Anthropic-compatible endpoint", self.doc_content)
        assert "## Anthropic-compatible endpoint" in self.doc_content
        self.assertIn("anthropic-compat", self.doc_content)
        assert "anthropic-compat" in self.doc_content
        self.assertIn("COMPAT_BASE_URL", self.doc_content)
        assert "COMPAT_BASE_URL" in self.doc_content
        self.assertTrue("Egress" in self.doc_content or "egress" in self.doc_content)
        assert "Egress" in self.doc_content or "egress" in self.doc_content
        self.assertTrue("unknown" in self.doc_content.lower())
        assert "unknown" in self.doc_content.lower()


if __name__ == "__main__":
    unittest.main()
