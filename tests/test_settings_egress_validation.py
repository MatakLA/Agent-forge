#!/usr/bin/env python3
"""Regression tests: dashboard settings save rejects a provider with a missing egress host.

Public interface:
- Run directly: python3 tests/test_settings_egress_validation.py
- Run via unittest: python3 -m unittest tests/test_settings_egress_validation.py

Important invariants:
- Covers every provider in bin/lib/foreman/settings.py PROVIDERS_ALL.
- settings_apply rejects provider changes when required egress hosts are absent from allowlist.
- Rejection error names both the provider and the missing egress host, and change is NOT written to disk.
- Positive-case tests verify that settings_apply succeeds and persists to disk when required hosts are present.
- Uses isolated temp files/dirs (monkeypatching egress module constants); never modifies repo egress/allowlist.txt.
- Every test method contains at least one bare top-level assert statement.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))
sys.path.insert(0, ROOT)

# Top-level smoke-import check: prints traceback and exits non-zero on failure
try:
    import egress
    import providers
    from foreman import ctx
    from foreman.settings import PROVIDERS_ALL, conf_path, settings_apply
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)


class TestSettingsEgressValidation(unittest.TestCase):
    """Regression tests for orchestrator provider egress allowlist validation in settings_apply."""

    def setUp(self):
        """Prepare isolated environment, temp config directories, and clean egress allowlist state."""
        self.test_dir = tempfile.mkdtemp(prefix="af-test-settings-egress-")
        self.etc_dir = os.path.join(self.test_dir, "etc")
        os.makedirs(self.etc_dir, exist_ok=True)

        self.foreman_conf = os.path.join(self.etc_dir, "foreman.conf")
        with open(self.foreman_conf, "w") as f:
            f.write("# foreman config\nCLAUDE_MODEL=sonnet\nPLAN_PROVIDERS=claude-cli\nREVIEW_PROVIDERS=agy-claude\n")

        self.limits_conf = os.path.join(self.etc_dir, "limits.conf")
        with open(self.limits_conf, "w") as f:
            f.write("# limits config\nMAX_PARALLEL=4\n")

        self._orig_root = ctx.ROOT
        self._orig_pdir = ctx.PDIR
        self._orig_c = dict(ctx.C)
        ctx.ROOT = self.test_dir
        ctx.PDIR = ""
        ctx.C.clear()
        ctx.C.update({
            "PLAN_PROVIDERS": "claude-cli",
            "REVIEW_PROVIDERS": "agy-claude",
            "CLAUDE_MODEL": "sonnet",
            "OPENAI_MODEL": "gpt-4o",
            "GEMINI_MODEL": "gemini-2.5-pro",
            "ANTHROPIC_MODEL": "claude-3-7-sonnet",
            "OPENAI_API_BASE": "https://api.openai.com/v1",
            "OPENAI_API_KEY": "",
            "GEMINI_API_KEY": "",
        })

        # Temporary files for isolated egress allowlists
        self.tmp_allowlist = tempfile.NamedTemporaryFile("w+", delete=False)
        self.tmp_local_allowlist = tempfile.NamedTemporaryFile("w+", delete=False)
        self.tmp_allowlist.close()
        self.tmp_local_allowlist.close()

        self.patchers = [
            patch.object(egress, "ALLOWLIST", self.tmp_allowlist.name),
            patch.object(egress, "ALLOWLIST_LOCAL", self.tmp_local_allowlist.name),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        """Clean up patches, temporary files, and restore original context state."""
        for p in reversed(self.patchers):
            p.stop()
        for path in [self.tmp_allowlist.name, self.tmp_local_allowlist.name]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        ctx.ROOT = self._orig_root
        ctx.PDIR = self._orig_pdir
        ctx.C.clear()
        ctx.C.update(self._orig_c)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _set_allowlist_hosts(self, hosts):
        """Populate the temporary allowlist file with specified hostnames."""
        with open(self.tmp_allowlist.name, "w") as f:
            for h in hosts:
                f.write(f"{h}\n")

    # -------------------------------------------------------------------------
    # Rejection tests for every provider in PROVIDERS_ALL
    # -------------------------------------------------------------------------

    def test_missing_host_anthropic_api(self):
        """anthropic-api save is rejected when api.anthropic.com is missing from allowlist, not written to disk."""
        self._set_allowlist_hosts([])
        errs = settings_apply({
            "PLAN_PROVIDERS": "anthropic-api",
            "ANTHROPIC_MODEL": "claude-3-7-sonnet",
        })
        self.assertIn("PLAN_PROVIDERS", errs)
        self.assertIn("api.anthropic.com", errs["PLAN_PROVIDERS"])
        self.assertIn("anthropic-api", errs["PLAN_PROVIDERS"])
        self.assertIn("missing egress host", errs["PLAN_PROVIDERS"])

        with open(self.foreman_conf) as f:
            conf_text = f.read()
        self.assertNotIn("anthropic-api", conf_text)
        assert "PLAN_PROVIDERS" in errs and "api.anthropic.com" in errs["PLAN_PROVIDERS"] and "anthropic-api" not in conf_text

    def test_missing_host_openai_api(self):
        """openai-api save is rejected when api.openai.com is missing from allowlist, not written to disk."""
        self._set_allowlist_hosts([])
        errs = settings_apply({
            "PLAN_PROVIDERS": "openai-api",
            "OPENAI_MODEL": "gpt-4o",
        })
        self.assertIn("PLAN_PROVIDERS", errs)
        self.assertIn("api.openai.com", errs["PLAN_PROVIDERS"])
        self.assertIn("openai-api", errs["PLAN_PROVIDERS"])
        self.assertIn("missing egress host", errs["PLAN_PROVIDERS"])

        with open(self.foreman_conf) as f:
            conf_text = f.read()
        self.assertNotIn("openai-api", conf_text)
        assert "PLAN_PROVIDERS" in errs and "api.openai.com" in errs["PLAN_PROVIDERS"] and "openai-api" not in conf_text

    def test_missing_host_gemini_api(self):
        """gemini-api save is rejected when generativelanguage.googleapis.com is missing, not written to disk."""
        self._set_allowlist_hosts([])
        errs = settings_apply({
            "REVIEW_PROVIDERS": "gemini-api",
            "GEMINI_MODEL": "gemini-2.5-pro",
        })
        self.assertIn("REVIEW_PROVIDERS", errs)
        self.assertIn("generativelanguage.googleapis.com", errs["REVIEW_PROVIDERS"])
        self.assertIn("gemini-api", errs["REVIEW_PROVIDERS"])
        self.assertIn("missing egress host", errs["REVIEW_PROVIDERS"])

        with open(self.foreman_conf) as f:
            conf_text = f.read()
        self.assertNotIn("gemini-api", conf_text)
        assert "REVIEW_PROVIDERS" in errs and "generativelanguage.googleapis.com" in errs["REVIEW_PROVIDERS"] and "gemini-api" not in conf_text

    def test_missing_host_claude_cli(self):
        """claude-cli save is rejected when its configured host is absent, and setting is not written to disk."""
        simulated_host = "claude-cli.internal"
        self._set_allowlist_hosts([])
        with patch.dict(providers.PROVIDER_HOSTS, {"claude-cli": simulated_host}):
            errs = settings_apply({
                "PLAN_PROVIDERS": "claude-cli",
            })
            self.assertIn("PLAN_PROVIDERS", errs)
            self.assertIn(simulated_host, errs["PLAN_PROVIDERS"])
            self.assertIn("claude-cli", errs["PLAN_PROVIDERS"])
            self.assertIn("missing egress host", errs["PLAN_PROVIDERS"])

        with open(self.foreman_conf) as f:
            conf_text = f.read()
        self.assertNotIn(simulated_host, conf_text)
        assert "PLAN_PROVIDERS" in errs and simulated_host in errs["PLAN_PROVIDERS"] and simulated_host not in conf_text

    def test_missing_host_agy_claude(self):
        """agy-claude save is rejected when its configured host is absent, and setting is not written to disk."""
        simulated_host = "agy-claude.internal"
        self._set_allowlist_hosts([])
        with patch.dict(providers.PROVIDER_HOSTS, {"agy-claude": simulated_host}):
            errs = settings_apply({
                "REVIEW_PROVIDERS": "agy-claude",
            })
            self.assertIn("REVIEW_PROVIDERS", errs)
            self.assertIn(simulated_host, errs["REVIEW_PROVIDERS"])
            self.assertIn("agy-claude", errs["REVIEW_PROVIDERS"])
            self.assertIn("missing egress host", errs["REVIEW_PROVIDERS"])

        with open(self.foreman_conf) as f:
            conf_text = f.read()
        self.assertNotIn(simulated_host, conf_text)
        assert "REVIEW_PROVIDERS" in errs and simulated_host in errs["REVIEW_PROVIDERS"] and simulated_host not in conf_text

    def test_missing_host_agy_gemini(self):
        """agy-gemini save is rejected when its configured host is absent, and setting is not written to disk."""
        simulated_host = "agy-gemini.internal"
        self._set_allowlist_hosts([])
        with patch.dict(providers.PROVIDER_HOSTS, {"agy-gemini": simulated_host}):
            errs = settings_apply({
                "PLAN_PROVIDERS": "agy-gemini",
            })
            self.assertIn("PLAN_PROVIDERS", errs)
            self.assertIn(simulated_host, errs["PLAN_PROVIDERS"])
            self.assertIn("agy-gemini", errs["PLAN_PROVIDERS"])
            self.assertIn("missing egress host", errs["PLAN_PROVIDERS"])

        with open(self.foreman_conf) as f:
            conf_text = f.read()
        self.assertNotIn(simulated_host, conf_text)
        assert "PLAN_PROVIDERS" in errs and simulated_host in errs["PLAN_PROVIDERS"] and simulated_host not in conf_text

    def test_agy_providers_unpinned_succeed_with_empty_allowlist(self):
        """When unpatched, agy-based providers need no pinned egress hosts and succeed even with empty allowlist."""
        self._set_allowlist_hosts([])
        for prov in ("claude-cli", "agy-claude", "agy-gemini"):
            errs_plan = settings_apply({"PLAN_PROVIDERS": prov})
            self.assertEqual(errs_plan, {}, f"Expected {prov} plan save to succeed")
            errs_rev = settings_apply({"REVIEW_PROVIDERS": prov})
            self.assertEqual(errs_rev, {}, f"Expected {prov} review save to succeed")

        with open(self.foreman_conf) as f:
            conf_text = f.read()
        self.assertIn("PLAN_PROVIDERS=agy-gemini", conf_text)
        self.assertIn("REVIEW_PROVIDERS=agy-gemini", conf_text)
        assert not errs_plan and not errs_rev and "PLAN_PROVIDERS=agy-gemini" in conf_text

    # -------------------------------------------------------------------------
    # Positive-case tests per provider family
    # -------------------------------------------------------------------------

    def test_positive_anthropic_api_persists_when_host_present(self):
        """anthropic-api save succeeds and persists to disk when api.anthropic.com is in allowlist."""
        self._set_allowlist_hosts(["api.anthropic.com"])
        errs = settings_apply({
            "PLAN_PROVIDERS": "anthropic-api",
            "ANTHROPIC_MODEL": "claude-3-7-sonnet",
        })
        self.assertEqual(errs, {})
        with open(self.foreman_conf) as f:
            conf_text = f.read()
        self.assertIn("PLAN_PROVIDERS=anthropic-api", conf_text)
        assert not errs and "PLAN_PROVIDERS=anthropic-api" in conf_text

    def test_positive_openai_api_persists_when_host_present(self):
        """openai-api save succeeds and persists to disk when api.openai.com is in allowlist."""
        self._set_allowlist_hosts(["api.openai.com"])
        errs = settings_apply({
            "PLAN_PROVIDERS": "openai-api",
            "OPENAI_MODEL": "gpt-4o",
        })
        self.assertEqual(errs, {})
        with open(self.foreman_conf) as f:
            conf_text = f.read()
        self.assertIn("PLAN_PROVIDERS=openai-api", conf_text)
        assert not errs and "PLAN_PROVIDERS=openai-api" in conf_text

    def test_positive_gemini_api_persists_when_host_present(self):
        """gemini-api save succeeds and persists to disk when generativelanguage.googleapis.com is in allowlist."""
        self._set_allowlist_hosts(["generativelanguage.googleapis.com"])
        errs = settings_apply({
            "REVIEW_PROVIDERS": "gemini-api",
            "GEMINI_MODEL": "gemini-2.5-pro",
        })
        self.assertEqual(errs, {})
        with open(self.foreman_conf) as f:
            conf_text = f.read()
        self.assertIn("REVIEW_PROVIDERS=gemini-api", conf_text)
        assert not errs and "REVIEW_PROVIDERS=gemini-api" in conf_text

    def test_custom_openai_api_base_egress_validation_and_persistence(self):
        """Custom OPENAI_API_BASE egress host is validated: rejected when absent, persisted when present."""
        custom_base = "https://custom-gateway.internal:8443/v1"
        custom_host = "custom-gateway.internal"

        # 1. Negative case: default host is present, but overridden custom host is missing
        self._set_allowlist_hosts(["api.openai.com"])
        errs = settings_apply({
            "PLAN_PROVIDERS": "openai-api",
            "OPENAI_MODEL": "gpt-4o",
            "OPENAI_API_BASE": custom_base,
        })
        self.assertIn("PLAN_PROVIDERS", errs)
        self.assertIn(custom_host, errs["PLAN_PROVIDERS"])
        self.assertIn("openai-api", errs["PLAN_PROVIDERS"])
        with open(self.foreman_conf) as f:
            conf_text = f.read()
        self.assertNotIn(custom_base, conf_text)

        # 2. Positive case: custom host is present in allowlist -> save succeeds and writes both settings
        self._set_allowlist_hosts([custom_host])
        errs_ok = settings_apply({
            "PLAN_PROVIDERS": "openai-api",
            "OPENAI_MODEL": "gpt-4o",
            "OPENAI_API_BASE": custom_base,
        })
        self.assertEqual(errs_ok, {})
        with open(self.foreman_conf) as f:
            conf_text_ok = f.read()
        self.assertIn("PLAN_PROVIDERS=openai-api", conf_text_ok)
        self.assertIn(f"OPENAI_API_BASE={custom_base}", conf_text_ok)
        assert custom_host in errs["PLAN_PROVIDERS"] and not errs_ok and f"OPENAI_API_BASE={custom_base}" in conf_text_ok

    def test_comma_separated_providers_rejects_missing_and_persists_when_present(self):
        """Comma-separated provider chain rejects save naming missing host; succeeds and persists when all present."""
        # 1. Negative case: openai-api host present, but gemini-api host missing
        self._set_allowlist_hosts(["api.openai.com"])
        errs = settings_apply({
            "PLAN_PROVIDERS": "claude-cli,openai-api,gemini-api",
            "OPENAI_MODEL": "gpt-4o",
            "GEMINI_MODEL": "gemini-2.5-pro",
        })
        self.assertIn("PLAN_PROVIDERS", errs)
        self.assertIn("generativelanguage.googleapis.com", errs["PLAN_PROVIDERS"])
        self.assertIn("gemini-api", errs["PLAN_PROVIDERS"])
        with open(self.foreman_conf) as f:
            conf_text = f.read()
        self.assertNotIn("claude-cli,openai-api,gemini-api", conf_text)

        # 2. Positive case: all required hosts present
        self._set_allowlist_hosts(["api.openai.com", "generativelanguage.googleapis.com"])
        errs_ok = settings_apply({
            "PLAN_PROVIDERS": "claude-cli,openai-api,gemini-api",
            "OPENAI_MODEL": "gpt-4o",
            "GEMINI_MODEL": "gemini-2.5-pro",
        })
        self.assertEqual(errs_ok, {})
        with open(self.foreman_conf) as f:
            conf_text_ok = f.read()
        self.assertIn("PLAN_PROVIDERS=claude-cli,openai-api,gemini-api", conf_text_ok)
        assert "generativelanguage.googleapis.com" in errs["PLAN_PROVIDERS"] and not errs_ok and "claude-cli,openai-api,gemini-api" in conf_text_ok


if __name__ == "__main__":
    unittest.main()
