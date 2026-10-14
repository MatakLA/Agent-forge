#!/usr/bin/env python3
"""Unit tests for orchestrator provider diagnostic checks in bin/doctor.

Public interface:
- Run directly: python3 tests/test_doctor_providers.py
- Run via unittest: python3 -m unittest tests/test_doctor_providers.py

Important invariants:
- bin/doctor checks openai-api and gemini-api when listed in PLAN_PROVIDERS / REVIEW_PROVIDERS.
- Missing keys, missing models, or invalid URLs emit [FAIL] or [WARN] with clear remediation.
- Valid configurations emit [OK] with provider and model names; secrets are NEVER printed.
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
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

    doc_path = os.path.join(ROOT, "bin", "doctor")
    loader = importlib.machinery.SourceFileLoader("doctor", doc_path)
    spec = importlib.util.spec_from_loader("doctor", loader)
    doctor = importlib.util.module_from_spec(spec)
    loader.exec_module(doctor)
    if not hasattr(doctor, "check_orchestrator_providers"):
        raise AttributeError("doctor missing check_orchestrator_providers")
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)


class TestDoctorProviders(unittest.TestCase):
    """Test suite verifying doctor diagnostic checks for external orchestrator providers."""

    def setUp(self):
        """Prepare clean test environment and state for each test method."""
        doctor.CHECKS.clear()
        doctor.BAD.clear()
        doctor.WARN.clear()
        doctor.SECTION[0] = ""
        self.orig_json = doctor.JSON
        doctor.JSON = True
        self.orig_env = dict(os.environ)
        for k in ("PLAN_PROVIDERS", "REVIEW_PROVIDERS", "OPENAI_MODEL", "GEMINI_MODEL",
                  "OPENAI_API_BASE", "OPENAI_API_KEY", "GEMINI_API_KEY", "openai_api_key", "gemini_api_key"):
            os.environ.pop(k, None)

    def tearDown(self):
        """Restore environment after test completion."""
        doctor.JSON = self.orig_json
        os.environ.clear()
        os.environ.update(self.orig_env)

    def test_smoke_check_orchestrator_providers_exists(self):
        """Invariant: check_orchestrator_providers exists and is callable."""
        self.assertTrue(callable(doctor.check_orchestrator_providers))
        assert callable(doctor.check_orchestrator_providers)

    def test_providers_unconfigured(self):
        """Invariant: When orchestrator providers are unconfigured, doctor outputs [OK] and no errors."""
        res = doctor.check_orchestrator_providers({"PLAN_PROVIDERS": "", "REVIEW_PROVIDERS": ""})
        self.assertEqual(len(res["bad"]), 0)
        self.assertEqual(len(res["warn"]), 0)
        self.assertTrue(any("[OK]" in c["message"] for c in res["ok"]))
        assert len(res["bad"]) == 0 and len(res["warn"]) == 0

    def test_default_subscription_providers_only(self):
        """Invariant: Default subscription providers (claude-cli, agy-claude) do not trigger API checks."""
        conf = {"PLAN_PROVIDERS": "claude-cli,agy-claude,agy-gemini", "REVIEW_PROVIDERS": "agy-claude,claude-cli"}
        res = doctor.check_orchestrator_providers(conf)
        self.assertEqual(len(res["bad"]), 0)
        self.assertEqual(len(res["warn"]), 0)
        assert len(res["bad"]) == 0

    def test_openai_api_correctly_configured(self):
        """Invariant: Correctly configured openai-api outputs [OK] with provider and model name."""
        os.environ["OPENAI_API_KEY"] = "sk-test-openai-key-valid"
        conf = {"PLAN_PROVIDERS": "openai-api", "OPENAI_MODEL": "gpt-4o", "OPENAI_API_BASE": "https://api.openai.com/v1"}
        res = doctor.check_orchestrator_providers(conf)
        self.assertEqual(len(res["bad"]), 0)
        self.assertTrue(any("[OK]" in c["message"] and "openai-api" in c["message"] and "gpt-4o" in c["message"] for c in res["ok"]))
        assert len(res["bad"]) == 0

    def test_gemini_api_correctly_configured(self):
        """Invariant: Correctly configured gemini-api outputs [OK] with provider and model name."""
        os.environ["GEMINI_API_KEY"] = "gemini-test-key-valid"
        conf = {"REVIEW_PROVIDERS": "gemini-api", "GEMINI_MODEL": "gemini-1.5-pro"}
        res = doctor.check_orchestrator_providers(conf)
        self.assertEqual(len(res["bad"]), 0)
        self.assertTrue(any("[OK]" in c["message"] and "gemini-api" in c["message"] and "gemini-1.5-pro" in c["message"] for c in res["ok"]))
        assert len(res["bad"]) == 0

    def test_openai_api_missing_secret(self):
        """Invariant: openai-api with missing secret emits descriptive [FAIL] with remediation command."""
        conf = {"PLAN_PROVIDERS": "openai-api", "OPENAI_MODEL": "gpt-4o", "OPENAI_API_BASE": "https://api.openai.com/v1"}
        res = doctor.check_orchestrator_providers(conf)
        self.assertGreaterEqual(len(res["bad"]), 1)
        bad_msg = [c["message"] for c in res["bad"]]
        bad_fix = [c.get("fix", "") for c in res["bad"]]
        self.assertTrue(any("[FAIL]" in m and "secret" in m for m in bad_msg))
        self.assertTrue(any("bin/secret-set openai_api_key" in f or "bin/secret-set openai.key" in f for f in bad_fix))
        assert len(res["bad"]) >= 1

    def test_gemini_api_missing_secret(self):
        """Invariant: gemini-api with missing secret emits descriptive [FAIL] with remediation command."""
        conf = {"PLAN_PROVIDERS": "gemini-api", "GEMINI_MODEL": "gemini-1.5-pro"}
        res = doctor.check_orchestrator_providers(conf)
        self.assertGreaterEqual(len(res["bad"]), 1)
        bad_msg = [c["message"] for c in res["bad"]]
        bad_fix = [c.get("fix", "") for c in res["bad"]]
        self.assertTrue(any("[FAIL]" in m and "secret" in m for m in bad_msg))
        self.assertTrue(any("bin/secret-set gemini_api_key" in f or "bin/secret-set gemini.key" in f for f in bad_fix))
        assert len(res["bad"]) >= 1

    def test_openai_api_missing_model(self):
        """Invariant: openai-api with missing OPENAI_MODEL emits descriptive [FAIL] with remediation instruction."""
        os.environ["OPENAI_API_KEY"] = "sk-valid-key"
        conf = {"PLAN_PROVIDERS": "openai-api", "OPENAI_MODEL": "", "OPENAI_API_BASE": "https://api.openai.com/v1"}
        res = doctor.check_orchestrator_providers(conf)
        self.assertGreaterEqual(len(res["bad"]), 1)
        self.assertTrue(any("[FAIL]" in c["message"] and "OPENAI_MODEL" in c["message"] for c in res["bad"]))
        assert any("OPENAI_MODEL" in c["message"] for c in res["bad"])

    def test_gemini_api_missing_model(self):
        """Invariant: gemini-api with missing GEMINI_MODEL emits descriptive [FAIL] with remediation instruction."""
        os.environ["GEMINI_API_KEY"] = "gemini-valid-key"
        conf = {"PLAN_PROVIDERS": "gemini-api", "GEMINI_MODEL": ""}
        res = doctor.check_orchestrator_providers(conf)
        self.assertGreaterEqual(len(res["bad"]), 1)
        self.assertTrue(any("[FAIL]" in c["message"] and "GEMINI_MODEL" in c["message"] for c in res["bad"]))
        assert any("GEMINI_MODEL" in c["message"] for c in res["bad"])

    def test_openai_api_invalid_base_url(self):
        """Invariant: openai-api with invalid OPENAI_API_BASE URL emits descriptive [FAIL] with remediation."""
        os.environ["OPENAI_API_KEY"] = "sk-valid-key"
        conf = {"PLAN_PROVIDERS": "openai-api", "OPENAI_MODEL": "gpt-4o", "OPENAI_API_BASE": "ftp://invalid-url.com"}
        res = doctor.check_orchestrator_providers(conf)
        self.assertGreaterEqual(len(res["bad"]), 1)
        self.assertTrue(any("[FAIL]" in c["message"] and "OPENAI_API_BASE" in c["message"] for c in res["bad"]))
        assert any("invalid OPENAI_API_BASE" in c["message"] for c in res["bad"])

    def test_openai_api_egress_allowlist_warning(self):
        """Invariant: openai-api pointing to a host not in allowlist or local endpoint produces [WARN]."""
        os.environ["OPENAI_API_KEY"] = "sk-valid-key"
        conf = {"PLAN_PROVIDERS": "openai-api", "OPENAI_MODEL": "gpt-4o", "OPENAI_API_BASE": "https://custom-proxy.internal.corp/v1"}
        res = doctor.check_orchestrator_providers(conf)
        self.assertTrue(any("[WARN]" in c["message"] and "custom-proxy.internal.corp" in c["message"] for c in res["warn"]))
        assert any("[WARN]" in c["message"] and "egress allowlist" in c["message"] for c in res["warn"])

    def test_openai_api_local_endpoint_allowed(self):
        """Invariant: openai-api pointing to local endpoint configuration does not warn about egress."""
        os.environ["OPENAI_API_KEY"] = "sk-valid-key"
        conf = {"PLAN_PROVIDERS": "openai-api", "OPENAI_MODEL": "gpt-4o", "OPENAI_API_BASE": "http://192.168.1.100:11434/v1"}
        with patch.object(egress, "local_endpoint", return_value=("192.168.1.100", 11434)):
            res = doctor.check_orchestrator_providers(conf)
        self.assertFalse(any("egress allowlist" in c["message"] for c in res["warn"]))
        assert not any("egress allowlist" in c["message"] for c in res["warn"])

    def test_secret_strings_never_emitted_in_doctor_output(self):
        """Invariant: Doctor checks never print or log raw secret tokens in stdout, stderr, or check objects."""
        canary_openai = "CANARY_OPENAI_SECRET_KEY_987654321"
        canary_gemini = "CANARY_GEMINI_SECRET_KEY_123456789"
        os.environ["OPENAI_API_KEY"] = canary_openai
        os.environ["GEMINI_API_KEY"] = canary_gemini

        doctor.JSON = False
        stdout_trap = io.StringIO()
        with patch("sys.stdout", stdout_trap):
            res = doctor.check_orchestrator_providers({
                "PLAN_PROVIDERS": "openai-api,gemini-api",
                "OPENAI_MODEL": "gpt-4o",
                "GEMINI_MODEL": "gemini-1.5-pro",
            })

        output_text = stdout_trap.getvalue()
        self.assertNotIn(canary_openai, output_text)
        self.assertNotIn(canary_gemini, output_text)
        self.assertIn("[OK]", output_text)

        all_check_text = " ".join(c.get("message", "") + " " + c.get("fix", "") for c in res["checks"])
        self.assertNotIn(canary_openai, all_check_text)
        self.assertNotIn(canary_gemini, all_check_text)
        assert canary_openai not in output_text and canary_gemini not in output_text

    def test_secret_file_in_sandbox_secrets_dir(self):
        """Invariant: Secret keys stored in sandbox secrets directory are recognized."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            orig_sec = sbx.SECRETS
            try:
                sbx.SECRETS = tmp_dir
                key_path = os.path.join(tmp_dir, "openai.key")
                with open(key_path, "w") as f:
                    f.write("sk-secret-from-file-1234")
                os.chmod(key_path, 0o600)

                conf = {"PLAN_PROVIDERS": "openai-api", "OPENAI_MODEL": "gpt-4o", "OPENAI_API_BASE": "https://api.openai.com/v1"}
                res = doctor.check_orchestrator_providers(conf)
                self.assertEqual(len(res["bad"]), 0)
                assert len(res["bad"]) == 0
            finally:
                sbx.SECRETS = orig_sec

    def test_both_providers_configured_together(self):
        """Invariant: Both openai-api and gemini-api are evaluated when listed in providers."""
        os.environ["OPENAI_API_KEY"] = "sk-valid-openai"
        os.environ["GEMINI_API_KEY"] = "valid-gemini-key"
        conf = {
            "PLAN_PROVIDERS": "openai-api",
            "REVIEW_PROVIDERS": "gemini-api",
            "OPENAI_MODEL": "gpt-4o",
            "GEMINI_MODEL": "gemini-1.5-pro",
        }
        res = doctor.check_orchestrator_providers(conf)
        self.assertEqual(len(res["bad"]), 0)
        self.assertGreaterEqual(len(res["ok"]), 2)
        assert len(res["ok"]) >= 2

    def test_doctor_cli_subprocess_never_emits_secrets(self):
        """Invariant: Running bin/doctor via subprocess does not emit secrets in stdout or stderr."""
        canary = "CANARY_CLI_SECRET_TOKEN_4455667788"
        env = dict(os.environ)
        env["OPENAI_API_KEY"] = canary
        env["PLAN_PROVIDERS"] = "openai-api"
        env["OPENAI_MODEL"] = "gpt-4o"
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "bin", "doctor"), "--quick"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        self.assertNotIn(canary, proc.stdout)
        self.assertNotIn(canary, proc.stderr)
        assert canary not in proc.stdout and canary not in proc.stderr


if __name__ == "__main__":
    unittest.main()
