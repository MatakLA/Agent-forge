#!/usr/bin/env python3
"""Tests for OpenAI-compatible and Gemini API orchestrator settings validation.

Public interface: run via python3 tests/test_settings_providers.py or python3 -m unittest tests/test_settings_providers.py.
Invariants:
- settings_apply validates OPENAI_API_BASE for scheme, hostname, traversal, and shell safety.
- settings_apply rejects unknown providers in PLAN_PROVIDERS and REVIEW_PROVIDERS.
- settings_apply requires OPENAI_MODEL when openai-api is used, and GEMINI_MODEL when gemini-api is used.
- settings_apply and settings_get validate and surface OPENCODE_MODEL, QWEN_MODEL, and GOOSE_MODEL.
- settings_apply rejects providers or runtimes whose required egress host is missing from allowlist.
- Sensitive API keys and tokens are never echoed in errors, event logs, or return values.
"""
import atexit
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T_RUNS = tempfile.mkdtemp(prefix="af-test-settings-runs-")
atexit.register(shutil.rmtree, T_RUNS, ignore_errors=True)
os.environ["SANDBOX_TEST"] = "1"
os.environ["SANDBOX_RUNS_DIR"] = T_RUNS
os.environ["LIMITS_FILE"] = os.path.join(ROOT, "etc", "limits.example.conf")
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))

try:
    import egress
    from foreman import ctx
    from foreman.settings import (
        EDITABLE,
        PROVIDERS_ALL,
        settings_apply,
        settings_get,
        validate_url,
        validate_providers,
    )
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Top-level smoke-import check ensuring modules load without error
assert settings_apply is not None
assert EDITABLE is not None
assert PROVIDERS_ALL is not None
assert validate_url is not None
assert validate_providers is not None
assert egress is not None


