#!/usr/bin/env python3
"""Unit tests for runtime and provider readiness diagnostic section in bin/doctor.

Public interface:
- Run directly: python3 tests/test_doctor_readiness.py
- Run via unittest: python3 -m unittest tests/test_doctor_readiness.py

Important invariants:
- bin/doctor prints exactly one line per runtime (10 entries) and per provider (6 entries).
- Output format matches doctor conventions with [OK] and [WARN] markers.
- Secrets are NEVER printed in stdout, stderr, or check dictionaries.
- bin/doctor exits 0 when every readiness item is merely missing_setting/missing_egress.
- Readiness checks report level='warn' for unconfigured runtimes/providers without causing hard doctor failure.
"""
import importlib.machinery
import importlib.util
import io
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))

# Top-level smoke-import check to fail loudly if import breaks
try:
    import providers
    import egress
    import runtimes
    import sbx
    from foreman import readiness

    doc_path = os.path.join(ROOT, "bin", "doctor")
    loader = importlib.machinery.SourceFileLoader("doctor", doc_path)
    spec = importlib.util.spec_from_loader("doctor", loader)
    doctor = importlib.util.module_from_spec(spec)
    loader.exec_module(doctor)
    if not hasattr(doctor, "check_readiness"):
        raise AttributeError("doctor missing check_readiness")
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)


class TestDoctorReadiness(unittest.TestCase):
    """Test suite verifying doctor diagnostic checks for runtime and provider readiness."""

    def setUp(self):
        """Prepare clean test environment and doctor state before each test."""
        doctor.CHECKS.clear()
        doctor.BAD.clear()
        doctor.WARN.clear()
        doctor.SECTION[0] = ""
        self.orig_json = doctor.JSON
        doctor.JSON = True
        self.orig_env = dict(os.environ)

    def tearDown(self):
        """Restore environment after test completion."""
        doctor.JSON = self.orig_json
        os.environ.clear()
        os.environ.update(self.orig_env)

    def test_smoke_check_readiness_exists(self):
        """Invariant: check_readiness exists on doctor module and is callable."""
        self.assertTrue(callable(doctor.check_readiness))
        assert callable(doctor.check_readiness)

    def test_readiness_counts_all_registered_runtimes_and_providers(self):
        """Invariant: check_readiness evaluates all registered runtimes and providers."""
        res = doctor.check_readiness()
        checks = res["checks"]
        expected_total = len(readiness.runtime_items()) + len(readiness.provider_items())
        self.assertEqual(len(checks), expected_total)
        for name in readiness.runtime_items():
            self.assertTrue(any(f" {name}: " in c["message"] for c in checks))
        for name in readiness.provider_items():
            self.assertTrue(any(f" {name}: " in c["message"] for c in checks))
        assert len(checks) == expected_total

    def test_readiness_output_markers_and_format(self):
        """Invariant: Every line has name, [OK] or [WARN] marker, and detail sentence."""
        res = doctor.check_readiness()
        for c in res["checks"]:
            msg = c["message"]
            self.assertTrue(msg.startswith("[OK] ") or msg.startswith("[WARN] "))
            self.assertEqual(c["level"], "ok" if msg.startswith("[OK] ") else "warn")
            self.assertIn(": ", msg)
        assert len(res["checks"]) > 0

    def test_readiness_custom_status_mapping(self):
        """Invariant: check_readiness maps ok to [OK] and missing_setting/egress to [WARN]."""
        mock_status = {
            "runtimes": {
                "custom-rt": {"state": "ok", "detail": "Runtime 'custom-rt' is ready."},
                "custom-rt2": {"state": "missing_setting", "detail": "Runtime 'custom-rt2' missing KEY."},
            },
            "providers": {
                "custom-pr": {"state": "missing_egress", "detail": "Provider 'custom-pr' missing egress."},
            },
        }
        res = doctor.check_readiness(mock_status)
        self.assertEqual(len(res["ok"]), 1)
        self.assertEqual(len(res["warn"]), 2)
        self.assertEqual(len(res["bad"]), 0)
        self.assertTrue(any("[OK] custom-rt: " in c["message"] for c in res["ok"]))
        self.assertTrue(any("[WARN] custom-rt2: " in c["message"] for c in res["warn"]))
        self.assertTrue(any("[WARN] custom-pr: " in c["message"] for c in res["warn"]))
        assert len(res["ok"]) == 1 and len(res["warn"]) == 2

    def test_readiness_never_leaks_secrets(self):
        """Invariant: Secret keys and tokens never appear in doctor output or check objects."""
        canary = "CANARY_SECRET_READINESS_TOKEN_998877"
        os.environ["ANTHROPIC_API_KEY"] = canary
        os.environ["OPENAI_API_KEY"] = canary
        os.environ["GEMINI_API_KEY"] = canary

        doctor.JSON = False
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            res = doctor.check_readiness()
        out = buf.getvalue()

        self.assertNotIn(canary, out)
        all_messages = " ".join(c["message"] for c in res["checks"])
        self.assertNotIn(canary, all_messages)
        assert canary not in out and canary not in all_messages

    def test_doctor_exits_zero_when_readiness_items_are_merely_unconfigured(self):
        """Invariant: doctor exits 0 when every item is merely missing_setting/missing_egress."""
        # Clean out any previous bad or warn
        doctor.BAD.clear()
        doctor.WARN.clear()
        doctor.CHECKS.clear()

        # Run check_readiness where all items are missing_setting
        mock_status = {
            "runtimes": {
                f"rt-{i}": {"state": "missing_setting", "detail": f"Runtime rt-{i} missing config."}
                for i in range(10)
            },
            "providers": {
                f"pr-{i}": {"state": "missing_egress", "detail": f"Provider pr-{i} missing egress."}
                for i in range(6)
            },
        }
        res = doctor.check_readiness(mock_status)
        self.assertEqual(len(res["warn"]), 16)
        self.assertEqual(len(res["bad"]), 0)
        # Because readiness warnings are optional=True, doctor.WARN must remain empty
        self.assertEqual(len(doctor.WARN), 0)
        self.assertEqual(len(doctor.BAD), 0)
        # When BAD and WARN are empty, exit code evaluates to 0
        exit_code = 2 if doctor.BAD else 1 if doctor.WARN else 0
        self.assertEqual(exit_code, 0)
        assert exit_code == 0

    def test_doctor_cli_subprocess_contains_readiness_section(self):
        """Invariant: Running bin/doctor via subprocess includes == Readiness section with all entries."""
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "bin", "doctor"), "--quick"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertIn("== Readiness", proc.stdout)
        self.assertIn("✔ [OK] agy: Runtime 'agy' is ready.", proc.stdout)
        self.assertIn("[WARN] claude: Runtime 'claude' is missing setting", proc.stdout)
        self.assertIn("✔ [OK] claude-cli: Provider 'claude-cli' is ready.", proc.stdout)
        assert "== Readiness" in proc.stdout


if __name__ == "__main__":
    unittest.main()
