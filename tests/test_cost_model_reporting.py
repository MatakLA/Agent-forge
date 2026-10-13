#!/usr/bin/env python3
"""Unit and regression tests for cost model reporting in bin/doctor and foreman money() report.

Public interface:
- Run directly: python3 tests/test_cost_model_reporting.py
- Run via unittest: python3 -m unittest tests/test_cost_model_reporting.py

Important invariants:
- money() returns cost_models dict keyed by runtime, containing exact kind and detail from runtimes.cost_model(rt).
- Unknown cost model runtimes (opencode, goose, qwen-code, anthropic-compat) report kind 'unknown' with non-empty detail.
- Metered runtimes report kind 'metered', quota runtimes report kind 'quota'.
- bin/doctor emits a cost-model line for every checked runtime without failing or altering exit codes.
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

import runtimes
import sbx
from foreman import readiness, report


# Dynamically load bin/doctor
doc_path = os.path.join(ROOT, "bin", "doctor")
loader = importlib.machinery.SourceFileLoader("doctor", doc_path)
spec = importlib.util.spec_from_loader("doctor", loader)
doctor = importlib.util.module_from_spec(spec)
loader.exec_module(doctor)


class TestMoneyCostModels(unittest.TestCase):
    """Regression and unit tests for cost_models reporting in foreman report.money()."""

    def setUp(self):
        self.orig_env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_env)

    def test_money_cost_models_with_fixture_runs(self):
        """Acceptance criterion: money() returns cost_models for opencode/goose/qwen-code/anthropic-compat

        and metered runtime (claude), asserting kind=='unknown' with non-empty detail, and kind=='metered'.
        """
        fixture_stats = {
            "run-1": {"runtime": "opencode", "model": "opencode:default", "cost": None},
            "run-2": {"runtime": "goose", "model": "goose:default", "cost": None},
            "run-3": {"runtime": "qwen-code", "model": "qwen-code:default", "cost": None},
            "run-4": {"runtime": "anthropic-compat", "model": "anthropic-compat:default", "cost": None},
            "run-5": {"runtime": "claude", "model": "claude:sonnet", "cost": 0.05},
            "run-6": {"runtime": "agy", "model": "gemini", "cost": None},
        }

        with patch("foreman.report.run_stats", return_value=fixture_stats), \
             patch("sbx.read_ledger", return_value=[]):
            res = report.money()

        self.assertIn("cost_models", res)
        cms = res["cost_models"]
        self.assertIsInstance(cms, dict)

        # Assert all runtimes from fixture appear
        for rt in ("opencode", "goose", "qwen-code", "anthropic-compat", "claude", "agy"):
            self.assertIn(rt, cms)
            self.assertIn("kind", cms[rt])
            self.assertIn("detail", cms[rt])
            self.assertTrue(len(cms[rt]["detail"]) > 0)
            expected = runtimes.cost_model(rt)
            self.assertEqual(cms[rt]["kind"], expected["kind"])
            self.assertEqual(cms[rt]["detail"], expected["detail"])

        # Specific assertions per acceptance criteria
        for unknown_rt in ("opencode", "goose", "qwen-code", "anthropic-compat"):
            self.assertEqual(cms[unknown_rt]["kind"], "unknown")
            self.assertTrue(bool(cms[unknown_rt]["detail"]))

        self.assertEqual(cms["claude"]["kind"], "metered")
        self.assertEqual(cms["agy"]["kind"], "quota")

    def test_money_empty_run_stats_returns_empty_cost_models(self):
        """Invariant: When run_stats returns no runs, cost_models is empty dict."""
        with patch("foreman.report.run_stats", return_value={}), \
             patch("sbx.read_ledger", return_value=[]):
            res = report.money()
        self.assertIn("cost_models", res)
        self.assertEqual(res["cost_models"], {})

    def test_money_each_unknown_runtime_individually(self):
        """Falsifiability test: Verify each individual unknown runtime independently."""
        unknown_runtimes = ["opencode", "goose", "qwen-code", "anthropic-compat"]
        for rt in unknown_runtimes:
            fixture = {f"run-{rt}": {"runtime": rt}}
            with patch("foreman.report.run_stats", return_value=fixture), \
                 patch("sbx.read_ledger", return_value=[]):
                res = report.money()
            self.assertIn(rt, res["cost_models"])
            self.assertEqual(res["cost_models"][rt]["kind"], "unknown")
            self.assertEqual(res["cost_models"][rt]["detail"], runtimes.cost_model(rt)["detail"])


class TestDoctorCostModels(unittest.TestCase):
    """Unit and integration tests for cost-model diagnostics in bin/doctor."""

    def setUp(self):
        doctor.CHECKS.clear()
        doctor.BAD.clear()
        doctor.WARN.clear()
        doctor.SECTION[0] = ""
        self.orig_json = doctor.JSON
        doctor.JSON = True
        self.orig_env = dict(os.environ)

    def tearDown(self):
        doctor.JSON = self.orig_json
        os.environ.clear()
        os.environ.update(self.orig_env)

    def test_doctor_has_check_cost_models_function(self):
        """Invariant: doctor has callable check_cost_models function."""
        self.assertTrue(hasattr(doctor, "check_cost_models"))
        self.assertTrue(callable(doctor.check_cost_models))

    def test_check_cost_models_reports_every_registered_runtime(self):
        """Acceptance criterion: cost-model line for every checked runtime with literal 'unknown'

        for opencode, qwen-code, goose, anthropic-compat.
        """
        res = doctor.check_cost_models()
        checks = res["checks"]
        all_runtimes = readiness.runtime_items()
        self.assertEqual(len(checks), len(all_runtimes))

        # Check each runtime line
        for rt in all_runtimes:
            cm = runtimes.cost_model(rt)
            matching = [c for c in checks if f" {rt}: " in c["message"]]
            self.assertEqual(len(matching), 1, f"Expected exactly one check line for runtime {rt}")
            msg = matching[0]["message"]
            self.assertIn(cm["kind"], msg)
            self.assertIn(cm["detail"], msg)
            self.assertEqual(matching[0]["level"], "ok")

        # Specific assertions for the 4 unknown runtimes
        for unknown_rt in ("opencode", "qwen-code", "goose", "anthropic-compat"):
            matching = [c for c in checks if f" {unknown_rt}: " in c["message"]]
            self.assertIn("unknown", matching[0]["message"])

        # Specific assertions for metered and quota runtimes
        matching_claude = [c for c in checks if " claude: " in c["message"]]
        self.assertIn("metered", matching_claude[0]["message"])
        matching_agy = [c for c in checks if " agy: " in c["message"]]
        self.assertIn("quota", matching_agy[0]["message"])

    def test_check_cost_models_does_not_fail_doctor(self):
        """Acceptance criterion: exit code is unaffected by unknown cost models."""
        res = doctor.check_cost_models()
        self.assertEqual(len(res["bad"]), 0)
        self.assertEqual(len(res["warn"]), 0)
        self.assertEqual(len(doctor.BAD), 0)
        self.assertEqual(len(doctor.WARN), 0)
        exit_code = 2 if doctor.BAD else 1 if doctor.WARN else 0
        self.assertEqual(exit_code, 0)

    def test_check_cost_models_custom_rts_subset(self):
        """Falsifiability test: passing subset of runtimes checks only those runtimes."""
        res = doctor.check_cost_models(["opencode", "claude"])
        checks = res["checks"]
        self.assertEqual(len(checks), 2)
        self.assertTrue(any(" opencode: " in c["message"] and "unknown" in c["message"] for c in checks))
        self.assertTrue(any(" claude: " in c["message"] and "metered" in c["message"] for c in checks))

    def test_bin_doctor_subprocess_stdout_contains_cost_models(self):
        """Integration test: bin/doctor CLI output contains visible cost model lines."""
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "bin", "doctor"), "--quick"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        stdout = proc.stdout
        self.assertIn("== Cost Models", stdout)
        for unknown_rt in ("opencode", "qwen-code", "goose", "anthropic-compat"):
            self.assertIn(f"{unknown_rt}: cost model unknown", stdout)
            expected_detail = runtimes.cost_model(unknown_rt)["detail"]
            self.assertIn(expected_detail, stdout)

        self.assertIn("claude: cost model metered", stdout)
        self.assertIn("agy: cost model quota", stdout)

    def test_bin_doctor_subprocess_json_contains_cost_models(self):
        """Integration test: bin/doctor --json output contains cost model checks."""
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "bin", "doctor"), "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        data = json.loads(proc.stdout)
        cm_checks = [c for c in data.get("checks", []) if c.get("section") == "Cost Models"]
        self.assertEqual(len(cm_checks), len(readiness.runtime_items()))
        for c in cm_checks:
            self.assertEqual(c["level"], "ok")


if __name__ == "__main__":
    unittest.main()
