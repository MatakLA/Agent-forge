#!/usr/bin/env python3
"""Tests for orchestrator provider egress host allowlist verification.

Public interface:
- Run directly: python3 tests/test_provider_egress_hosts.py
- Run via unittest: python3 -m unittest tests/test_provider_egress_hosts.py
- Run via pytest: python3 -m pytest tests/test_provider_egress_hosts.py -q

Important invariants:
- Every provider in settings.PROVIDERS_ALL is tested.
- claude-cli, agy-claude, and agy-gemini route through agy and always return [].
- anthropic-api, openai-api, and gemini-api require their provider egress hosts.
- Allowed hosts return [] from missing_hosts(); missing hosts return [host].
- Custom api_base overrides the default host when checking egress allowlists.
"""
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))
sys.path.insert(0, ROOT)

# Top-level smoke-import check: prints traceback and exits non-zero on failure
try:
    import egress
    from bin.lib.foreman.settings import PROVIDERS_ALL
    from bin.lib.providers import PROVIDER_HOSTS, missing_hosts
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)


class TestProviderEgressHosts(unittest.TestCase):
    """Offline unit tests verifying egress allowlist requirements for orchestrator providers."""

    def test_claude_cli(self):
        """Invariant: claude-cli needs no pinned egress host and always returns []."""
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]):
            res_empty = missing_hosts("claude-cli")
        with patch.object(egress, "allowlist", return_value=["api.anthropic.com"]), \
             patch.object(egress, "local_allowlist", return_value=["example.com"]):
            res_populated = missing_hosts("claude-cli")
        self.assertEqual(res_empty, [])
        self.assertEqual(res_populated, [])
        assert res_empty == []
        assert res_populated == []

    def test_agy_claude(self):
        """Invariant: agy-claude needs no pinned egress host and always returns []."""
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]):
            res_empty = missing_hosts("agy-claude")
        with patch.object(egress, "allowlist", return_value=["api.anthropic.com"]), \
             patch.object(egress, "local_allowlist", return_value=["example.com"]):
            res_populated = missing_hosts("agy-claude")
        self.assertEqual(res_empty, [])
        self.assertEqual(res_populated, [])
        assert res_empty == []
        assert res_populated == []

    def test_agy_gemini(self):
        """Invariant: agy-gemini needs no pinned egress host and always returns []."""
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]):
            res_empty = missing_hosts("agy-gemini")
        with patch.object(egress, "allowlist", return_value=["generativelanguage.googleapis.com"]):
            res_populated = missing_hosts("agy-gemini")
        self.assertEqual(res_empty, [])
        self.assertEqual(res_populated, [])
        assert res_empty == []
        assert res_populated == []

    def test_anthropic_api(self):
        """Invariant: anthropic-api requires api.anthropic.com to be allowlisted."""
        provider = "anthropic-api"
        host = PROVIDER_HOSTS[provider]
        with patch.object(egress, "allowlist", return_value=[host]), \
             patch.object(egress, "local_allowlist", return_value=[]):
            res_allowed = missing_hosts(provider)
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]):
            res_missing = missing_hosts(provider)
        self.assertEqual(res_allowed, [])
        self.assertEqual(res_missing, [host])
        assert res_allowed == []
        assert res_missing == [host]

    def test_openai_api(self):
        """Invariant: openai-api requires api.openai.com to be allowlisted."""
        provider = "openai-api"
        host = PROVIDER_HOSTS[provider]
        with patch.object(egress, "allowlist", return_value=[host]), \
             patch.object(egress, "local_allowlist", return_value=[]):
            res_allowed = missing_hosts(provider)
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]):
            res_missing = missing_hosts(provider)
        self.assertEqual(res_allowed, [])
        self.assertEqual(res_missing, [host])
        assert res_allowed == []
        assert res_missing == [host]

    def test_gemini_api(self):
        """Invariant: gemini-api requires generativelanguage.googleapis.com to be allowlisted."""
        provider = "gemini-api"
        host = PROVIDER_HOSTS[provider]
        with patch.object(egress, "allowlist", return_value=[host]), \
             patch.object(egress, "local_allowlist", return_value=[]):
            res_allowed = missing_hosts(provider)
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]):
            res_missing = missing_hosts(provider)
        self.assertEqual(res_allowed, [])
        self.assertEqual(res_missing, [host])
        assert res_allowed == []
        assert res_missing == [host]

    def test_api_base_override(self):
        """Invariant: custom api_base overrides default host and returns overridden host when missing."""
        custom_base = "https://custom-openai.example.org:9000/v1"
        overridden_host = "custom-openai.example.org"
        with patch.object(egress, "allowlist", return_value=["api.openai.com"]), \
             patch.object(egress, "local_allowlist", return_value=[]):
            res_missing = missing_hosts("openai-api", api_base=custom_base)
        with patch.object(egress, "allowlist", return_value=[overridden_host]), \
             patch.object(egress, "local_allowlist", return_value=[]):
            res_allowed = missing_hosts("openai-api", api_base=custom_base)
        self.assertEqual(res_missing, [overridden_host])
        self.assertEqual(res_allowed, [])
        assert res_missing == [overridden_host]
        assert res_allowed == []


# Iterate PROVIDERS_ALL to verify every entry maps to one test method
for _provider in PROVIDERS_ALL:
    _method_name = f"test_{_provider.replace('-', '_')}"
    assert hasattr(TestProviderEgressHosts, _method_name), f"Missing test method {_method_name} for {_provider}"


if __name__ == "__main__":
    unittest.main()
