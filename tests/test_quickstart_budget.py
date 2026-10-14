#!/usr/bin/env python3
"""Tests for quickstart wall-clock budget evaluation and reporting fixture.

Public interface: run via `python3 -m unittest tests.test_quickstart_budget -v`.
Invariants:
- Evaluates individual pipeline steps against STEP_BUDGETS limits.
- Evaluates total wall-clock duration against TOTAL_BUDGET.
- Rejects unknown pipeline steps with KeyError.
- Formats reports matching exact '<step>: <elapsed:.1f>s (budget <budget>s) <OK|SLOW>' specification.
- Every test method includes top-level bare assertions for static assertion counting.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Top-level smoke-import check: exit non-zero on broken import
try:
    from tests.fixtures.quickstart_budget import (
        STEP_BUDGETS,
        TOTAL_BUDGET,
        check_budget,
        check_total,
        format_report,
    )
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Invariant smoke-import assertion
assert callable(check_budget) and callable(check_total) and callable(format_report)


class TestQuickstartBudget(unittest.TestCase):
    """Unit tests for quickstart wall-clock timing and reporting."""

    def test_step_within_budget(self):
        ok, msg = check_budget("install", 250.0)
        self.assertTrue(ok)
        self.assertIn("install", msg)
        self.assertIn("250.0", msg)
        self.assertIn("300", msg)
        assert ok is True
        assert "install" in msg
        assert "300" in msg

        # Boundary condition: elapsed exactly at budget ceiling is ok
        ok_bound, msg_bound = check_budget("install", 300.0)
        self.assertTrue(ok_bound)
        self.assertIn("install", msg_bound)
        assert ok_bound is True

    def test_step_over_budget(self):
        ok, msg = check_budget("plan", 180.1)
        self.assertFalse(ok)
        self.assertIn("plan", msg)
        self.assertIn("180.1", msg)
        self.assertIn("180", msg)
        assert ok is False
        assert "plan" in msg
        assert "180" in msg

        # Another step significantly over budget
        ok_over, msg_over = check_budget("dispatch", 150.0)
        self.assertFalse(ok_over)
        self.assertIn("dispatch", msg_over)
        self.assertIn("150.0", msg_over)
        self.assertIn("120", msg_over)
        assert ok_over is False
        assert "dispatch" in msg_over

    def test_unknown_step_raises_key_error(self):
        with self.assertRaises(KeyError):
            check_budget("nonexistent_step", 10.0)

        raised = False
        try:
            check_budget("deploy_unknown", 5.0)
        except KeyError:
            raised = True
        self.assertTrue(raised)
        assert raised is True

    def test_check_total_within_budget(self):
        ok, msg = check_total(1500.0)
        self.assertTrue(ok)
        self.assertIn("total", msg)
        self.assertIn("1500.0", msg)
        self.assertIn("1800", msg)
        assert ok is True
        assert "total" in msg
        assert "1800" in msg

        # Boundary condition: total exactly at ceiling is ok
        ok_bound, msg_bound = check_total(1800.0)
        self.assertTrue(ok_bound)
        self.assertIn("total", msg_bound)
        assert ok_bound is True

    def test_check_total_over_budget(self):
        ok, msg = check_total(1800.1)
        self.assertFalse(ok)
        self.assertIn("total", msg)
        self.assertIn("1800.1", msg)
        self.assertIn("1800", msg)
        assert ok is False
        assert "total" in msg
        assert "1800" in msg

        # Total substantially over budget
        ok_over, msg_over = check_total(2000.0)
        self.assertFalse(ok_over)
        self.assertIn("total", msg_over)
        assert ok_over is False
        assert "total" in msg_over

    def test_format_report_exact_lines_two_step_dict(self):
        # 2-step dict with one OK step and one SLOW step
        timings = {"install": 250.0, "plan": 200.0}
        expected = (
            "install: 250.0s (budget 300s) OK\n"
            "plan: 200.0s (budget 180s) SLOW\n"
            "total: 450.0s (budget 1800s) OK"
        )
        report = format_report(timings)
        self.assertEqual(report, expected)
        assert report == expected

        # Reversed input dict preserves STEP_BUDGETS line ordering
        report_reversed = format_report({"plan": 200.0, "install": 250.0})
        self.assertEqual(report_reversed, expected)
        assert report_reversed == expected

    def test_format_report_total_slow(self):
        # Multiple steps resulting in total exceeding TOTAL_BUDGET
        timings = {"install": 1000.0, "gate": 900.0}
        expected = (
            "install: 1000.0s (budget 300s) SLOW\n"
            "gate: 900.0s (budget 300s) SLOW\n"
            "total: 1900.0s (budget 1800s) SLOW"
        )
        report = format_report(timings)
        self.assertEqual(report, expected)
        assert report == expected

    def test_format_report_missing_steps_skipped(self):
        # Missing steps skipped gracefully, single-step reporting
        timings = {"merge": 45.0}
        expected = (
            "merge: 45.0s (budget 60s) OK\n"
            "total: 45.0s (budget 1800s) OK"
        )
        report = format_report(timings)
        self.assertEqual(report, expected)
        assert report == expected

        # Empty dict produces only the total line
        report_empty = format_report({})
        expected_empty = "total: 0.0s (budget 1800s) OK"
        self.assertEqual(report_empty, expected_empty)
        assert report_empty == expected_empty

    def test_constants_and_pipeline_order(self):
        expected_steps = ["install", "plan", "dispatch", "gate", "merge"]
        self.assertEqual(list(STEP_BUDGETS.keys()), expected_steps)
        self.assertEqual(STEP_BUDGETS["install"], 300)
        self.assertEqual(STEP_BUDGETS["plan"], 180)
        self.assertEqual(STEP_BUDGETS["dispatch"], 120)
        self.assertEqual(STEP_BUDGETS["gate"], 300)
        self.assertEqual(STEP_BUDGETS["merge"], 60)
        self.assertEqual(TOTAL_BUDGET, 1800)
        assert list(STEP_BUDGETS.keys()) == expected_steps
        assert TOTAL_BUDGET == 1800


if __name__ == "__main__":
    unittest.main()
