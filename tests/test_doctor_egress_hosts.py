#!/usr/bin/env python3
"""Regression tests: bin/doctor fails clearly when a runtime's egress host is missing.

Public interface:
- Run directly: python3 tests/test_doctor_egress_hosts.py
- Run via unittest: python3 -m unittest tests/test_doctor_egress_hosts.py

Important invariants:
- Covers every runtime key in bin/lib/runtimes.py RUNTIMES for missing egress hosts.
- Missing required egress hosts emit non-zero/warning status naming both runtime and missing host.
- Positive cases verify that allowlisted hosts produce OK status with no false alarms.
- Uses tempfile.NamedTemporaryFile for allowlists; never writes into repo egress files.
- Every test method contains at least one bare top-level assert statement.
"""
import importlib.machinery
import importlib.util
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
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)


class TestDoctorEgressHosts(unittest.TestCase):
    """Test suite verifying bin/doctor diagnostic reporting for missing runtime egress hosts."""

    def setUp(self):
        """Prepare isolated environment, temp files, and clean doctor state."""
        doctor.CHECKS.clear()
        doctor.BAD.clear()
        doctor.WARN.clear()
        doctor.SECTION[0] = ""
        self.orig_json = doctor.JSON
        doctor.JSON = True
        self.orig_env = dict(os.environ)

        # Isolated temporary directory for secrets
        self.tmp_secrets_dir = tempfile.TemporaryDirectory()
        for k in ["anthropic.key", "openai.key", "gemini.key", "qwen.key", "compat.key"]:
            with open(os.path.join(self.tmp_secrets_dir.name, k), "w") as f:
                f.write("test-secret-content\n")
        self.orig_secrets = sbx.SECRETS
        sbx.SECRETS = self.tmp_secrets_dir.name

        # Temporary files for egress allowlists and endpoint configuration
        self.tmp_allowlist = tempfile.NamedTemporaryFile("w+", delete=False)
        self.tmp_local_allowlist = tempfile.NamedTemporaryFile("w+", delete=False)
        self.tmp_local_endpoint = tempfile.NamedTemporaryFile("w+", delete=False)
        self.tmp_allowlist.close()
        self.tmp_local_allowlist.close()
        self.tmp_local_endpoint.close()

        self.patchers = [
            patch.object(egress, "ALLOWLIST", self.tmp_allowlist.name),
            patch.object(egress, "ALLOWLIST_LOCAL", self.tmp_local_allowlist.name),
            patch.object(egress, "LOCAL_ENDPOINT", self.tmp_local_endpoint.name),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        """Clean up patches, temporary files, and restore original environment."""
        for p in reversed(self.patchers):
            p.stop()
        for path in [self.tmp_allowlist.name, self.tmp_local_allowlist.name, self.tmp_local_endpoint.name]:
            if os.path.exists(path):
                os.remove(path)
        sbx.SECRETS = self.orig_secrets
        self.tmp_secrets_dir.cleanup()

        doctor.JSON = self.orig_json
        os.environ.clear()
        os.environ.update(self.orig_env)

    def _write_allowlist(self, lines):
        with open(self.tmp_allowlist.name, "w") as f:
            for line in lines:
                f.write(f"{line}\n")

    def _write_local_allowlist(self, lines):
        with open(self.tmp_local_allowlist.name, "w") as f:
            for line in lines:
                f.write(f"{line}\n")

    def _write_local_endpoint(self, host, port):
        with open(self.tmp_local_endpoint.name, "w") as f:
            f.write(egress.local_endpoint_conf(host, port))

    def test_missing_host_claude(self):
        """Invariant: Claude runtime missing api.anthropic.com produces a clear warning naming the host."""
        self._write_allowlist([])
        res = doctor.check_readiness()
        chk = [c for c in res["checks"] if " claude: " in c["message"]][0]
        st = readiness.status_of("runtime", "claude")

        self.assertEqual(chk["level"], "warn")
        self.assertIn("claude", chk["message"])
        self.assertIn("api.anthropic.com", chk["message"])
        self.assertIn("api.anthropic.com", st["missing_egress"])
        self.assertEqual(st["state"], "missing_egress")
        assert chk["level"] == "warn" and "api.anthropic.com" in chk["message"]
        assert "api.anthropic.com" in st["missing_egress"]

    def test_missing_host_codex(self):
        """Invariant: Codex runtime missing api.openai.com produces a clear warning naming the host."""
        self._write_allowlist([])
        res = doctor.check_readiness()
        chk = [c for c in res["checks"] if " codex: " in c["message"]][0]
        st = readiness.status_of("runtime", "codex")

        self.assertEqual(chk["level"], "warn")
        self.assertIn("codex", chk["message"])
        self.assertIn("api.openai.com", chk["message"])
        self.assertIn("api.openai.com", st["missing_egress"])
        self.assertEqual(st["state"], "missing_egress")
        assert chk["level"] == "warn" and "api.openai.com" in chk["message"]
        assert "api.openai.com" in st["missing_egress"]

    def test_missing_host_gemini(self):
        """Invariant: Gemini runtime missing generativelanguage.googleapis.com produces a clear warning."""
        self._write_allowlist([])
        res = doctor.check_readiness()
        chk = [c for c in res["checks"] if " gemini: " in c["message"]][0]
        st = readiness.status_of("runtime", "gemini")

        self.assertEqual(chk["level"], "warn")
        self.assertIn("gemini", chk["message"])
        self.assertIn("generativelanguage.googleapis.com", chk["message"])
        self.assertIn("generativelanguage.googleapis.com", st["missing_egress"])
        self.assertEqual(st["state"], "missing_egress")
        assert chk["level"] == "warn" and "generativelanguage.googleapis.com" in chk["message"]
        assert "generativelanguage.googleapis.com" in st["missing_egress"]

    def test_missing_host_qwen_code(self):
        """Invariant: Qwen-Code runtime missing DashScope egress hosts produces a clear warning naming them."""
        self._write_allowlist([])
        res = doctor.check_readiness()
        chk = [c for c in res["checks"] if " qwen-code: " in c["message"]][0]
        st = readiness.status_of("runtime", "qwen-code")

        self.assertEqual(chk["level"], "warn")
        self.assertIn("qwen-code", chk["message"])
        self.assertTrue("dashscope.aliyuncs.com" in chk["message"] or "dashscope-intl.aliyuncs.com" in chk["message"])
        self.assertIn("dashscope.aliyuncs.com", st["missing_egress"])
        self.assertIn("dashscope-intl.aliyuncs.com", st["missing_egress"])
        self.assertEqual(st["state"], "missing_egress")
        assert chk["level"] == "warn" and "dashscope.aliyuncs.com" in chk["message"]
        assert "dashscope.aliyuncs.com" in st["missing_egress"]

    def test_missing_host_compat(self):
        """Invariant: Compat runtime missing model server host produces a clear warning naming the host."""
        os.environ["COMPAT_BASE_URL"] = "http://compat-server.internal:11434"
        self._write_allowlist([])
        res = doctor.check_readiness()
        chk = [c for c in res["checks"] if " compat: " in c["message"]][0]
        st = readiness.status_of("runtime", "compat")

        self.assertEqual(chk["level"], "warn")
        self.assertIn("compat", chk["message"])
        self.assertIn("compat-server.internal", chk["message"])
        self.assertIn("compat-server.internal", st["missing_egress"])
        self.assertEqual(st["state"], "missing_egress")
        assert chk["level"] == "warn" and "compat-server.internal" in chk["message"]
        assert "compat-server.internal" in st["missing_egress"]

    def test_missing_host_aider(self):
        """Invariant: Aider runtime missing model server host produces a clear warning naming the host."""
        os.environ["COMPAT_BASE_URL"] = "http://aider-server.internal:8000"
        self._write_allowlist([])
        res = doctor.check_readiness()
        chk = [c for c in res["checks"] if " aider: " in c["message"]][0]
        st = readiness.status_of("runtime", "aider")

        self.assertEqual(chk["level"], "warn")
        self.assertIn("aider", chk["message"])
        self.assertIn("aider-server.internal", chk["message"])
        self.assertIn("aider-server.internal", st["missing_egress"])
        self.assertEqual(st["state"], "missing_egress")
        assert chk["level"] == "warn" and "aider-server.internal" in chk["message"]
        assert "aider-server.internal" in st["missing_egress"]

    def test_missing_host_opencode(self):
        """Invariant: OpenCode runtime missing model server host produces a clear warning naming the host."""
        os.environ["COMPAT_BASE_URL"] = "http://opencode-server.internal:9000"
        self._write_allowlist([])
        res = doctor.check_readiness()
        chk = [c for c in res["checks"] if " opencode: " in c["message"]][0]
        st = readiness.status_of("runtime", "opencode")

        self.assertEqual(chk["level"], "warn")
        self.assertIn("opencode", chk["message"])
        self.assertIn("opencode-server.internal", chk["message"])
        self.assertIn("opencode-server.internal", st["missing_egress"])
        self.assertEqual(st["state"], "missing_egress")
        assert chk["level"] == "warn" and "opencode-server.internal" in chk["message"]
        assert "opencode-server.internal" in st["missing_egress"]

    def test_missing_host_goose(self):
        """Invariant: Goose runtime missing model server host produces a clear warning naming the host."""
        os.environ["COMPAT_BASE_URL"] = "http://goose-server.internal:5000"
        self._write_allowlist([])
        res = doctor.check_readiness()
        chk = [c for c in res["checks"] if " goose: " in c["message"]][0]
        st = readiness.status_of("runtime", "goose")

        self.assertEqual(chk["level"], "warn")
        self.assertIn("goose", chk["message"])
        self.assertIn("goose-server.internal", chk["message"])
        self.assertIn("goose-server.internal", st["missing_egress"])
        self.assertEqual(st["state"], "missing_egress")
        assert chk["level"] == "warn" and "goose-server.internal" in chk["message"]
        assert "goose-server.internal" in st["missing_egress"]

    def test_missing_host_anthropic_compat(self):
        """Invariant: Anthropic-compat runtime missing model server host produces a clear warning."""
        os.environ["COMPAT_BASE_URL"] = "http://anthropic-server.internal:4000"
        self._write_allowlist([])
        res = doctor.check_readiness()
        chk = [c for c in res["checks"] if " anthropic-compat: " in c["message"]][0]
        st = readiness.status_of("runtime", "anthropic-compat")

        self.assertEqual(chk["level"], "warn")
        self.assertIn("anthropic-compat", chk["message"])
        self.assertIn("anthropic-server.internal", chk["message"])
        self.assertIn("anthropic-server.internal", st["missing_egress"])
        self.assertEqual(st["state"], "missing_egress")
        assert chk["level"] == "warn" and "anthropic-server.internal" in chk["message"]
        assert "anthropic-server.internal" in st["missing_egress"]

    def test_missing_host_agy(self):
        """Invariant: AGY runtime with model API host omitted from allowlist produces clear warning."""
        agy_host = "daily-cloudcode-pa.googleapis.com"
        orig_agy_hosts = list(runtimes.RUNTIMES["agy"]["hosts"])
        try:
            runtimes.RUNTIMES["agy"]["hosts"] = [agy_host]
            self._write_allowlist([])
            res = doctor.check_readiness()
            chk = [c for c in res["checks"] if " agy: " in c["message"]][0]
            st = readiness.status_of("runtime", "agy")

            self.assertEqual(chk["level"], "warn")
            self.assertIn("agy", chk["message"])
            self.assertIn(agy_host, chk["message"])
            self.assertIn(agy_host, st["missing_egress"])
            self.assertEqual(st["state"], "missing_egress")
        finally:
            runtimes.RUNTIMES["agy"]["hosts"] = orig_agy_hosts
        assert chk["level"] == "warn" and agy_host in chk["message"]
        assert agy_host in st["missing_egress"]

    def test_positive_case_hosts_allowed_no_false_alarm(self):
        """Invariant: When required hosts are present in the allowlist, doctor reports OK and no false alarms."""
        endpoint_host = "shared-model-server.internal"
        os.environ["COMPAT_BASE_URL"] = f"http://{endpoint_host}:11434"
        allowed_hosts = [
            "daily-cloudcode-pa.googleapis.com",
            "api.anthropic.com",
            "api.openai.com",
            "generativelanguage.googleapis.com",
            "dashscope.aliyuncs.com",
            "dashscope-intl.aliyuncs.com",
            endpoint_host,
        ]
        self._write_allowlist(allowed_hosts)

        orig_agy_hosts = list(runtimes.RUNTIMES["agy"]["hosts"])
        all_missing = []
        all_levels = []
        try:
            runtimes.RUNTIMES["agy"]["hosts"] = ["daily-cloudcode-pa.googleapis.com"]
            res = doctor.check_readiness()
            for rt in runtimes.RUNTIMES:
                chk = [c for c in res["checks"] if f" {rt}: " in c["message"]][0]
                st = readiness.status_of("runtime", rt)

                self.assertEqual(st["missing_egress"], [])
                self.assertEqual(st["state"], "ok")
                self.assertEqual(chk["level"], "ok")
                self.assertIn(f"[OK] {rt}: Runtime '{rt}' is ready.", chk["message"])
                all_missing.extend(st["missing_egress"])
                all_levels.append(chk["level"])
        finally:
            runtimes.RUNTIMES["agy"]["hosts"] = orig_agy_hosts
        assert all_missing == []
        assert all(lvl == "ok" for lvl in all_levels)

    def test_local_allowlist_covers_required_host(self):
        """Invariant: A host present in local_allowlist (egress/allowlist.local.txt) is considered covered."""
        self._write_allowlist([])
        self._write_local_allowlist(["api.anthropic.com"])
        res = doctor.check_readiness()
        chk = [c for c in res["checks"] if " claude: " in c["message"]][0]
        st = readiness.status_of("runtime", "claude")

        self.assertEqual(st["missing_egress"], [])
        self.assertEqual(st["state"], "ok")
        self.assertEqual(chk["level"], "ok")
        assert st["missing_egress"] == []
        assert chk["level"] == "ok"

    def test_local_endpoint_covers_endpoint_runtime(self):
        """Invariant: For endpoint runtimes, local endpoint configuration satisfies the host requirement."""
        ep_host = "192.168.1.100"
        ep_port = 11434
        os.environ["COMPAT_BASE_URL"] = f"http://{ep_host}:{ep_port}"
        self._write_allowlist([])
        self._write_local_allowlist([])
        self._write_local_endpoint(ep_host, ep_port)

        res = doctor.check_readiness()
        all_missing = []
        all_levels = []
        for rt in ["compat", "aider", "opencode", "goose", "anthropic-compat"]:
            chk = [c for c in res["checks"] if f" {rt}: " in c["message"]][0]
            st = readiness.status_of("runtime", rt)
            self.assertEqual(st["missing_egress"], [])
            self.assertEqual(st["state"], "ok")
            self.assertEqual(chk["level"], "ok")
            all_missing.extend(st["missing_egress"])
            all_levels.append(chk["level"])
        assert all_missing == []
        assert all(lvl == "ok" for lvl in all_levels)

    def test_allowlist_comments_and_case_insensitivity(self):
        """Invariant: Allowlist lines match case-insensitively and ignore '#' comments and whitespace."""
        self._write_allowlist([
            "# top-level comment",
            "   ",
            "  API.ANTHROPIC.COM  ",
            "API.OPENAI.COM # inline comment",
        ])
        st_claude = readiness.status_of("runtime", "claude")
        st_codex = readiness.status_of("runtime", "codex")

        self.assertEqual(st_claude["missing_egress"], [])
        self.assertEqual(st_codex["missing_egress"], [])
        assert st_claude["missing_egress"] == []
        assert st_codex["missing_egress"] == []

    def test_doctor_subprocess_reports_readiness(self):
        """Invariant: Running bin/doctor via subprocess executes cleanly and outputs Readiness section."""
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "bin", "doctor"), "--quick"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertIn("== Readiness", proc.stdout)
        self.assertIn("✔ [OK] agy: Runtime 'agy' is ready.", proc.stdout)
        assert "== Readiness" in proc.stdout
        assert proc.returncode in (0, 1, 2)


# Dynamically assert every runtime key in runtimes.RUNTIMES maps to an explicit test method
for _rt in runtimes.RUNTIMES:
    _method_name = f"test_missing_host_{_rt.replace('-', '_')}"
    assert hasattr(TestDoctorEgressHosts, _method_name), f"Missing test method {_method_name} for runtime {_rt}"


if __name__ == "__main__":
    unittest.main()
