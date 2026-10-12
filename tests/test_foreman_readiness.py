#!/usr/bin/env python3
"""Unit tests for core runtime/provider readiness check module (bin/lib/foreman/readiness.py).

Public interface:
- Run directly: python3 tests/test_foreman_readiness.py
- Run via unittest: python3 -m unittest tests/test_foreman_readiness.py

Invariants:
- runtime_items() matches keys of RUNTIMES in order.
- provider_items() matches PROVIDERS_ALL in order.
- all_status() includes every runtime and provider entry.
- State priority when both missing setting and missing egress apply: missing_setting wins.
- Inapplicable settings (e.g. claude-cli, agy) report state 'ok' with empty missing lists.
- Secrets are never leaked into any return value or status dictionary.
"""
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))

# Top-level smoke-import check to fail loudly if import breaks
try:
    import egress
    import providers
    import runtimes
    import sbx
    from foreman import ctx, readiness, settings
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)


class TestForemanReadiness(unittest.TestCase):
    """Test suite verifying runtime and provider readiness checks."""

    def setUp(self):
        """Prepare clean test environment and state for each test method."""
        self.orig_env = dict(os.environ)
        # Clear common environment variables that affect checks
        for k in (
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "DASHSCOPE_API_KEY",
            "anthropic.key", "openai.key", "gemini.key", "qwen.key", "compat.key",
            "COMPAT_BASE_URL", "OPENAI_API_BASE", "OPENAI_MODEL", "GEMINI_MODEL", "ANTHROPIC_MODEL",
        ):
            os.environ.pop(k, None)

    def tearDown(self):
        """Restore environment after test completion."""
        os.environ.clear()
        os.environ.update(self.orig_env)

    def test_smoke_exports_exist_and_callable(self):
        """Invariant: Public interface functions exist and are callable."""
        self.assertTrue(callable(readiness.runtime_items))
        self.assertTrue(callable(readiness.provider_items))
        self.assertTrue(callable(readiness.status_of))
        self.assertTrue(callable(readiness.all_status))
        assert callable(readiness.runtime_items) and callable(readiness.provider_items)

    def test_runtime_items_matches_runtimes_registry(self):
        """Invariant: runtime_items() returns all RUNTIMES keys in exact dict order."""
        items = readiness.runtime_items()
        expected = list(runtimes.RUNTIMES.keys())
        self.assertEqual(items, expected)
        assert items == expected

    def test_provider_items_matches_providers_all(self):
        """Invariant: provider_items() returns all PROVIDERS_ALL entries in exact order."""
        items = readiness.provider_items()
        expected = list(settings.PROVIDERS_ALL)
        self.assertEqual(items, expected)
        assert items == expected

    def test_runtime_agy_always_ok(self):
        """Invariant: Plain CLI runtime (agy) requires no keys or egress and is ok."""
        st = readiness.status_of("runtime", "agy")
        self.assertEqual(st["state"], "ok")
        self.assertEqual(st["missing_settings"], [])
        self.assertEqual(st["missing_egress"], [])
        assert st["state"] == "ok" and st["missing_settings"] == [] and st["missing_egress"] == []

    def test_runtime_with_everything_set_is_ok(self):
        """Invariant: A runtime with secret present and egress allowed reports state ok."""
        with patch.object(readiness, "_has_secret", return_value=True), \
             patch.object(readiness, "_allowed_egress_hosts", return_value={"api.anthropic.com"}):
            st = readiness.status_of("runtime", "claude")
        self.assertEqual(st["state"], "ok")
        self.assertEqual(st["missing_settings"], [])
        self.assertEqual(st["missing_egress"], [])
        assert st["state"] == "ok" and st["missing_settings"] == [] and st["missing_egress"] == []

    def test_runtime_missing_secret_is_missing_setting(self):
        """Invariant: A runtime missing its secret reports state missing_setting."""
        with patch.object(readiness, "_has_secret", return_value=False), \
             patch.object(readiness, "_allowed_egress_hosts", return_value={"api.anthropic.com"}):
            st = readiness.status_of("runtime", "claude")
        self.assertEqual(st["state"], "missing_setting")
        self.assertIn("anthropic.key", st["missing_settings"])
        self.assertEqual(st["missing_egress"], [])
        assert st["state"] == "missing_setting" and "anthropic.key" in st["missing_settings"]

    def test_runtime_missing_egress_host_is_missing_egress(self):
        """Invariant: A runtime with secret present but host absent from allowlist reports missing_egress."""
        with patch.object(readiness, "_has_secret", return_value=True), \
             patch.object(readiness, "_allowed_egress_hosts", return_value=set()), \
             patch.object(readiness, "_local_endpoint_host", return_value=""):
            st = readiness.status_of("runtime", "claude")
        self.assertEqual(st["state"], "missing_egress")
        self.assertEqual(st["missing_settings"], [])
        self.assertIn("api.anthropic.com", st["missing_egress"])
        assert st["state"] == "missing_egress" and "api.anthropic.com" in st["missing_egress"]

    def test_runtime_missing_both_setting_and_egress_prioritizes_missing_setting(self):
        """Invariant: When both missing_setting and missing_egress apply, missing_setting wins and both are listed."""
        with patch.object(readiness, "_has_secret", return_value=False), \
             patch.object(readiness, "_allowed_egress_hosts", return_value=set()), \
             patch.object(readiness, "_local_endpoint_host", return_value=""):
            st = readiness.status_of("runtime", "claude")
        self.assertEqual(st["state"], "missing_setting")
        self.assertIn("anthropic.key", st["missing_settings"])
        self.assertIn("api.anthropic.com", st["missing_egress"])
        self.assertIn("anthropic.key", st["detail"])
        self.assertIn("api.anthropic.com", st["detail"])
        assert st["state"] == "missing_setting" and len(st["missing_settings"]) > 0 and len(st["missing_egress"]) > 0

    def test_endpoint_runtime_missing_compat_base_url(self):
        """Invariant: An endpoint runtime (compat, aider, opencode, goose) missing COMPAT_BASE_URL reports missing_setting."""
        results = {}
        with patch.object(readiness, "_get_compat_base_url", return_value=""):
            for rt in ("compat", "aider", "opencode", "goose"):
                results[rt] = readiness.status_of("runtime", rt)
        for rt, st in results.items():
            self.assertEqual(st["state"], "missing_setting")
            self.assertIn("COMPAT_BASE_URL", st["missing_settings"])
        assert all(results[rt]["state"] == "missing_setting" and "COMPAT_BASE_URL" in results[rt]["missing_settings"] for rt in ("compat", "aider", "opencode", "goose"))

    def test_endpoint_runtime_with_local_endpoint_allowed(self):
        """Invariant: An endpoint runtime with COMPAT_BASE_URL allowed by local_endpoint reports ok."""
        with patch.object(readiness, "_get_compat_base_url", return_value="http://192.168.1.50:11434/v1"), \
             patch.object(readiness, "_allowed_egress_hosts", return_value=set()), \
             patch.object(readiness, "_local_endpoint_host", return_value="192.168.1.50"):
            st = readiness.status_of("runtime", "compat")
        self.assertEqual(st["state"], "ok")
        self.assertEqual(st["missing_settings"], [])
        self.assertEqual(st["missing_egress"], [])
        assert st["state"] == "ok" and st["missing_settings"] == []

    def test_subscription_providers_always_ok(self):
        """Invariant: Inapplicable orchestrator providers (claude-cli, agy-claude, agy-gemini) report state ok."""
        results = {}
        for prov in ("claude-cli", "agy-claude", "agy-gemini"):
            results[prov] = readiness.status_of("provider", prov)
            self.assertEqual(results[prov]["state"], "ok")
            self.assertEqual(results[prov]["missing_settings"], [])
            self.assertEqual(results[prov]["missing_egress"], [])
        assert all(results[p]["state"] == "ok" and results[p]["missing_settings"] == [] for p in ("claude-cli", "agy-claude", "agy-gemini"))

    def test_openai_api_provider_missing_model_and_key(self):
        """Invariant: openai-api missing model and key reports missing_setting with both in missing_settings."""
        with patch.object(readiness, "_get_conf_value", return_value=""), \
             patch.object(readiness, "_has_secret", return_value=False):
            st = readiness.status_of("provider", "openai-api")
        self.assertEqual(st["state"], "missing_setting")
        self.assertIn("OPENAI_MODEL", st["missing_settings"])
        self.assertIn("OPENAI_API_KEY", st["missing_settings"])
        assert st["state"] == "missing_setting" and "OPENAI_MODEL" in st["missing_settings"]

    def test_openai_api_provider_configured_but_missing_egress(self):
        """Invariant: openai-api with model and key configured but host absent from egress reports missing_egress."""
        def fake_conf(k):
            return "gpt-4o" if k == "OPENAI_MODEL" else ("https://api.openai.com/v1" if k == "OPENAI_API_BASE" else "")

        with patch.object(readiness, "_get_conf_value", side_effect=fake_conf), \
             patch.object(readiness, "_has_secret", return_value=True), \
             patch.object(readiness, "_allowed_egress_hosts", return_value=set()), \
             patch.object(readiness, "_local_endpoint_host", return_value=""):
            st = readiness.status_of("provider", "openai-api")
        self.assertEqual(st["state"], "missing_egress")
        self.assertEqual(st["missing_settings"], [])
        self.assertIn("api.openai.com", st["missing_egress"])
        assert st["state"] == "missing_egress" and "api.openai.com" in st["missing_egress"]

    def test_gemini_api_provider_configured_is_ok(self):
        """Invariant: gemini-api configured with model and key reports ok."""
        with patch.object(readiness, "_get_conf_value", return_value="gemini-1.5-pro"), \
             patch.object(readiness, "_has_secret", return_value=True):
            st = readiness.status_of("provider", "gemini-api")
        self.assertEqual(st["state"], "ok")
        self.assertEqual(st["missing_settings"], [])
        self.assertEqual(st["missing_egress"], [])
        assert st["state"] == "ok" and st["missing_settings"] == []

    def test_anthropic_api_provider_configured_is_ok(self):
        """Invariant: anthropic-api configured with model and key reports ok."""
        with patch.object(readiness, "_get_conf_value", return_value="claude-3-5-sonnet"), \
             patch.object(readiness, "_has_secret", return_value=True):
            st = readiness.status_of("provider", "anthropic-api")
        self.assertEqual(st["state"], "ok")
        self.assertEqual(st["missing_settings"], [])
        self.assertEqual(st["missing_egress"], [])
        assert st["state"] == "ok" and st["missing_settings"] == []

    def test_qwen_runtime_readiness(self):
        """Invariant: qwen-code runtime checks qwen.key presence and DashScope egress."""
        with patch.object(readiness, "_has_secret", return_value=True), \
             patch.object(readiness, "_allowed_egress_hosts", return_value={"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"}):
            st_ok = readiness.status_of("runtime", "qwen-code")
        self.assertEqual(st_ok["state"], "ok")

        with patch.object(readiness, "_has_secret", return_value=False), \
             patch.object(readiness, "_allowed_egress_hosts", return_value={"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"}):
            st_missing = readiness.status_of("runtime", "qwen-code")
        self.assertEqual(st_missing["state"], "missing_setting")
        self.assertIn("qwen.key", st_missing["missing_settings"])
        assert st_ok["state"] == "ok" and st_missing["state"] == "missing_setting"

    def test_conf_and_secrets_via_ctx_c(self):
        """Invariant: Settings and secrets loaded into ctx.C are properly resolved."""
        fake_c = {
            "OPENAI_MODEL": "gpt-4o",
            "OPENAI_API_KEY": "sk-test-key",
            "OPENAI_API_BASE": "https://api.openai.com/v1",
        }
        with patch.object(ctx, "C", fake_c), \
             patch.object(readiness, "_allowed_egress_hosts", return_value={"api.openai.com"}):
            st = readiness.status_of("provider", "openai-api")
        self.assertEqual(st["state"], "ok")
        assert st["state"] == "ok" and st["missing_settings"] == []

    def test_all_status_contains_every_runtime_and_provider(self):
        """Invariant: all_status() includes an entry for every runtime and provider."""
        all_st = readiness.all_status()
        self.assertIn("runtimes", all_st)
        self.assertIn("providers", all_st)
        self.assertEqual(set(all_st["runtimes"].keys()), set(readiness.runtime_items()))
        self.assertEqual(set(all_st["providers"].keys()), set(readiness.provider_items()))
        for rt, st in all_st["runtimes"].items():
            self.assertIn(st["state"], ("ok", "missing_setting", "missing_egress"))
            self.assertTrue(isinstance(st["detail"], str) and len(st["detail"]) > 0)
        for pr, st in all_st["providers"].items():
            self.assertIn(st["state"], ("ok", "missing_setting", "missing_egress"))
            self.assertTrue(isinstance(st["detail"], str) and len(st["detail"]) > 0)
        assert set(all_st["runtimes"].keys()) == set(readiness.runtime_items())

    def test_secret_value_never_appears_in_status_dicts(self):
        """Invariant: A secret value substring is never exposed in any status dict or all_status string."""
        canary = "SUPER_SECRET_CANARY_VALUE_XYZ_9876543210"
        os.environ["OPENAI_API_KEY"] = canary
        os.environ["ANTHROPIC_API_KEY"] = canary
        os.environ["GEMINI_API_KEY"] = canary
        os.environ["DASHSCOPE_API_KEY"] = canary
        os.environ["anthropic.key"] = canary
        all_st = readiness.all_status()
        all_st_str = str(all_st)
        self.assertNotIn(canary, all_st_str)
        for rt in readiness.runtime_items():
            st = readiness.status_of("runtime", rt)
            self.assertNotIn(canary, str(st))
        for pr in readiness.provider_items():
            st = readiness.status_of("provider", pr)
            self.assertNotIn(canary, str(st))
        assert canary not in all_st_str

    def test_invalid_kind_raises_value_error(self):
        """Invariant: Calling status_of with unknown kind raises ValueError."""
        with self.assertRaises(ValueError) as ctx_err:
            readiness.status_of("invalid_kind", "agy")
        self.assertIn("unknown kind", str(ctx_err.exception))
        assert "unknown kind" in str(ctx_err.exception)

    def test_unknown_runtime_or_provider_raises_value_error(self):
        """Invariant: Calling status_of with unknown name raises ValueError."""
        with self.assertRaises(ValueError) as ctx_rt:
            readiness.status_of("runtime", "unknown_runtime_xyz")
        self.assertIn("unknown runtime", str(ctx_rt.exception))
        with self.assertRaises(ValueError) as ctx_pr:
            readiness.status_of("provider", "unknown_provider_xyz")
        self.assertIn("unknown provider", str(ctx_pr.exception))
        assert "unknown runtime" in str(ctx_rt.exception) and "unknown provider" in str(ctx_pr.exception)


if __name__ == "__main__":
    unittest.main()
