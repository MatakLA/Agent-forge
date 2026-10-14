#!/usr/bin/env python3
"""Offline unit tests for runtime egress host allowlist verification.

Public interface:
- Run directly: python3 tests/test_egress_runtime_hosts.py
- Run via pytest: python3 -m pytest tests/test_egress_runtime_hosts.py -q

Important invariants:
- Iterates runtimes.RUNTIMES dynamically so test suite never drifts from runtime registry.
- Dedicated test methods verify local-endpoint handling for localhost/private-LAN and flagging of public endpoints.
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

    def test_local_compat_ollama_endpoints(self):
        """Assert ollama localhost and private-LAN endpoints yield no missing hosts via local_endpoint,
        and public https endpoint is flagged as missing."""
        rt = "ollama"
        # Localhost endpoint
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]), \
             patch.object(egress, "local_endpoint", return_value=("localhost", 11434)):
            assert egress.runtime_missing_hosts(rt, "http://localhost:11434") == []

        # Private-LAN endpoint
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]), \
             patch.object(egress, "local_endpoint", return_value=("192.168.1.50", 11434)):
            assert egress.runtime_missing_hosts(rt, "http://192.168.1.50:11434") == []

        # Uncovered when local_endpoint is not configured
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]), \
             patch.object(egress, "local_endpoint", return_value=None):
            assert egress.runtime_missing_hosts(rt, "http://localhost:11434") == ["localhost"]
            assert egress.runtime_missing_hosts(rt, "http://192.168.1.50:11434") == ["192.168.1.50"]

        # Public HTTPS endpoint is flagged as missing (not silently allowed)
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]), \
             patch.object(egress, "local_endpoint", return_value=("127.0.0.1", 11434)):
            assert egress.runtime_missing_hosts(rt, "https://ollama.example.com/v1") == ["ollama.example.com"]

    def test_local_compat_vllm_endpoints(self):
        """Assert vllm localhost and private-LAN endpoints yield no missing hosts via local_endpoint,
        and public https endpoint is flagged as missing."""
        rt = "vllm"
        # Localhost endpoint (127.0.0.1)
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]), \
             patch.object(egress, "local_endpoint", return_value=("127.0.0.1", 8000)):
            assert egress.runtime_missing_hosts(rt, "http://127.0.0.1:8000") == []

        # Private-LAN endpoint (10.0.0.25)
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]), \
             patch.object(egress, "local_endpoint", return_value=("10.0.0.25", 8000)):
            assert egress.runtime_missing_hosts(rt, "http://10.0.0.25:8000") == []

        # Uncovered when local_endpoint is not configured
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]), \
             patch.object(egress, "local_endpoint", return_value=None):
            assert egress.runtime_missing_hosts(rt, "http://127.0.0.1:8000") == ["127.0.0.1"]
            assert egress.runtime_missing_hosts(rt, "http://10.0.0.25:8000") == ["10.0.0.25"]

        # Public HTTPS endpoint is flagged as missing (not silently allowed)
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]), \
             patch.object(egress, "local_endpoint", return_value=("127.0.0.1", 8000)):
            assert egress.runtime_missing_hosts(rt, "https://vllm.example.org") == ["vllm.example.org"]

    def test_local_compat_lmstudio_endpoints(self):
        """Assert lmstudio localhost and private-LAN endpoints yield no missing hosts via local_endpoint,
        and public https endpoint is flagged as missing."""
        rt = "lmstudio"
        # Localhost endpoint
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]), \
             patch.object(egress, "local_endpoint", return_value=("localhost", 1234)):
            assert egress.runtime_missing_hosts(rt, "http://localhost:1234") == []

        # Private-LAN endpoint (172.16.1.10)
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]), \
             patch.object(egress, "local_endpoint", return_value=("172.16.1.10", 1234)):
            assert egress.runtime_missing_hosts(rt, "http://172.16.1.10:1234") == []

        # Uncovered when local_endpoint is not configured
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]), \
             patch.object(egress, "local_endpoint", return_value=None):
            assert egress.runtime_missing_hosts(rt, "http://localhost:1234") == ["localhost"]
            assert egress.runtime_missing_hosts(rt, "http://172.16.1.10:1234") == ["172.16.1.10"]

        # Public HTTPS endpoint is flagged as missing (not silently allowed)
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]), \
             patch.object(egress, "local_endpoint", return_value=("127.0.0.1", 1234)):
            assert egress.runtime_missing_hosts(rt, "https://lmstudio.example.com") == ["lmstudio.example.com"]

    def test_local_compat_llamacpp_endpoints(self):
        """Assert llamacpp localhost and private-LAN endpoints yield no missing hosts via local_endpoint,
        and public https endpoint is flagged as missing."""
        rt = "llamacpp"
        # Localhost endpoint (127.0.0.1)
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]), \
             patch.object(egress, "local_endpoint", return_value=("127.0.0.1", 8080)):
            assert egress.runtime_missing_hosts(rt, "http://127.0.0.1:8080") == []

        # Private-LAN endpoint (192.168.0.200)
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]), \
             patch.object(egress, "local_endpoint", return_value=("192.168.0.200", 8080)):
            assert egress.runtime_missing_hosts(rt, "http://192.168.0.200:8080") == []

        # Uncovered when local_endpoint is not configured
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]), \
             patch.object(egress, "local_endpoint", return_value=None):
            assert egress.runtime_missing_hosts(rt, "http://127.0.0.1:8080") == ["127.0.0.1"]
            assert egress.runtime_missing_hosts(rt, "http://192.168.0.200:8080") == ["192.168.0.200"]

        # Public HTTPS endpoint is flagged as missing (not silently allowed)
        with patch.object(egress, "allowlist", return_value=[]), \
             patch.object(egress, "local_allowlist", return_value=[]), \
             patch.object(egress, "local_endpoint", return_value=("127.0.0.1", 8080)):
            assert egress.runtime_missing_hosts(rt, "https://llamacpp.example.net") == ["llamacpp.example.net"]

    def test_local_compat_public_https_endpoint_flagged(self):
        """Assert a public https endpoint URL for each local-compat runtime is treated like
        any other public endpoint (flagged as missing, not silently allowed)."""
        public_url = "https://public-models.example.com/v1"
        public_host = "public-models.example.com"

        for rt in ("ollama", "vllm", "lmstudio", "llamacpp"):
            # 1. Flagged when not in allowlist, even if local_endpoint is active
            with patch.object(egress, "allowlist", return_value=[]), \
                 patch.object(egress, "local_allowlist", return_value=[]), \
                 patch.object(egress, "local_endpoint", return_value=("127.0.0.1", 11434)):
                missing = egress.runtime_missing_hosts(rt, public_url)
            assert missing == [public_host]

            # 2. When explicitly approved by the owner in local_allowlist(), it is covered
            with patch.object(egress, "allowlist", return_value=[]), \
                 patch.object(egress, "local_allowlist", return_value=[public_host]), \
                 patch.object(egress, "local_endpoint", return_value=None):
                covered_missing = egress.runtime_missing_hosts(rt, public_url)
            assert covered_missing == []


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
