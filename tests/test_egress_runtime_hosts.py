#!/usr/bin/env python3
"""Offline unit tests for runtime egress host allowlist verification.

Public interface:
- Run directly: python3 tests/test_egress_runtime_hosts.py
- Run via pytest: python3 -m pytest tests/test_egress_runtime_hosts.py -q

Important invariants:
- Iterates runtimes.RUNTIMES dynamically so test suite never drifts from runtime registry.
- Exactly one test method per runtime key plus one dedicated endpoint runtime coverage test.
- Every test method contains at least one bare top-level assert statement.
- In-memory mock fixtures only; never reads or writes real files under egress/.
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
    import runtimes
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)


class TestEgressRuntimeHosts(unittest.TestCase):
    """Offline unit tests for runtime egress host allowlist checking."""

    def test_endpoint_coverage_from_local_endpoint(self):
        """Dedicated test for an endpoint runtime proving coverage comes from local_endpoint(), not allowlist()."""
        rt = "compat"
        endpoint_url = "http://192.168.1.100:11434"
        req_hosts = runtimes.hosts(rt, endpoint_url)

        # 1. Coverage from local_endpoint when allowlist is empty
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]), \
             patch.object(egress, "local_endpoint", return_value=("192.168.1.100", 11434)):
            covered_missing = egress.runtime_missing_hosts(rt, endpoint_url)

        # 2. When local_endpoint is not configured, host remains missing despite allowlist empty
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]), \
             patch.object(egress, "local_endpoint", return_value=None):
            uncovered_missing = egress.runtime_missing_hosts(rt, endpoint_url)

        # 3. For a non-endpoint runtime (claude), local_endpoint does NOT provide coverage
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]), \
             patch.object(egress, "local_endpoint", return_value=("api.anthropic.com", 443)):
            non_endpoint_missing = egress.runtime_missing_hosts("claude")

        assert covered_missing == []
        assert uncovered_missing == sorted(req_hosts)
        assert non_endpoint_missing == sorted(runtimes.hosts("claude"))


def _make_runtime_test(rt_key):
    def test_method(self):
        req_hosts = runtimes.hosts(rt_key)

        # 1. With its exact required host(s) patched into the fake allowlist
        with patch.object(egress, "allowlist", return_value=list(req_hosts)), \
             patch.object(egress, "local_allowlist", return_value=[]), \
             patch.object(egress, "local_endpoint", return_value=None):
            covered_missing = egress.runtime_missing_hosts(rt_key)

        # 2. With the fake allowlist empty
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]), \
             patch.object(egress, "local_endpoint", return_value=None):
            empty_missing = egress.runtime_missing_hosts(rt_key)

        # 3. Case-insensitive, whitespace-padded and comments in allowlist
        noisy_allowlist = [f"  {h.upper()}  " for h in req_hosts] + ["", "   ", "# comment", "  # indented comment"]
        with patch.object(egress, "allowlist", return_value=noisy_allowlist), \
             patch.object(egress, "local_allowlist", return_value=[]), \
             patch.object(egress, "local_endpoint", return_value=None):
            noisy_missing = egress.runtime_missing_hosts(rt_key)

        # 4. Coverage provided via local_allowlist()
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=list(req_hosts)), \
             patch.object(egress, "local_endpoint", return_value=None):
            local_allow_missing = egress.runtime_missing_hosts(rt_key)

        expected_empty = sorted(req_hosts)
        assert covered_missing == []
        assert empty_missing == expected_empty
        assert set(empty_missing) == set(req_hosts)
        assert noisy_missing == []
        assert local_allow_missing == []

    test_method.__name__ = f"test_runtime_{rt_key.replace('-', '_')}"
    test_method.__doc__ = f"Verify egress missing hosts check for runtime {rt_key}."
    return test_method


for _rt in runtimes.RUNTIMES:
    _method_name = f"test_runtime_{_rt.replace('-', '_')}"
    setattr(TestEgressRuntimeHosts, _method_name, _make_runtime_test(_rt))


if __name__ == "__main__":
    unittest.main()
