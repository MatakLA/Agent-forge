#!/usr/bin/env python3
"""Tests for quickstart end-to-end runner logic and step budget constraints.

Public interface: run via `python3 -m unittest tests.test_quickstart_e2e -v`.
Invariants:
- Derives step order strictly from STEP_BUDGETS keys without a duplicate step list.
- run_step executes argv via subprocess.run without shell=True and returns positive elapsed seconds.
- run_all stops before executing any later step once an earlier step fails check_budget.
- Verified by monkeypatching run_step to return over-budget for first step and asserting later argv never run.
- main operates strictly against an isolated scratch directory without mutating real runs/ or ~/sandbox.
- Every test method includes top-level bare assertions for static assertion counting.
"""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Top-level smoke-import check: exit non-zero on broken import
try:
    from tests.fixtures.quickstart_budget import STEP_BUDGETS, TOTAL_BUDGET
    from tests.fixtures.quickstart_e2e_runner import (
        default_step_commands,
        get_pipeline_steps,
        run_step,
        run_all,
        main,
    )
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Invariant smoke-import assertions
assert callable(run_step) and callable(run_all) and callable(main) and callable(get_pipeline_steps)
assert list(STEP_BUDGETS.keys()) == ["install", "plan", "dispatch", "gate", "merge"]


