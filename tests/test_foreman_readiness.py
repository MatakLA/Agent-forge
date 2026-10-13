#!/usr/bin/env python3
"""Unit tests for core runtime/provider readiness check module (bin/lib/foreman/readiness.py).

Public interface:
- Run directly: python3 tests/test_foreman_readiness.py
- Run via unittest: python3 -m unittest tests/test_foreman_readiness.py

Important invariants:
- runtime_items() matches keys of RUNTIMES in order.
- provider_items() matches PROVIDERS_ALL in order.
- all_status() includes every runtime and provider entry.
- State priority when both missing setting and missing egress apply: missing_setting wins.
- Inapplicable settings (e.g. claude-cli, agy) report state 'ok' with empty missing lists.
- Secrets are never leaked into any return value or status dictionary.
- Provider requirements are derived generically from settings.EDITABLE and verified unmocked.
- recommend() ranks ready runtimes before not-ready runtimes and cheapest cost tier first.
- recommend() returns top=null and non-empty message when no runtime is ready.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
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
        # Clear environment variables that might interfere with clean unconfigured state
        for k in (
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "DASHSCOPE_API_KEY",
            "anthropic.key", "openai.key", "gemini.key", "qwen.key", "compat.key",
            "COMPAT_BASE_URL", "OPENAI_API_BASE", "OPENAI_MODEL", "GEMINI_MODEL", "ANTHROPIC_MODEL",
        ):
            os.environ.pop(k, None)

        self.tmp_dir = tempfile.mkdtemp(prefix="af-readiness-test-")
        self.orig_secrets = getattr(sbx, "SECRETS", "")
        sbx.SECRETS = self.tmp_dir

    def tearDown(self):
        """Restore environment and directories after test completion."""
        sbx.SECRETS = self.orig_secrets
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
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

    def test_real_registry_provider_claude_cli(self):
        """Invariant: claude-cli provider checked against real registry without mocks reports state ok."""
        st = readiness.status_of("provider", "claude-cli")
        self.assertEqual(st["state"], "ok")
        self.assertEqual(st["missing_settings"], [])
        self.assertEqual(st["missing_egress"], [])
        assert st["state"] == "ok" and st["missing_settings"] == [] and st["missing_egress"] == []

    def test_real_registry_provider_agy_claude(self):
        """Invariant: agy-claude provider checked against real registry without mocks reports state ok."""
        st = readiness.status_of("provider", "agy-claude")
        self.assertEqual(st["state"], "ok")
        self.assertEqual(st["missing_settings"], [])
        self.assertEqual(st["missing_egress"], [])
        assert st["state"] == "ok" and st["missing_settings"] == [] and st["missing_egress"] == []

    def test_real_registry_provider_agy_gemini(self):
        """Invariant: agy-gemini provider checked against real registry without mocks reports state ok."""
        st = readiness.status_of("provider", "agy-gemini")
        self.assertEqual(st["state"], "ok")
        self.assertEqual(st["missing_settings"], [])
        self.assertEqual(st["missing_egress"], [])
        assert st["state"] == "ok" and st["missing_settings"] == [] and st["missing_egress"] == []

    def test_real_registry_provider_openai_api(self):
        """Invariant: openai-api derives OPENAI_MODEL, OPENAI_API_KEY, OPENAI_API_BASE from real registry."""
        # 1. Unconfigured: missing_setting with both OPENAI_MODEL and OPENAI_API_KEY
        st_unconf = readiness.status_of("provider", "openai-api")
        self.assertEqual(st_unconf["state"], "missing_setting")
        self.assertIn("OPENAI_MODEL", st_unconf["missing_settings"])
        self.assertIn("OPENAI_API_KEY", st_unconf["missing_settings"])
        assert "OPENAI_MODEL" in st_unconf["missing_settings"] and "OPENAI_API_KEY" in st_unconf["missing_settings"]

        # 2. Configured credentials: host api.openai.com is checked against egress allowlist
        os.environ["OPENAI_MODEL"] = "gpt-4o"
        os.environ["OPENAI_API_KEY"] = "sk-test-openai-key"
        st_conf = readiness.status_of("provider", "openai-api")
        self.assertEqual(st_conf["missing_settings"], [])
        self.assertEqual(st_conf["state"], "missing_egress")
        self.assertIn("api.openai.com", st_conf["missing_egress"])
        assert st_conf["state"] == "missing_egress" and "api.openai.com" in st_conf["missing_egress"]

    def test_real_registry_provider_gemini_api(self):
        """Invariant: gemini-api derives GEMINI_MODEL and GEMINI_API_KEY from real registry without mocks."""
        # 1. Unconfigured: missing_setting with both GEMINI_MODEL and GEMINI_API_KEY
        st_unconf = readiness.status_of("provider", "gemini-api")
        self.assertEqual(st_unconf["state"], "missing_setting")
        self.assertIn("GEMINI_MODEL", st_unconf["missing_settings"])
        self.assertIn("GEMINI_API_KEY", st_unconf["missing_settings"])
        assert "GEMINI_MODEL" in st_unconf["missing_settings"] and "GEMINI_API_KEY" in st_unconf["missing_settings"]

        # 2. Fully configured: reports state ok
        os.environ["GEMINI_MODEL"] = "gemini-2.5-pro"
        os.environ["GEMINI_API_KEY"] = "gemini-test-secret-key"
        st_conf = readiness.status_of("provider", "gemini-api")
        self.assertEqual(st_conf["state"], "ok")
        self.assertEqual(st_conf["missing_settings"], [])
        self.assertEqual(st_conf["missing_egress"], [])
        assert st_conf["state"] == "ok" and st_conf["missing_settings"] == []

    def test_real_registry_provider_anthropic_api(self):
        """Invariant: anthropic-api derives ANTHROPIC_MODEL and ANTHROPIC_API_KEY from real registry without mocks."""
        # 1. Unconfigured: missing_setting with ANTHROPIC_MODEL and ANTHROPIC_API_KEY
        st_unconf = readiness.status_of("provider", "anthropic-api")
        self.assertEqual(st_unconf["state"], "missing_setting")
        self.assertIn("ANTHROPIC_MODEL", st_unconf["missing_settings"])
        self.assertIn("ANTHROPIC_API_KEY", st_unconf["missing_settings"])
        assert "ANTHROPIC_MODEL" in st_unconf["missing_settings"] and "ANTHROPIC_API_KEY" in st_unconf["missing_settings"]

        # 2. Fully configured: reports state ok
        os.environ["ANTHROPIC_MODEL"] = "claude-3-7-sonnet"
        os.environ["ANTHROPIC_API_KEY"] = "ant-test-secret-key"
        st_conf = readiness.status_of("provider", "anthropic-api")
        self.assertEqual(st_conf["state"], "ok")
        self.assertEqual(st_conf["missing_settings"], [])
        self.assertEqual(st_conf["missing_egress"], [])
        assert st_conf["state"] == "ok" and st_conf["missing_settings"] == []

    def test_provider_secret_from_sbx_secrets_dir(self):
        """Invariant: Secret present as file in sbx.SECRETS is recognized without mocking."""
        key_file = os.path.join(self.tmp_dir, "OPENAI_API_KEY")
        with open(key_file, "w") as f:
            f.write("sk-real-file-key")
        os.environ["OPENAI_MODEL"] = "gpt-4o"
        st = readiness.status_of("provider", "openai-api")
        self.assertNotIn("OPENAI_API_KEY", st["missing_settings"])
        self.assertNotIn("OPENAI_MODEL", st["missing_settings"])
        assert "OPENAI_API_KEY" not in st["missing_settings"]

    def test_provider_invalid_base_url_reports_missing_setting(self):
        """Invariant: An invalid base URL for a provider reports the base key in missing_settings."""
        os.environ["OPENAI_MODEL"] = "gpt-4o"
        os.environ["OPENAI_API_KEY"] = "sk-valid-key"
        os.environ["OPENAI_API_BASE"] = "invalid url with spaces"
        st = readiness.status_of("provider", "openai-api")
        self.assertEqual(st["state"], "missing_setting")
        self.assertIn("OPENAI_API_BASE", st["missing_settings"])
        assert "OPENAI_API_BASE" in st["missing_settings"]

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

    def test_recommend_smoke_and_contract_shape(self):
        """Invariant: recommend() returns the exact documented {top, ranked, message} shape."""
        self.assertTrue(callable(readiness.recommend))
        res = readiness.recommend()
        self.assertIsInstance(res, dict)
        self.assertEqual(set(res.keys()), {"top", "ranked", "message"})
        self.assertIsInstance(res["ranked"], list)
        self.assertEqual(len(res["ranked"]), len(readiness.runtime_items()))
        self.assertIsInstance(res["message"], str)
        for entry in res["ranked"]:
            self.assertEqual(set(entry.keys()), {"name", "ready", "cost_tier", "note"})
            self.assertIsInstance(entry["name"], str)
            self.assertIsInstance(entry["ready"], bool)
            self.assertIsInstance(entry["cost_tier"], str)
            self.assertIsInstance(entry["note"], str)
        if res["top"] is not None:
            self.assertEqual(set(res["top"].keys()), {"name", "cost_tier", "reason"})
            self.assertIsInstance(res["top"]["name"], str)
            self.assertIsInstance(res["top"]["cost_tier"], str)
            self.assertIsInstance(res["top"]["reason"], str)
            self.assertEqual(res["message"], "")
        else:
            self.assertTrue(len(res["message"].strip()) > 0)

    def test_recommend_case_a_multiple_ready_ranks_cheaper_tier_first_and_top(self):
        """Case (a): Multiple runtimes are ready with different cost tiers -> cheaper tier ranked first and is top."""
        # Setup: codex (quota), claude (metered), compat (metered) are ready.
        # Codex appears AFTER claude in runtime_items(), but because codex has tier 'quota' (cheaper),
        # codex must rank before claude and be top.
        def fake_status(kind, name):
            if name in ("claude", "codex", "compat"):
                return {"state": "ok", "detail": f"Runtime '{name}' is ready.", "missing_settings": [], "missing_egress": []}
            return {"state": "missing_setting", "detail": f"Runtime '{name}' is missing setting.", "missing_settings": ["key"], "missing_egress": []}

        def fake_cost_model(name):
            if name == "codex":
                return {"kind": "quota", "detail": "quota subscription"}
            if name in ("claude", "compat"):
                return {"kind": "metered", "detail": "billed per token"}
            return {"kind": "unknown", "detail": "unknown pricing"}

        with patch.object(readiness, "status_of", side_effect=fake_status), \
             patch.object(readiness, "cost_model", side_effect=fake_cost_model):
            res = readiness.recommend()

        self.assertIsNotNone(res["top"])
        self.assertEqual(res["top"]["name"], "codex")
        self.assertEqual(res["top"]["cost_tier"], "quota")
        self.assertEqual(res["top"]["reason"], "Runtime 'codex' is ready.")
        self.assertEqual(res["message"], "")

        # ranked[0] must be codex
        self.assertEqual(res["ranked"][0]["name"], "codex")
        self.assertEqual(res["ranked"][0]["cost_tier"], "quota")
        self.assertTrue(res["ranked"][0]["ready"])

        # claude and compat come next as metered (cheaper than unknown, but after quota)
        ranked_ready_names = [r["name"] for r in res["ranked"] if r["ready"]]
        self.assertEqual(ranked_ready_names, ["codex", "claude", "compat"])

    def test_recommend_case_b_unknown_tier_ranks_after_known_cheap_tier(self):
        """Case (b): Ready runtime's tier is 'unknown' and another ready runtime has known cheap tier -> unknown ranks after."""
        # opencode is 'unknown' and ready; claude is 'metered' and ready; agy is 'quota' and ready.
        # Priority order must be quota -> metered -> unknown.
        def fake_status(kind, name):
            if name in ("opencode", "claude", "agy"):
                return {"state": "ok", "detail": f"Runtime '{name}' is ready.", "missing_settings": [], "missing_egress": []}
            return {"state": "missing_setting", "detail": f"Runtime '{name}' is missing setting.", "missing_settings": ["k"], "missing_egress": []}

        def fake_cost_model(name):
            if name == "agy":
                return {"kind": "quota", "detail": "quota"}
            if name == "claude":
                return {"kind": "metered", "detail": "metered"}
            if name == "opencode":
                return {"kind": "unknown", "detail": "unknown"}
            return {"kind": "unknown", "detail": "other"}

        with patch.object(readiness, "status_of", side_effect=fake_status), \
             patch.object(readiness, "cost_model", side_effect=fake_cost_model):
            res = readiness.recommend()

        ready_names = [r["name"] for r in res["ranked"] if r["ready"]]
        self.assertEqual(ready_names, ["agy", "claude", "opencode"])

        # Even if only opencode (unknown) and claude (metered) are ready
        def fake_status_two(kind, name):
            if name in ("opencode", "claude"):
                return {"state": "ok", "detail": f"Runtime '{name}' is ready.", "missing_settings": [], "missing_egress": []}
            return {"state": "missing_setting", "detail": f"Runtime '{name}' not ready.", "missing_settings": ["k"], "missing_egress": []}

        with patch.object(readiness, "status_of", side_effect=fake_status_two), \
             patch.object(readiness, "cost_model", side_effect=fake_cost_model):
            res2 = readiness.recommend()

        ready_names_2 = [r["name"] for r in res2["ranked"] if r["ready"]]
        self.assertEqual(ready_names_2, ["claude", "opencode"])
        self.assertEqual(res2["top"]["name"], "claude")
        self.assertEqual(res2["top"]["cost_tier"], "metered")

    def test_recommend_case_c_no_runtime_ready_top_null_and_message_non_empty(self):
        """Case (c): No runtime is ready -> top is null and message is non-empty."""
        def fake_status(kind, name):
            return {"state": "missing_setting", "detail": f"Runtime '{name}' is missing required setting(s): secret.key.", "missing_settings": ["secret.key"], "missing_egress": []}

        with patch.object(readiness, "status_of", side_effect=fake_status):
            res = readiness.recommend()

        self.assertIsNone(res["top"])
        self.assertIsInstance(res["message"], str)
        self.assertTrue(len(res["message"].strip()) > 0)
        self.assertEqual(len(res["ranked"]), len(readiness.runtime_items()))
        self.assertTrue(all(not r["ready"] for r in res["ranked"]))
        # In runtime_items order when all are not ready
        self.assertEqual([r["name"] for r in res["ranked"]], readiness.runtime_items())
        # Notes must reuse status_of's own detail
        for r in res["ranked"]:
            self.assertEqual(r["note"], f"Runtime '{r['name']}' is missing required setting(s): secret.key.")

    def test_recommend_case_d_not_ready_never_appears_before_ready(self):
        """Case (d): A not-ready runtime never appears before a ready one regardless of tier."""
        # agy is tier 'quota' (cheapest), but NOT ready.
        # claude is tier 'metered' and IS ready.
        # opencode is tier 'unknown' and IS ready.
        def fake_status(kind, name):
            if name in ("claude", "opencode"):
                return {"state": "ok", "detail": f"Runtime '{name}' is ready.", "missing_settings": [], "missing_egress": []}
            return {"state": "missing_setting", "detail": f"Runtime '{name}' is missing setting.", "missing_settings": ["key"], "missing_egress": []}

        def fake_cost_model(name):
            if name == "agy":
                return {"kind": "quota", "detail": "quota"}
            if name == "claude":
                return {"kind": "metered", "detail": "metered"}
            if name == "opencode":
                return {"kind": "unknown", "detail": "unknown"}
            return {"kind": "metered", "detail": "metered"}

        with patch.object(readiness, "status_of", side_effect=fake_status), \
             patch.object(readiness, "cost_model", side_effect=fake_cost_model):
            res = readiness.recommend()

        self.assertIsNotNone(res["top"])
        self.assertEqual(res["top"]["name"], "claude")
        self.assertEqual(res["top"]["cost_tier"], "metered")

        # Every ready entry must appear before every not-ready entry
        ready_indices = [i for i, r in enumerate(res["ranked"]) if r["ready"]]
        not_ready_indices = [i for i, r in enumerate(res["ranked"]) if not r["ready"]]
        self.assertTrue(len(ready_indices) > 0)
        self.assertTrue(len(not_ready_indices) > 0)
        self.assertLess(max(ready_indices), min(not_ready_indices))

        # agy is not-ready, so index of agy must be strictly greater than claude and opencode
        names = [r["name"] for r in res["ranked"]]
        idx_claude = names.index("claude")
        idx_opencode = names.index("opencode")
        idx_agy = names.index("agy")
        self.assertLess(idx_claude, idx_agy)
        self.assertLess(idx_opencode, idx_agy)

    def test_cli_readiness_recommend_subcommand(self):
        """Invariant: bin/foreman readiness-recommend prints valid JSON matching recommend()."""
        foreman_bin = os.path.join(ROOT, "bin", "foreman")
        proc = subprocess.run([sys.executable, foreman_bin, "readiness-recommend"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Command failed: {proc.stderr}")
        data = json.loads(proc.stdout)
        self.assertEqual(set(data.keys()), {"top", "ranked", "message"})
        self.assertEqual(data, readiness.recommend())


if __name__ == "__main__":
    unittest.main()
