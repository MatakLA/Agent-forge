#!/usr/bin/env python3
"""Unit tests for runtime and provider readiness diagnostic section in bin/doctor.

Public interface:
- Run directly: python3 tests/test_doctor_readiness.py
- Run via unittest: python3 -m unittest tests/test_doctor_readiness.py

Important invariants:
- bin/doctor prints exactly one line per runtime (16 entries) and per provider (6 entries).
- All six new runtimes (copilot, mistral-vibe, ollama, vllm, lmstudio, llamacpp) are covered by doctor readiness checks.
- Local-compat runtimes without configured endpoints report not-ready/not-configured ([WARN]), never falsely ready ([OK]).
- Output format matches doctor conventions with [OK] and [WARN] markers.
- Secrets are NEVER printed in stdout, stderr, or check dictionaries.
- bin/doctor exits 0 when every readiness item is merely missing_setting/missing_egress.
- Readiness checks report level='warn' for unconfigured runtimes/providers without causing hard doctor failure.
"""
import importlib.machinery
import importlib.util
import io
import json
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


    def test_runtime_egress_missing_host(self):
        """Invariant: When used runtime requires egress host not in allowlist, emits FAIL naming runtime and host with fix."""
        doctor.BAD.clear()
        doctor.CHECKS.clear()
        with patch.object(egress, "runtime_missing_hosts", return_value=["api.anthropic.com"]), \
             patch.object(sbx, "worker_runtime", return_value="claude"):
            res = doctor.check_runtime_egress(ws=[("worker-1", "running")])
        self.assertEqual(len(res["bad"]), 1)
        fail_check = res["bad"][0]
        self.assertIn("claude", fail_check["message"])
        self.assertIn("api.anthropic.com", fail_check["message"])
        self.assertIn("[FAIL]", fail_check["message"])
        self.assertEqual(fail_check["fix"], "add api.anthropic.com to egress/allowlist.txt, or allow it from the dashboard's Egress page")
        self.assertIn(fail_check["message"], doctor.BAD)
        assert len(res["bad"]) == 1 and "api.anthropic.com" in fail_check["message"]

    def test_runtime_egress_present_host(self):
        """Invariant: When used runtime required egress host is in allowlist, emits no failure and contributes nothing to exit code."""
        doctor.BAD.clear()
        doctor.CHECKS.clear()
        with patch.object(egress, "runtime_missing_hosts", return_value=[]), \
             patch.object(sbx, "worker_runtime", return_value="claude"):
            res = doctor.check_runtime_egress(ws=[("worker-1", "running")])
        self.assertEqual(len(res["bad"]), 0)
        self.assertEqual(len(doctor.BAD), 0)
        assert len(res["bad"]) == 0 and len(doctor.BAD) == 0

    def test_runtime_endpoint_egress_missing_and_present(self):
        """Invariant: Endpoint runtime passes endpoint URL to runtime_missing_hosts and reports missing host with fix."""
        doctor.BAD.clear()
        doctor.CHECKS.clear()
        captured_args = []
        def fake_missing(rt, endpoint_url=""):
            captured_args.append((rt, endpoint_url))
            return ["compat.corp.internal"] if "corp" in endpoint_url else []

        with patch.object(egress, "runtime_missing_hosts", side_effect=fake_missing), \
             patch.object(sbx, "worker_runtime", return_value="compat"):
            res_bad = doctor.check_runtime_egress(
                ws=[("worker-1", "running")],
                lim={"COMPAT_BASE_URL": "http://compat.corp.internal:8000/v1"},
            )
            self.assertEqual(len(res_bad["bad"]), 1)
            self.assertIn("compat", res_bad["bad"][0]["message"])
            self.assertIn("compat.corp.internal", res_bad["bad"][0]["message"])
            self.assertEqual(res_bad["bad"][0]["fix"], "add compat.corp.internal to egress/allowlist.txt, or allow it from the dashboard's Egress page")

            doctor.BAD.clear()
            doctor.CHECKS.clear()
            res_good = doctor.check_runtime_egress(
                ws=[("worker-1", "running")],
                lim={"COMPAT_BASE_URL": "http://allowed.local:8000/v1"},
            )
            self.assertEqual(len(res_good["bad"]), 0)
            self.assertEqual(len(doctor.BAD), 0)

        self.assertTrue(any(a == ("compat", "http://compat.corp.internal:8000/v1") for a in captured_args))
        assert len(res_bad["bad"]) == 1 and len(res_good["bad"]) == 0

    def test_provider_egress_missing_host(self):
        """Invariant: When configured provider requires egress host missing from allowlist, emits FAIL naming provider and host with fix."""
        doctor.BAD.clear()
        doctor.CHECKS.clear()
        with patch.object(providers, "missing_hosts", return_value=["api.anthropic.com"]):
            res = doctor.check_provider_egress(conf={"PROVIDERS": "anthropic-api"})
        self.assertEqual(len(res["bad"]), 1)
        fail_check = res["bad"][0]
        self.assertIn("anthropic-api", fail_check["message"])
        self.assertIn("api.anthropic.com", fail_check["message"])
        self.assertIn("[FAIL]", fail_check["message"])
        self.assertEqual(fail_check["fix"], "add api.anthropic.com to egress/allowlist.txt, or allow it from the dashboard's Egress page")
        self.assertIn(fail_check["message"], doctor.BAD)
        assert len(res["bad"]) == 1 and "api.anthropic.com" in fail_check["message"]

    def test_provider_egress_present_host(self):
        """Invariant: When configured provider required egress host is in allowlist, emits no failure and contributes nothing to exit code."""
        doctor.BAD.clear()
        doctor.CHECKS.clear()
        with patch.object(providers, "missing_hosts", return_value=[]):
            res = doctor.check_provider_egress(conf={"PROVIDERS": "anthropic-api"})
        self.assertEqual(len(res["bad"]), 0)
        self.assertEqual(len(doctor.BAD), 0)
        assert len(res["bad"]) == 0 and len(doctor.BAD) == 0

    def test_provider_egress_api_base_override(self):
        """Invariant: Custom API base setting is passed to providers.missing_hosts."""
        doctor.BAD.clear()
        doctor.CHECKS.clear()
        calls = []
        def fake_missing(provider, api_base=""):
            calls.append((provider, api_base))
            return ["custom.llm.example.com"]

        with patch.object(providers, "missing_hosts", side_effect=fake_missing):
            res = doctor.check_provider_egress(
                conf={"PROVIDERS": "openai-api", "OPENAI_API_BASE": "https://custom.llm.example.com/v1"}
            )
        self.assertEqual(len(res["bad"]), 1)
        self.assertEqual(calls, [("openai-api", "https://custom.llm.example.com/v1")])
        self.assertIn("custom.llm.example.com", res["bad"][0]["message"])
        assert ("openai-api", "https://custom.llm.example.com/v1") in calls

    def test_check_egress_hosts_composite(self):
        """Invariant: check_egress_hosts executes both runtime and provider checks and aggregates issues."""
        doctor.BAD.clear()
        doctor.CHECKS.clear()
        with patch.object(egress, "runtime_missing_hosts", return_value=["worker.egress.host"]), \
             patch.object(sbx, "worker_runtime", return_value="claude"), \
             patch.object(providers, "missing_hosts", return_value=["provider.egress.host"]):
            res = doctor.check_egress_hosts(
                conf={"PROVIDERS": "anthropic-api"},
                ws=[("worker-1", "running")],
            )
        self.assertEqual(len(res["bad"]), 2)
        hosts_found = [c["fix"] for c in res["bad"]]
        self.assertTrue(any("worker.egress.host" in h for h in hosts_found))
        self.assertTrue(any("provider.egress.host" in h for h in hosts_found))
        assert len(res["bad"]) == 2

    def test_doctor_subprocess_missing_provider_host_exits_nonzero(self):
        """Acceptance criterion: Running bin/doctor with missing provider host prints exact host and exits non-zero."""
        env = dict(os.environ)
        env["PROVIDERS"] = "anthropic-api"
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "bin", "doctor"), "--quick"],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        self.assertIn("api.anthropic.com", proc.stdout)
        self.assertIn("add api.anthropic.com to egress/allowlist.txt, or allow it from the dashboard's Egress page", proc.stdout)
        self.assertIn("[FAIL] anthropic-api: missing egress host 'api.anthropic.com'", proc.stdout)
        self.assertNotEqual(proc.returncode, 0)
        assert proc.returncode != 0 and "api.anthropic.com" in proc.stdout

    def test_doctor_subprocess_present_provider_host_contributes_nothing(self):
        """Acceptance criterion: When provider host is present or not required, check prints no FAIL line for egress."""
        env = dict(os.environ)
        env["PROVIDERS"] = "agy-claude"
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "bin", "doctor"), "--quick"],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        self.assertNotIn("missing egress host", proc.stdout)
        self.assertNotIn("add agy-claude to egress/allowlist.txt", proc.stdout)
        assert "missing egress host" not in proc.stdout

    def test_doctor_readiness_all_six_runtimes_present(self):
        """Acceptance criterion: Assert all six new runtime keys are present in doctor readiness output.

        Fails if any key of copilot, mistral-vibe, ollama, vllm, lmstudio, llamacpp is silently dropped.
        """
        six_keys = ("copilot", "mistral-vibe", "ollama", "vllm", "lmstudio", "llamacpp")

        # 1. Assert all six keys are present in readiness.all_status()["runtimes"]
        st = readiness.all_status()
        self.assertIn("runtimes", st)
        rt_status = st["runtimes"]
        for rt in six_keys:
            self.assertIn(rt, rt_status, f"Runtime '{rt}' was dropped from readiness.all_status()['runtimes']")
            info = rt_status[rt]
            self.assertIn("state", info)
            self.assertIn("detail", info)

        # 2. Assert all six keys produce an explicit result entry in doctor.check_readiness()
        doctor.CHECKS.clear()
        res = doctor.check_readiness()
        checks = res["checks"]
        for rt in six_keys:
            matching = [c for c in checks if f" {rt}: " in c["message"] and c["section"] == "Readiness"]
            self.assertEqual(len(matching), 1, f"Runtime '{rt}' missing from doctor readiness output")
            msg = matching[0]["message"]
            self.assertTrue(msg.startswith("[OK] ") or msg.startswith("[WARN] "), f"Invalid marker for {rt}: {msg}")

        # 3. Prove assertion fails if any key is silently dropped
        for rt in six_keys:
            tampered_status = {
                "runtimes": {k: v for k, v in rt_status.items() if k != rt},
                "providers": dict(st.get("providers", {})),
            }
            tampered_res = doctor.check_readiness(tampered_status)
            tampered_checks = tampered_res["checks"]
            matching = [c for c in tampered_checks if f" {rt}: " in c["message"] and c["section"] == "Readiness"]
            self.assertEqual(len(matching), 0, f"Dropped runtime '{rt}' unexpectedly found in tampered checks")

            # Verify that our verification logic would catch this dropped key
            with self.assertRaises(AssertionError):
                if not any(f" {rt}: " in c["message"] and c["section"] == "Readiness" for c in tampered_checks):
                    raise AssertionError(f"Runtime '{rt}' silently dropped from doctor readiness output")

        assert all(rt in rt_status for rt in six_keys)
        assert all(any(f" {rt}: " in c["message"] and c["section"] == "Readiness" for c in checks) for rt in six_keys)

    def test_local_compat_runtimes_unconfigured_reported_not_ready(self):
        """Acceptance criterion: A local-compat runtime with no endpoint configured reports not-ready/not-configured.

        Never falsely reported as ready when COMPAT_BASE_URL is absent or unconfigured.
        """
        local_compat = ("ollama", "vllm", "lmstudio", "llamacpp")

        # 1. With no endpoint configured
        with patch.object(readiness, "_get_compat_base_url", return_value=""), \
             patch.object(egress, "local_endpoint", return_value=None):
            for rt in local_compat:
                st = readiness.status_of("runtime", rt)
                self.assertNotEqual(st["state"], "ok", f"Runtime '{rt}' must not report state 'ok' when unconfigured")
                self.assertEqual(st["state"], "missing_setting")
                self.assertIn("COMPAT_BASE_URL", st["missing_settings"])
                self.assertIn("COMPAT_BASE_URL", st["detail"])

            doctor.CHECKS.clear()
            res = doctor.check_readiness()
            for rt in local_compat:
                matching = [c for c in res["checks"] if f" {rt}: " in c["message"] and c["section"] == "Readiness"]
                self.assertEqual(len(matching), 1)
                chk = matching[0]
                self.assertEqual(chk["level"], "warn", f"Runtime '{rt}' must be level 'warn' when unconfigured")
                self.assertTrue(chk["message"].startswith(f"[WARN] {rt}: "))
                self.assertIn("COMPAT_BASE_URL", chk["message"])

        # 2. With invalid endpoint URL
        with patch.object(readiness, "_get_compat_base_url", return_value="invalid://bad url"), \
             patch.object(egress, "local_endpoint", return_value=None):
            for rt in local_compat:
                st = readiness.status_of("runtime", rt)
                self.assertNotEqual(st["state"], "ok")
                self.assertEqual(st["state"], "missing_setting")
                self.assertIn("COMPAT_BASE_URL", st["missing_settings"])

        # 3. Positive verification: when endpoint is valid and host is allowed, state is ok
        with patch.object(readiness, "_get_compat_base_url", return_value="http://127.0.0.1:11434/v1"), \
             patch.object(readiness, "_allowed_egress_hosts", return_value={"127.0.0.1"}), \
             patch.object(readiness, "_local_endpoint_host", return_value="127.0.0.1"), \
             patch.object(readiness, "_has_secret", return_value=True):
            for rt in local_compat:
                st = readiness.status_of("runtime", rt)
                self.assertEqual(st["state"], "ok")
                self.assertEqual(st["missing_settings"], [])
                self.assertEqual(st["missing_egress"], [])

        assert all(readiness.status_of("runtime", rt)["state"] != "ok" for rt in local_compat)

    def test_copilot_and_mistral_vibe_readiness_states(self):
        """Invariant: copilot and mistral-vibe readiness checks evaluate settings and egress honestly."""
        # mistral-vibe requires mistral.key and api.mistral.ai allowlist
        with patch.object(readiness, "_has_secret", return_value=False), \
             patch.object(readiness, "_allowed_egress_hosts", return_value={"api.mistral.ai"}):
            st = readiness.status_of("runtime", "mistral-vibe")
            self.assertEqual(st["state"], "missing_setting")
            self.assertIn("mistral.key", st["missing_settings"])

        with patch.object(readiness, "_has_secret", return_value=True), \
             patch.object(readiness, "_allowed_egress_hosts", return_value={"api.mistral.ai"}):
            st = readiness.status_of("runtime", "mistral-vibe")
            self.assertEqual(st["state"], "ok")
            self.assertEqual(st["missing_settings"], [])
            self.assertEqual(st["missing_egress"], [])

        with patch.object(readiness, "_has_secret", return_value=True), \
             patch.object(readiness, "_allowed_egress_hosts", return_value=set()), \
             patch.object(readiness, "_local_endpoint_host", return_value=""):
            st = readiness.status_of("runtime", "mistral-vibe")
            self.assertEqual(st["state"], "missing_egress")
            self.assertIn("api.mistral.ai", st["missing_egress"])

        # copilot requires no API key, requires api.githubcopilot.com and api.github.com
        with patch.object(readiness, "_allowed_egress_hosts", return_value={"api.githubcopilot.com", "api.github.com"}):
            st = readiness.status_of("runtime", "copilot")
            self.assertEqual(st["state"], "ok")
            self.assertEqual(st["missing_settings"], [])
            self.assertEqual(st["missing_egress"], [])

        with patch.object(readiness, "_allowed_egress_hosts", return_value=set()), \
             patch.object(readiness, "_local_endpoint_host", return_value=""):
            st = readiness.status_of("runtime", "copilot")
            self.assertEqual(st["state"], "missing_egress")
            self.assertIn("api.githubcopilot.com", st["missing_egress"])
            self.assertIn("api.github.com", st["missing_egress"])

        assert readiness.status_of("runtime", "mistral-vibe") is not None
        assert readiness.status_of("runtime", "copilot") is not None

    def test_doctor_subprocess_emits_six_runtimes_in_text_and_json(self):
        """Acceptance criterion: Running bin/doctor produces a result entry for each of the six runtime keys."""
        six_keys = ("copilot", "mistral-vibe", "ollama", "vllm", "lmstudio", "llamacpp")

        # 1. Text mode
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "bin", "doctor"), "--quick"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn("== Readiness", proc.stdout)
        self.assertIn("== Cost Models", proc.stdout)
        for rt in six_keys:
            self.assertTrue(
                (f"[OK] {rt}: " in proc.stdout) or (f"[WARN] {rt}: " in proc.stdout),
                f"Runtime '{rt}' not found in doctor --quick output"
            )

        # 2. JSON mode
        proc_json = subprocess.run(
            [sys.executable, os.path.join(ROOT, "bin", "doctor"), "--quick", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotIn("Traceback", proc_json.stderr)
        data = json.loads(proc_json.stdout)
        self.assertIn("checks", data)
        readiness_checks = [c for c in data["checks"] if c.get("section") == "Readiness"]
        cost_checks = [c for c in data["checks"] if c.get("section") == "Cost Models"]
        for rt in six_keys:
            r_matching = [c for c in readiness_checks if f" {rt}: " in c["message"]]
            self.assertEqual(len(r_matching), 1, f"Runtime '{rt}' missing in doctor JSON readiness checks")
            c_matching = [c for c in cost_checks if f" {rt}: " in c["message"]]
            self.assertEqual(len(c_matching), 1, f"Runtime '{rt}' missing in doctor JSON cost model checks")

        assert all(f" {rt}: " in proc.stdout for rt in six_keys)
        assert all(any(f" {rt}: " in c["message"] for c in readiness_checks) for rt in six_keys)


if __name__ == "__main__":
    unittest.main()

