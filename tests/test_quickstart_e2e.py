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

        Verified by patching only subprocess.run (recording each argv it receives),
        patching time.monotonic to return an over-budget elapsed for the first call,
        and asserting that only the first step's argv reached subprocess.run.
        """
        executed_argvs = []

        def mock_subprocess_run(argv, *args, **kwargs):
            executed_argvs.append(argv)
            res = MagicMock()
            res.returncode = 0
            return res

        # install budget is 300s.
        # Call 1 (start of install): monotonic() -> 0.0
        # Call 2 (end of install): monotonic() -> 350.0 (elapsed = 350.0 > 300s -> budget failure)
        # Any subsequent monotonic calls increment by 1.0 (if early-stop logic were broken)
        monotonic_sequence = [0.0, 350.0, 351.0, 352.0, 353.0, 354.0, 355.0, 356.0, 357.0, 358.0]
        monotonic_iter = iter(monotonic_sequence)

        steps = [
            ("install", ["python3", "-m", "unittest", "discover", "-s", "tests"]),
            ("plan", ["bin/foreman", "goal", "test goal"]),
            ("dispatch", ["bin/foreman", "status"]),
            ("gate", ["bin/foreman", "status"]),
            ("merge", ["bin/foreman", "status"]),
        ]

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            with patch("time.monotonic", side_effect=lambda: next(monotonic_iter)):
                ok, msg, timings = run_all(steps)

        # Early termination verification
        self.assertFalse(ok)
        self.assertIn("install exceeded budget", msg)
        self.assertIn("350.0", msg)
        self.assertEqual(len(executed_argvs), 1)
        self.assertEqual(executed_argvs[0], steps[0][1])
        self.assertIn("install", timings)
        self.assertNotIn("plan", timings)
        self.assertNotIn("dispatch", timings)
        self.assertNotIn("gate", timings)
        self.assertNotIn("merge", timings)

        # Assert later steps' argv were never passed to subprocess.run
        for later_step_name, later_step_argv in steps[1:]:
            self.assertNotIn(later_step_argv, executed_argvs)

        assert ok is False
        assert "install exceeded budget" in msg
        assert len(executed_argvs) == 1
        assert executed_argvs[0] == steps[0][1]
        assert "plan" not in timings

    def test_run_all_stops_early_at_intermediate_step(self):
        """Invariant: run_all halts at the exact failing step (e.g., dispatch)."""
        executed_argvs = []

        def mock_subprocess_run(argv, *args, **kwargs):
            executed_argvs.append(argv)
            res = MagicMock()
            res.returncode = 0
            return res

        # Step 1 (install, budget 300s): 0.0 -> 1.0 (elapsed 1.0 <= 300s OK)
        # Step 2 (plan, budget 180s): 2.0 -> 3.0 (elapsed 1.0 <= 180s OK)
        # Step 3 (dispatch, budget 120s): 4.0 -> 150.0 (elapsed 146.0 > 120s OVER BUDGET)
        # Step 4-5 (if called): 151.0 -> 152.0...
        monotonic_sequence = [0.0, 1.0, 2.0, 3.0, 4.0, 150.0, 151.0, 152.0, 153.0, 154.0]
        monotonic_iter = iter(monotonic_sequence)

        steps = get_pipeline_steps()
        with patch("subprocess.run", side_effect=mock_subprocess_run):
            with patch("time.monotonic", side_effect=lambda: next(monotonic_iter)):
                ok, msg, timings = run_all(steps)

        self.assertFalse(ok)
        self.assertIn("dispatch exceeded budget", msg)
        self.assertEqual(len(executed_argvs), 3)
        self.assertEqual(executed_argvs[0], steps[0][1])
        self.assertEqual(executed_argvs[1], steps[1][1])
        self.assertEqual(executed_argvs[2], steps[2][1])
        self.assertEqual(len(timings), 3)
        self.assertNotIn("gate", timings)
        self.assertNotIn("merge", timings)

        # Later steps (gate, merge) were never run: exactly 3 calls occurred
        self.assertEqual(len(executed_argvs), 3)
        self.assertNotIn("gate", timings)
        self.assertNotIn("merge", timings)

        assert ok is False
        assert "dispatch exceeded budget" in msg
        assert len(executed_argvs) == 3
        assert "gate" not in timings
        assert "merge" not in timings

    def test_run_all_success_when_all_within_budget(self):
        """Invariant: run_all succeeds and records all timings when steps are within budget."""
        executed_argvs = []

        def mock_subprocess_run(argv, *args, **kwargs):
            executed_argvs.append(argv)
            res = MagicMock()
            res.returncode = 0
            return res

        # 5 steps, each taking 2.0s
        seq = []
        for i in range(5):
            seq.extend([float(i * 10), float(i * 10 + 2)])
        monotonic_iter = iter(seq)

        steps = get_pipeline_steps()
        with patch("subprocess.run", side_effect=mock_subprocess_run):
            with patch("time.monotonic", side_effect=lambda: next(monotonic_iter)):
                ok, msg, timings = run_all(steps)

        self.assertTrue(ok)
        self.assertEqual(msg, "")
        self.assertEqual(len(executed_argvs), 5)
        self.assertEqual(list(timings.keys()), list(STEP_BUDGETS.keys()))
        for step in STEP_BUDGETS:
            self.assertEqual(timings[step], 2.0)
        assert ok is True
        assert msg == ""
        assert len(executed_argvs) == 5
        assert list(timings.keys()) == list(STEP_BUDGETS.keys())

    def test_run_all_handles_command_failure_cleanly(self):
        """Invariant: run_all returns ok=False when a command exits with an error."""
        steps = get_pipeline_steps()
        executed_argvs = []

        def mock_subprocess_run(argv, *args, **kwargs):
            executed_argvs.append(argv)
            if argv == steps[1][1]:
                raise subprocess.CalledProcessError(returncode=2, cmd=argv)
            res = MagicMock()
            res.returncode = 0
            return res

        seq = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        monotonic_iter = iter(seq)

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            with patch("time.monotonic", side_effect=lambda: next(monotonic_iter)):
                ok, msg, timings = run_all(steps)

        self.assertFalse(ok)
        self.assertIn("plan failed with exit code 2", msg)
        self.assertEqual(len(executed_argvs), 2)
        self.assertIn("install", timings)
        self.assertNotIn("dispatch", timings)
        self.assertNotIn(steps[2][1], executed_argvs)
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
        """Invariant: main sets SANDBOX_TEST and SANDBOX_RUNS_DIR to an isolated scratch temp directory under /tmp."""
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
        self.assertTrue(scratch.startswith(tempfile.gettempdir()) or scratch.startswith("/tmp"))
        self.assertNotEqual(scratch, os.path.join(ROOT, "runs"))
        self.assertNotIn("sandbox/playground", scratch)
        self.assertNotIn(os.path.expanduser("~/sandbox"), scratch)
        self.assertTrue(captured_env.get("SANDBOX_SECRETS", "").startswith(scratch))
        assert captured_env.get("SANDBOX_TEST") == "1"
        assert scratch != os.path.join(ROOT, "runs")
        assert scratch.startswith(tempfile.gettempdir()) or scratch.startswith("/tmp")


if __name__ == "__main__":
    unittest.main()