class TestSettingsProviders(unittest.TestCase):
    """Unit tests for orchestrator provider settings validation in settings_apply."""

    def setUp(self):
        """Set up an isolated temporary environment for settings testing."""
        self._allowlist_patch = patch.object(
            egress,
            "allowlist",
            return_value=[
                "api.anthropic.com",
                "api.openai.com",
                "generativelanguage.googleapis.com",
            ],
        )
        self._allowlist_patch.start()
        self.test_dir = tempfile.mkdtemp(prefix="af-settings-prov-")
        self.etc_dir = os.path.join(self.test_dir, "etc")
        os.makedirs(self.etc_dir, exist_ok=True)
        self.foreman_conf = os.path.join(self.etc_dir, "foreman.conf")
        self.limits_conf = os.path.join(self.etc_dir, "limits.conf")
        with open(self.foreman_conf, "w") as f:
            f.write("# foreman config\nCLAUDE_MODEL=sonnet\nPLAN_PROVIDERS=claude-cli\nREVIEW_PROVIDERS=agy-claude\n")
        with open(self.limits_conf, "w") as f:
            f.write("# limits config\nMAX_PARALLEL=4\n")

        self._orig_root = ctx.ROOT
        self._orig_pdir = ctx.PDIR
        self._orig_c = dict(ctx.C)
        ctx.ROOT = self.test_dir
        ctx.PDIR = ""
        ctx.C.update({
            "PLAN_PROVIDERS": "claude-cli",
            "REVIEW_PROVIDERS": "agy-claude",
            "CLAUDE_MODEL": "sonnet",
            "OPENAI_MODEL": "",
            "GEMINI_MODEL": "",
            "OPENAI_API_BASE": "https://api.openai.com/v1",
            "OPENAI_API_KEY": "",
            "GEMINI_API_KEY": "",
            "OPENCODE_MODEL": "",
            "QWEN_MODEL": "",
            "GOOSE_MODEL": "",
        })

    def tearDown(self):
        """Restore global ctx state and clean up temporary directory."""
        self._allowlist_patch.stop()
        ctx.ROOT = self._orig_root
        ctx.PDIR = self._orig_pdir
        ctx.C.clear()
        ctx.C.update(self._orig_c)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_openai_api_base_valid_urls(self):
        """settings_apply accepts valid http/https URLs and empty string."""
        valid_urls = [
            "",
            "https://api.openai.com/v1",
            "http://localhost:11434/v1",
            "http://192.168.1.100:8000",
            "https://openrouter.ai/api/v1",
            "http://ollama.internal:11434",
            "http://my-host.internal:8080/path/to/api",
        ]
        for url in valid_urls:
            errs = settings_apply({"OPENAI_API_BASE": url})
            self.assertEqual(errs, {}, f"Expected valid url for {url}, got: {errs}")
        assert not errs

    def test_openai_api_base_invalid_scheme(self):
        """settings_apply rejects non-http/https URL schemes with an informative error."""
        invalid_schemes = [
            "ftp://bad.example",
            "file:///etc/passwd",
            "ws://localhost:8000",
            "gopher://old.server",
            "not-a-url",
        ]
        for url in invalid_schemes:
            errs = settings_apply({"OPENAI_API_BASE": url})
            self.assertIn("OPENAI_API_BASE", errs, f"Expected rejection for {url}")
            self.assertIn("scheme", errs["OPENAI_API_BASE"].lower())
        assert "OPENAI_API_BASE" in errs

    def test_openai_api_base_traversal_attempts(self):
        """settings_apply rejects path traversal attempts in OPENAI_API_BASE."""
        traversal_urls = [
            "https://api.openai.com/v1/../../etc/passwd",
            "http://localhost:11434/../v1",
            "http://127.0.0.1/..",
            "http://example.com/api/../",
        ]
        for url in traversal_urls:
            errs = settings_apply({"OPENAI_API_BASE": url})
            self.assertIn("OPENAI_API_BASE", errs, f"Expected traversal rejection for {url}")
            self.assertIn("traversal", errs["OPENAI_API_BASE"].lower())
        assert "OPENAI_API_BASE" in errs

    def test_openai_api_base_command_injection_attempts(self):
        """settings_apply rejects shell metacharacters and injection attempts in OPENAI_API_BASE."""
        injection_urls = [
            "http://localhost:11434/v1; rm -rf /",
            "http://example.com/$(id)",
            "http://example.com/`whoami`",
            "http://example.com/|cat",
            "http://example.com/&calc",
            "http://example.com/>out",
            "http://example.com/\nnewline",
        ]
        for url in injection_urls:
            errs = settings_apply({"OPENAI_API_BASE": url})
            self.assertIn("OPENAI_API_BASE", errs, f"Expected injection rejection for {url}")
            self.assertIn("shell", errs["OPENAI_API_BASE"].lower())
        assert "OPENAI_API_BASE" in errs

    def test_openai_api_base_credentials_and_query_strings(self):
        """settings_apply rejects credentials, query strings, and fragments in OPENAI_API_BASE."""
        disallowed = [
            ("https://user:pass@api.openai.com/v1", "credential"),
            ("https://api.openai.com/v1?token=secret123", "query string"),
            ("https://api.openai.com/v1#hash", "fragment"),
        ]
        for url, keyword in disallowed:
            errs = settings_apply({"OPENAI_API_BASE": url})
            self.assertIn("OPENAI_API_BASE", errs, f"Expected rejection for {url}")
            self.assertIn(keyword, errs["OPENAI_API_BASE"].lower())
        assert "OPENAI_API_BASE" in errs

    def test_openai_api_base_invalid_ports_and_hostnames(self):
        """settings_apply rejects invalid ports and missing hostnames in OPENAI_API_BASE."""
        invalid_urls = [
            ("http://localhost:0", "port"),
            ("http://localhost:70000", "port"),
            ("http://", "hostname"),
            ("https:///v1", "hostname"),
        ]
        for url, keyword in invalid_urls:
            errs = settings_apply({"OPENAI_API_BASE": url})
            self.assertIn("OPENAI_API_BASE", errs, f"Expected rejection for {url}")
            self.assertIn(keyword, errs["OPENAI_API_BASE"].lower())
        assert "OPENAI_API_BASE" in errs

    def test_plan_and_review_providers_valid(self):
        """settings_apply accepts valid comma-separated lists of registered providers."""
        valid_configs = [
            {"PLAN_PROVIDERS": "claude-cli"},
            {"PLAN_PROVIDERS": "claude-cli,openai-api", "OPENAI_MODEL": "gpt-4o"},
            {"REVIEW_PROVIDERS": "agy-claude,gemini-api", "GEMINI_MODEL": "gemini-2.5-pro"},
            {
                "PLAN_PROVIDERS": "openai-api,gemini-api",
                "REVIEW_PROVIDERS": "anthropic-api,agy-gemini",
                "OPENAI_MODEL": "gpt-4o",
                "GEMINI_MODEL": "gemini-2.5-pro",
                "ANTHROPIC_MODEL": "claude-3-7-sonnet",
            },
        ]
        for cfg in valid_configs:
            errs = settings_apply(cfg)
            self.assertEqual(errs, {}, f"Expected valid configuration for {cfg}, got: {errs}")
        assert not errs

    def test_plan_and_review_providers_unknown_rejected(self):
        """settings_apply rejects unknown providers with informative error mentioning allowed list."""
        unknown_configs = [
            {"PLAN_PROVIDERS": "unknown-provider"},
            {"PLAN_PROVIDERS": "claude-cli,unknown-provider"},
            {"REVIEW_PROVIDERS": "claude-cli,bogus-provider"},
        ]
        for cfg in unknown_configs:
            errs = settings_apply(cfg)
            key = "PLAN_PROVIDERS" if "PLAN_PROVIDERS" in cfg else "REVIEW_PROVIDERS"
            self.assertIn(key, errs)
            self.assertIn("unknown provider", errs[key])
            for p in PROVIDERS_ALL:
                self.assertIn(p, errs[key])
        assert "unknown provider" in errs[key]

    def test_plan_and_review_providers_empty_and_malformed(self):
        """settings_apply rejects empty lists or malformed comma entries."""
        malformed = [
            {"PLAN_PROVIDERS": ""},
            {"PLAN_PROVIDERS": "claude-cli,"},
            {"REVIEW_PROVIDERS": "claude-cli,,openai-api"},
            {"REVIEW_PROVIDERS": ",claude-cli"},
        ]
        for cfg in malformed:
            errs = settings_apply(cfg)
            key = "PLAN_PROVIDERS" if "PLAN_PROVIDERS" in cfg else "REVIEW_PROVIDERS"
            self.assertIn(key, errs)
        assert "PLAN_PROVIDERS" in errs or "REVIEW_PROVIDERS" in errs

    def test_missing_model_when_openai_api_selected(self):
        """settings_apply rejects openai-api in plan/review providers when OPENAI_MODEL is blank."""
        ctx.C["OPENAI_MODEL"] = ""
        errs_plan = settings_apply({"PLAN_PROVIDERS": "openai-api"})
        self.assertIn("OPENAI_MODEL", errs_plan)
        self.assertIn("OPENAI_MODEL cannot be blank", errs_plan["OPENAI_MODEL"])

        errs_review = settings_apply({"REVIEW_PROVIDERS": "openai-api"})
        self.assertIn("OPENAI_MODEL", errs_review)

        errs_both = settings_apply({"PLAN_PROVIDERS": "openai-api", "OPENAI_MODEL": ""})
        self.assertIn("OPENAI_MODEL", errs_both)

        # Providing OPENAI_MODEL satisfies the validation
        errs_ok = settings_apply({"PLAN_PROVIDERS": "openai-api", "OPENAI_MODEL": "gpt-4o"})
        self.assertEqual(errs_ok, {})
        assert "OPENAI_MODEL" in errs_plan

    def test_missing_model_when_gemini_api_selected(self):
        """settings_apply rejects gemini-api in plan/review providers when GEMINI_MODEL is blank."""
        ctx.C["GEMINI_MODEL"] = ""
        errs_plan = settings_apply({"PLAN_PROVIDERS": "gemini-api"})
        self.assertIn("GEMINI_MODEL", errs_plan)
        self.assertIn("GEMINI_MODEL cannot be blank", errs_plan["GEMINI_MODEL"])

        errs_review = settings_apply({"REVIEW_PROVIDERS": "gemini-api"})
        self.assertIn("GEMINI_MODEL", errs_review)

        errs_both = settings_apply({"REVIEW_PROVIDERS": "gemini-api", "GEMINI_MODEL": ""})
        self.assertIn("GEMINI_MODEL", errs_both)

        # Providing GEMINI_MODEL satisfies the validation
        errs_ok = settings_apply({"REVIEW_PROVIDERS": "gemini-api", "GEMINI_MODEL": "gemini-2.5-pro"})
        self.assertEqual(errs_ok, {})
        assert "GEMINI_MODEL" in errs_plan

    def test_orchestrator_models_validation(self):
        """OPENAI_MODEL and GEMINI_MODEL validate against standard MODEL_RE and SERVER_MODEL_RE."""
        valid_models = [
            "gpt-4o",
            "gpt-4o-mini",
            "qwen2.5-coder:32b",
            "anthropic/claude-sonnet-4.5",
            "Qwen/Qwen3-32B",
            "gemini-2.5-pro",
            "",
        ]
        for m in valid_models:
            errs = settings_apply({"OPENAI_MODEL": m, "GEMINI_MODEL": m})
            self.assertEqual(errs, {}, f"Expected valid model {m!r}, got: {errs}")

        invalid_models = [
            "invalid model with spaces",
            "model; injection",
            "model\nnewline",
            "$VAR",
            "`whoami`",
        ]
        for m in invalid_models:
            errs = settings_apply({"OPENAI_MODEL": m, "GEMINI_MODEL": m})
            self.assertIn("OPENAI_MODEL", errs)
            self.assertIn("GEMINI_MODEL", errs)
        assert "OPENAI_MODEL" in errs

    def test_api_keys_and_secrets_validation(self):
        """OPENAI_API_KEY and GEMINI_API_KEY accept valid secret names or masked identifiers."""
        valid_secrets = [
            "openai.key",
            "gemini.key",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "sk-proj-****",
            "***",
            "••••••••",
            "",
        ]
        for s in valid_secrets:
            errs = settings_apply({"OPENAI_API_KEY": s, "GEMINI_API_KEY": s})
            self.assertEqual(errs, {}, f"Expected valid secret {s!r}, got: {errs}")

        invalid_secrets = [
            "../secret_file",
            "secret/with/slash",
            "key; rm -rf /",
            "$(whoami)",
            "key with spaces",
            "key\nnewline",
            "key=value",
        ]
        for s in invalid_secrets:
            errs = settings_apply({"OPENAI_API_KEY": s, "GEMINI_API_KEY": s})
            self.assertIn("OPENAI_API_KEY", errs)
            self.assertIn("GEMINI_API_KEY", errs)
            self.assertEqual(errs["OPENAI_API_KEY"], "invalid secret name or masked identifier")
        assert "OPENAI_API_KEY" in errs

    def test_no_secret_leak_in_errors_logs_or_returns(self):
        """API keys and secrets must never be echoed in errors, logs, or settings_get return values."""
        sensitive_raw_token = "sk-super-secret-key-12345"

        # 1. Error message never contains raw secret
        errs = settings_apply({"OPENAI_API_KEY": f"{sensitive_raw_token}; bad"})
        self.assertIn("OPENAI_API_KEY", errs)
        self.assertNotIn(sensitive_raw_token, str(errs))

        # 2. settings_get() masks non-empty secret keys
        ctx.C["OPENAI_API_KEY"] = sensitive_raw_token
        res = settings_get()
        self.assertNotIn(sensitive_raw_token, str(res["values"]))
        self.assertEqual(res["values"]["OPENAI_API_KEY"], "***")

        # 3. Masked identifier preserved in settings_get()
        ctx.C["OPENAI_API_KEY"] = "sk-proj-****"
        res_masked = settings_get()
        self.assertEqual(res_masked["values"]["OPENAI_API_KEY"], "sk-proj-****")
        assert sensitive_raw_token not in str(errs)

    def test_settings_apply_writes_valid_config_using_named_temporary_file(self):
        """settings_apply persists valid configuration to disk using tempfile.NamedTemporaryFile."""
        with tempfile.NamedTemporaryFile(mode="w+", prefix="af-conf-test-", delete=True) as tf:
            target_path = tf.name
            tf.write("# initial config\n")
            tf.flush()

            old_conf_path = ctx.PDIR
            try:
                # Direct write test using settings_apply
                valid_changes = {
                    "OPENAI_API_BASE": "https://api.openai.com/v1",
                    "OPENAI_MODEL": "gpt-4o",
                    "GEMINI_MODEL": "gemini-2.5-pro",
                    "PLAN_PROVIDERS": "openai-api",
                    "REVIEW_PROVIDERS": "gemini-api",
                }
                errs = settings_apply(valid_changes)
                self.assertEqual(errs, {})

                with open(self.foreman_conf) as f:
                    content = f.read()
                self.assertIn("OPENAI_API_BASE=https://api.openai.com/v1", content)
                self.assertIn("OPENAI_MODEL=gpt-4o", content)
                self.assertIn("GEMINI_MODEL=gemini-2.5-pro", content)
                self.assertIn("PLAN_PROVIDERS=openai-api", content)
                self.assertIn("REVIEW_PROVIDERS=gemini-api", content)
            finally:
                ctx.PDIR = old_conf_path
        assert "OPENAI_API_BASE=https://api.openai.com/v1" in content

    def test_opencode_model_settings(self):
        """OPENCODE_MODEL is included in settings_get, accepts valid model strings, and rejects invalid ones."""
        self.assertIn("OPENCODE_MODEL", EDITABLE)
        res = settings_get()
        self.assertIn("OPENCODE_MODEL", res["values"])
        self.assertEqual(res["scopes"].get("OPENCODE_MODEL"), "project")

        valid_models = [
            "gpt-4o",
            "anthropic/claude-sonnet-4.5",
            "qwen2.5-coder:32b",
            "Qwen/Qwen3-32B",
            "",
        ]
        for m in valid_models:
            errs = settings_apply({"OPENCODE_MODEL": m})
            self.assertEqual(errs, {}, f"Expected valid OPENCODE_MODEL {m!r}, got: {errs}")

        invalid_models = [
            "invalid model with spaces",
            "model; injection",
            "model\nnewline",
            "$VAR",
            "`whoami`",
        ]
        for m in invalid_models:
            errs = settings_apply({"OPENCODE_MODEL": m})
            self.assertIn("OPENCODE_MODEL", errs, f"Expected OPENCODE_MODEL error for {m!r}")
            self.assertEqual(errs["OPENCODE_MODEL"], "invalid value")
        assert "OPENCODE_MODEL" in errs

    def test_qwen_model_settings(self):
        """QWEN_MODEL is included in settings_get, accepts valid model strings, and rejects invalid ones."""
        self.assertIn("QWEN_MODEL", EDITABLE)
        res = settings_get()
        self.assertIn("QWEN_MODEL", res["values"])
        self.assertEqual(res["scopes"].get("QWEN_MODEL"), "project")

        valid_models = [
            "qwen-max",
            "qwen-plus",
            "qwen2.5-coder:32b",
            "qwen-turbo",
            "",
        ]
        for m in valid_models:
            errs = settings_apply({"QWEN_MODEL": m})
            self.assertEqual(errs, {}, f"Expected valid QWEN_MODEL {m!r}, got: {errs}")

        invalid_models = [
            "invalid model with spaces",
            "model; injection",
            "model\nnewline",
            "$VAR",
            "`whoami`",
        ]
        for m in invalid_models:
            errs = settings_apply({"QWEN_MODEL": m})
            self.assertIn("QWEN_MODEL", errs, f"Expected QWEN_MODEL error for {m!r}")
            self.assertEqual(errs["QWEN_MODEL"], "invalid value")
        assert "QWEN_MODEL" in errs

    def test_goose_model_settings(self):
        """GOOSE_MODEL is included in settings_get, accepts valid model strings, and rejects invalid ones."""
        self.assertIn("GOOSE_MODEL", EDITABLE)
        res = settings_get()
        self.assertIn("GOOSE_MODEL", res["values"])
        self.assertEqual(res["scopes"].get("GOOSE_MODEL"), "project")

        valid_models = [
            "gpt-4o",
            "qwen2.5-coder:32b",
            "claude-3-7-sonnet",
            "",
        ]
        for m in valid_models:
            errs = settings_apply({"GOOSE_MODEL": m})
            self.assertEqual(errs, {}, f"Expected valid GOOSE_MODEL {m!r}, got: {errs}")

        invalid_models = [
            "invalid model with spaces",
            "model; injection",
            "model\nnewline",
            "$VAR",
            "`whoami`",
        ]
        for m in invalid_models:
            errs = settings_apply({"GOOSE_MODEL": m})
            self.assertIn("GOOSE_MODEL", errs, f"Expected GOOSE_MODEL error for {m!r}")
            self.assertEqual(errs["GOOSE_MODEL"], "invalid value")
        assert "GOOSE_MODEL" in errs

    def test_api_provider_egress_host_missing_rejected(self):
        """Saving an *-api provider whose required egress host is missing is rejected naming the host."""
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]):
            errs = settings_apply({"PLAN_PROVIDERS": "openai-api", "OPENAI_MODEL": "gpt-4o"})
            self.assertIn("PLAN_PROVIDERS", errs)
            self.assertIn("api.openai.com", errs["PLAN_PROVIDERS"])
            self.assertIn("openai-api", errs["PLAN_PROVIDERS"])
            self.assertIn("egress/allowlist.txt", errs["PLAN_PROVIDERS"])

            errs_anthropic = settings_apply({"REVIEW_PROVIDERS": "anthropic-api", "ANTHROPIC_MODEL": "claude-3-7-sonnet"})
            self.assertIn("REVIEW_PROVIDERS", errs_anthropic)
            self.assertIn("api.anthropic.com", errs_anthropic["REVIEW_PROVIDERS"])
            self.assertIn("anthropic-api", errs_anthropic["REVIEW_PROVIDERS"])
            self.assertIn("egress/allowlist.txt", errs_anthropic["REVIEW_PROVIDERS"])

            errs_gemini = settings_apply({"PLAN_PROVIDERS": "gemini-api", "GEMINI_MODEL": "gemini-2.5-pro"})
            self.assertIn("PLAN_PROVIDERS", errs_gemini)
            self.assertIn("generativelanguage.googleapis.com", errs_gemini["PLAN_PROVIDERS"])
            self.assertIn("gemini-api", errs_gemini["PLAN_PROVIDERS"])
            self.assertIn("egress/allowlist.txt", errs_gemini["PLAN_PROVIDERS"])
        assert "PLAN_PROVIDERS" in errs and "api.openai.com" in errs["PLAN_PROVIDERS"]

    def test_api_provider_egress_host_present_accepted(self):
        """Saving an *-api provider whose required egress host is present in allowlist succeeds."""
        with patch.object(egress, "allowlist", return_value=["api.openai.com", "api.anthropic.com", "generativelanguage.googleapis.com"]), \
             patch.object(egress, "local_allowlist", return_value=[]):
            errs = settings_apply({"PLAN_PROVIDERS": "openai-api", "OPENAI_MODEL": "gpt-4o"})
            self.assertEqual(errs, {})

            errs_anthropic = settings_apply({"REVIEW_PROVIDERS": "anthropic-api", "ANTHROPIC_MODEL": "claude-3-7-sonnet"})
            self.assertEqual(errs_anthropic, {})

            errs_gemini = settings_apply({"PLAN_PROVIDERS": "gemini-api", "GEMINI_MODEL": "gemini-2.5-pro"})
            self.assertEqual(errs_gemini, {})
        assert not errs and not errs_anthropic and not errs_gemini

    def test_agy_based_provider_accepted_regardless_of_allowlist(self):
        """Saving an agy-based provider succeeds even when egress allowlist is completely empty."""
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]):
            for agy_prov in ("claude-cli", "agy-claude", "agy-gemini"):
                errs_plan = settings_apply({"PLAN_PROVIDERS": agy_prov})
                self.assertEqual(errs_plan, {}, f"Expected agy provider {agy_prov} to be accepted when allowlist is empty")
                errs_review = settings_apply({"REVIEW_PROVIDERS": agy_prov})
                self.assertEqual(errs_review, {}, f"Expected agy provider {agy_prov} to be accepted when allowlist is empty")
        assert not errs_plan and not errs_review

    def test_provider_custom_api_base_egress_validation(self):
        """Custom OPENAI_API_BASE egress host is validated and rejected when missing or accepted when present."""
        custom_base = "https://custom-proxy.internal:8443/v1"
        overridden_host = "custom-proxy.internal"
        with patch.object(egress, "allowlist", return_value=["api.openai.com"]), \
             patch.object(egress, "local_allowlist", return_value=[]):
            errs = settings_apply({
                "PLAN_PROVIDERS": "openai-api",
                "OPENAI_MODEL": "gpt-4o",
                "OPENAI_API_BASE": custom_base,
            })
            self.assertIn("PLAN_PROVIDERS", errs)
            self.assertIn(overridden_host, errs["PLAN_PROVIDERS"])
            self.assertIn("openai-api", errs["PLAN_PROVIDERS"])

        with patch.object(egress, "allowlist", return_value=[overridden_host]), \
             patch.object(egress, "local_allowlist", return_value=[]):
            errs_ok = settings_apply({
                "PLAN_PROVIDERS": "openai-api",
                "OPENAI_MODEL": "gpt-4o",
                "OPENAI_API_BASE": custom_base,
            })
            self.assertEqual(errs_ok, {})
        assert overridden_host in errs["PLAN_PROVIDERS"] and not errs_ok

    def test_runtime_egress_validation(self):
        """Saving a runtime-selecting setting validates required egress host."""
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]):
            errs_claude = settings_apply({"WORKER_RUNTIME": "claude"})
            self.assertIn("WORKER_RUNTIME", errs_claude)
            self.assertIn("api.anthropic.com", errs_claude["WORKER_RUNTIME"])
            self.assertIn("claude", errs_claude["WORKER_RUNTIME"])
            self.assertIn("egress/allowlist.txt", errs_claude["WORKER_RUNTIME"])

            # agy runtime requires no pinned egress host and is accepted
            errs_agy = settings_apply({"WORKER_RUNTIME": "agy"})
            self.assertEqual(errs_agy, {})

        with patch.object(egress, "allowlist", return_value=["api.anthropic.com"]), \
             patch.object(egress, "local_allowlist", return_value=[]):
            errs_ok = settings_apply({"WORKER_RUNTIME": "claude"})
            self.assertEqual(errs_ok, {})
        assert "WORKER_RUNTIME" in errs_claude and not errs_ok


if __name__ == "__main__":
    unittest.main()