class TestQuickstartE2ERunner(unittest.TestCase):
    """Unit and regression tests for quickstart end-to-end runner."""

    def test_step_order_derived_from_step_budgets_keys(self):
        """Invariant: step order derived from STEP_BUDGETS keys with no second hardcoded list."""
        pipeline_steps = get_pipeline_steps()
        step_names = [name for name, _ in pipeline_steps]
        expected_names = list(STEP_BUDGETS.keys())
        self.assertEqual(step_names, expected_names)
        assert step_names == expected_names
        assert len(step_names) == 5

        # Check default commands mapping coverage
        cmds = default_step_commands()
        for expected in expected_names:
            self.assertIn(expected, cmds)
            assert expected in cmds
            self.assertIsInstance(cmds[expected], list)
            assert len(cmds[expected]) > 0

    def test_run_step_executes_argv_and_measures_elapsed(self):
        """Invariant: run_step executes argv without shell=True and returns float seconds."""
        argv = [sys.executable, "-c", "import time; time.sleep(0.02)"]
        elapsed = run_step("test_step", argv)
        self.assertIsInstance(elapsed, float)
        self.assertGreater(elapsed, 0.01)
        assert isinstance(elapsed, float)
        assert elapsed > 0.01

    def test_run_step_raises_on_command_failure(self):
        """Invariant: run_step raises CalledProcessError when command exits non-zero."""
        argv = [sys.executable, "-c", "import sys; sys.exit(7)"]
        with self.assertRaises(subprocess.CalledProcessError) as ctx:
            run_step("failing_step", argv)
        self.assertEqual(ctx.exception.returncode, 7)
        assert ctx.exception.returncode == 7

    def test_run_all_stops_early_on_first_over_budget_step(self):
        """Invariant: run_all stops before running later steps when check_budget returns ok=False.

        Verified by monkeypatching run_step to return an over-budget time for the first step
        and asserting later steps' argv were never passed to subprocess.run.
        """
        called_argvs = []

        def mock_run_step(name, argv, cwd=None, env=None):
            called_argvs.append((name, argv))
            if name == "install":
                # Exceed install budget (300s)
                return 350.0
            return 10.0

        steps = [
            ("install", ["echo", "install"]),
            ("plan", ["echo", "plan"]),
            ("dispatch", ["echo", "dispatch"]),
            ("gate", ["echo", "gate"]),
            ("merge", ["echo", "merge"]),
        ]

        with patch("tests.fixtures.quickstart_e2e_runner.run_step", side_effect=mock_run_step):
            with patch("subprocess.run") as mock_subproc:
                ok, msg, timings = run_all(steps)

        # Early termination verification
        self.assertFalse(ok)
        self.assertIn("install exceeded budget", msg)
        self.assertIn("350.0", msg)
        self.assertEqual(len(called_argvs), 1)
        self.assertEqual(called_argvs[0][0], "install")
        self.assertIn("install", timings)
        self.assertNotIn("plan", timings)
        self.assertNotIn("dispatch", timings)
        self.assertNotIn("gate", timings)
        self.assertNotIn("merge", timings)

        # Verify later steps' argv were never executed by subprocess.run
        mock_subproc.assert_not_called()
        assert ok is False
        assert "install exceeded budget" in msg
        assert len(called_argvs) == 1
        assert "plan" not in timings

    def test_run_all_stops_early_at_intermediate_step(self):
        """Invariant: run_all halts at the exact failing step (e.g., dispatch)."""
        called_steps = []

        def mock_run_step(name, argv, cwd=None, env=None):
            called_steps.append(name)
            if name == "dispatch":
                return 150.0  # Exceed dispatch budget (120s)
            return 1.0

        steps = get_pipeline_steps()
        with patch("tests.fixtures.quickstart_e2e_runner.run_step", side_effect=mock_run_step):
            ok, msg, timings = run_all(steps)

        self.assertFalse(ok)
        self.assertIn("dispatch exceeded budget", msg)
        self.assertEqual(called_steps, ["install", "plan", "dispatch"])
        self.assertEqual(len(timings), 3)
        self.assertNotIn("gate", timings)
        self.assertNotIn("merge", timings)
        assert ok is False
        assert "dispatch exceeded budget" in msg
        assert called_steps == ["install", "plan", "dispatch"]

    def test_run_all_success_when_all_within_budget(self):
        """Invariant: run_all succeeds and records all timings when steps are within budget."""
        def mock_run_step(name, argv, cwd=None, env=None):
            return 2.0

        steps = get_pipeline_steps()
        with patch("tests.fixtures.quickstart_e2e_runner.run_step", side_effect=mock_run_step):
            ok, msg, timings = run_all(steps)

        self.assertTrue(ok)
        self.assertEqual(msg, "")
        self.assertEqual(list(timings.keys()), list(STEP_BUDGETS.keys()))
        for step in STEP_BUDGETS:
            self.assertEqual(timings[step], 2.0)
        assert ok is True
        assert msg == ""
        assert list(timings.keys()) == list(STEP_BUDGETS.keys())

    def test_run_all_handles_command_failure_cleanly(self):
        """Invariant: run_all returns ok=False when a command exits with an error."""
        def mock_run_step(name, argv, cwd=None, env=None):
            if name == "plan":
                raise subprocess.CalledProcessError(returncode=2, cmd=argv)
            return 1.0

        steps = get_pipeline_steps()
        with patch("tests.fixtures.quickstart_e2e_runner.run_step", side_effect=mock_run_step):
            ok, msg, timings = run_all(steps)

        self.assertFalse(ok)
        self.assertIn("plan failed with exit code 2", msg)
        self.assertIn("install", timings)
        self.assertNotIn("dispatch", timings)
        assert ok is False
        assert "plan failed with exit code 2" in msg

    def test_main_success_prints_report_and_returns_zero(self):
        """Invariant: main returns 0 and prints formatted report on success."""
        timings_mock = {
            "install": 10.0,
            "plan": 5.0,
            "dispatch": 5.0,
            "gate": 10.0,
            "merge": 2.0,
        }

        def mock_run_all(steps, cwd=None, env=None):
            return True, "", timings_mock

        with patch("tests.fixtures.quickstart_e2e_runner.run_all", side_effect=mock_run_all):
            out = io.StringIO()
            err = io.StringIO()
            with patch("sys.stdout", out), patch("sys.stderr", err):
                rc = main([])

        self.assertEqual(rc, 0)
        report = out.getvalue()
        self.assertIn("install: 10.0s (budget 300s) OK", report)
        self.assertIn("plan: 5.0s (budget 180s) OK", report)
        self.assertIn("dispatch: 5.0s (budget 120s) OK", report)
        self.assertIn("gate: 10.0s (budget 300s) OK", report)
        self.assertIn("merge: 2.0s (budget 60s) OK", report)
        self.assertIn("total: 32.0s (budget 1800s) OK", report)
        self.assertEqual(err.getvalue(), "")
        assert rc == 0
        assert "total: 32.0s (budget 1800s) OK" in report

    def test_main_step_over_budget_prints_stderr_and_returns_one(self):
        """Invariant: main returns 1 and outputs error to stderr when a step is over budget."""
        def mock_run_all(steps, cwd=None, env=None):
            return False, "plan exceeded budget: 200.0s (budget 180s)", {"install": 10.0, "plan": 200.0}

        with patch("tests.fixtures.quickstart_e2e_runner.run_all", side_effect=mock_run_all):
            out = io.StringIO()
            err = io.StringIO()
            with patch("sys.stdout", out), patch("sys.stderr", err):
                rc = main([])

        self.assertEqual(rc, 1)
        self.assertIn("plan exceeded budget: 200.0s (budget 180s)", err.getvalue())
        self.assertEqual(out.getvalue(), "")
        assert rc == 1
        assert "plan exceeded budget" in err.getvalue()

    def test_main_total_over_budget_prints_stderr_and_returns_one(self):
        """Invariant: main returns 1 when individual steps pass but total exceeds TOTAL_BUDGET."""
        timings_mock = {
            "install": 290.0,
            "plan": 170.0,
            "dispatch": 110.0,
            "gate": 290.0,
            "merge": 50.0,
        }
        # Force sum to exceed TOTAL_BUDGET
        with patch("tests.fixtures.quickstart_budget.TOTAL_BUDGET", 500):
            def mock_run_all(steps, cwd=None, env=None):
                return True, "", timings_mock

            with patch("tests.fixtures.quickstart_e2e_runner.run_all", side_effect=mock_run_all):
                out = io.StringIO()
                err = io.StringIO()
                with patch("sys.stdout", out), patch("sys.stderr", err):
                    rc = main([])

            self.assertEqual(rc, 1)
            self.assertIn("total exceeded budget", err.getvalue())
            assert rc == 1
            assert "total exceeded budget" in err.getvalue()

    def test_main_uses_scratch_directory_and_never_touches_real_runs(self):
        """Invariant: main sets SANDBOX_TEST and SANDBOX_RUNS_DIR to a scratch temp directory."""
        captured_env = {}

        def mock_run_all(steps, cwd=None, env=None):
            if env:
                captured_env.update(env)
            return True, "", {"install": 1.0}

        with patch("tests.fixtures.quickstart_e2e_runner.run_all", side_effect=mock_run_all):
            with patch("sys.stdout", io.StringIO()):
                rc = main([])

        self.assertEqual(rc, 0)
        self.assertEqual(captured_env.get("SANDBOX_TEST"), "1")
        scratch = captured_env.get("SANDBOX_RUNS_DIR")
        self.assertIsNotNone(scratch)
        self.assertNotEqual(scratch, os.path.join(ROOT, "runs"))
        self.assertNotIn("sandbox/playground", scratch)
        assert captured_env.get("SANDBOX_TEST") == "1"
        assert scratch != os.path.join(ROOT, "runs")


if __name__ == "__main__":
    unittest.main()
