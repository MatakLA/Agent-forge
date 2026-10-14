#!/usr/bin/env python3
"""Tests for quickstart end-to-end runner logic and step budget constraints.

Public interface: run via `python3 -m unittest tests.test_quickstart_e2e -v`.
Invariants:
- Derives step order strictly from STEP_BUDGETS keys without a duplicate step list.
- run_step executes argv via subprocess.run without shell=True and returns positive elapsed seconds.
- run_all stops before executing any later step once an earlier step fails check_budget.
- run_all does not stop early when only the last step exceeds budget; all earlier steps run.
- main prints a report matching format_report from tests.fixtures.quickstart_budget on success.
- main returns 0 on all-within-budget and 1 on over-budget runs.
- main operates strictly against an isolated scratch directory without mutating real runs/ or ~/sandbox.
- Every test method includes top-level bare assertions for static assertion counting.
"""
import io
import os
import shutil
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
    from tests.fixtures.quickstart_budget import (
        STEP_BUDGETS,
        TOTAL_BUDGET,
        format_report,
    )
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
assert callable(format_report)
assert isinstance(STEP_BUDGETS, dict) and len(STEP_BUDGETS) > 0


class TestQuickstartE2ERunner(unittest.TestCase):
    """Unit and regression tests for quickstart end-to-end runner."""

    def test_step_order_derived_from_step_budgets_keys(self):
        """Invariant: step order derived from STEP_BUDGETS keys with no second hardcoded list."""
        pipeline_steps = get_pipeline_steps()
        step_names = [name for name, _ in pipeline_steps]
        expected_names = list(STEP_BUDGETS.keys())
        self.assertEqual(step_names, expected_names)
        assert step_names == expected_names
        self.assertEqual(len(step_names), len(expected_names))
        assert len(step_names) == len(expected_names)

        # Check default commands mapping coverage
        cmds = default_step_commands()
        self.assertEqual(list(cmds.keys()), expected_names)
        assert list(cmds.keys()) == expected_names
        for expected in expected_names:
            self.assertIn(expected, cmds)
            assert expected in cmds
            self.assertIsInstance(cmds[expected], list)
            self.assertGreater(len(cmds[expected]), 0)

    def test_get_pipeline_steps_custom_commands_preserves_order(self):
        """Invariant: get_pipeline_steps preserves STEP_BUDGETS order when given custom commands."""
        step_names = list(STEP_BUDGETS.keys())
        first_step = step_names[0]
        custom = {first_step: ["echo", "custom_first"]}
        steps = get_pipeline_steps(custom)
        derived_names = [name for name, _ in steps]
        self.assertEqual(derived_names, step_names)
        assert derived_names == step_names
        self.assertEqual(steps[0][1], ["echo", "custom_first"])
        assert steps[0][1] == ["echo", "custom_first"]
        # Missing keys fall back to default fallback command
        if len(step_names) > 1:
            self.assertEqual(steps[1][1], ["bin/foreman", "status"])
            assert steps[1][1] == ["bin/foreman", "status"]

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

        steps = get_pipeline_steps()
        first_step_name = steps[0][0]
        first_budget = STEP_BUDGETS[first_step_name]

        # Call 1 (start of step 1): monotonic() -> 0.0
        # Call 2 (end of step 1): monotonic() -> first_budget + 50.0 (over budget)
        # Any subsequent monotonic calls increment by 1.0 (if early-stop logic were broken)
        monotonic_sequence = [0.0, float(first_budget + 50.0), 500.0, 501.0, 502.0, 503.0]
        monotonic_iter = iter(monotonic_sequence)

        with patch("tests.fixtures.quickstart_e2e_runner.subprocess.run", side_effect=mock_subprocess_run):
            with patch("time.monotonic", side_effect=lambda: next(monotonic_iter)):
                ok, msg, timings = run_all(steps)

        # Early termination verification
        self.assertFalse(ok)
        self.assertIn(f"{first_step_name} exceeded budget", msg)
        self.assertEqual(len(executed_argvs), 1)
        self.assertEqual(executed_argvs[0], steps[0][1])
        self.assertIn(first_step_name, timings)

        # Assert later steps' argv were never passed to subprocess.run
        for later_step_name, later_step_argv in steps[1:]:
            self.assertNotIn(later_step_name, timings)
            self.assertNotIn(later_step_argv, executed_argvs)

        assert ok is False
        assert f"{first_step_name} exceeded budget" in msg
        assert len(executed_argvs) == 1
        assert executed_argvs[0] == steps[0][1]
        assert len(timings) == 1

    def test_run_all_does_not_stop_early_when_only_last_step_over_budget(self):
        """Invariant: run_all executes all earlier steps when only the last step is over budget.

        Verified by patching subprocess.run, ensuring all earlier steps finish within budget,
        making only the last step exceed budget, and asserting that every earlier step ran.
        """
        executed_argvs = []

        def mock_subprocess_run(argv, *args, **kwargs):
            executed_argvs.append(argv)
            res = MagicMock()
            res.returncode = 0
            return res

        steps = get_pipeline_steps()
        num_steps = len(steps)
        last_step_name = steps[-1][0]
        last_budget = STEP_BUDGETS[last_step_name]

        # Generate timestamps: earlier steps take 1.0s each; last step exceeds budget
        monotonic_sequence = []
        current_time = 0.0
        for i in range(num_steps - 1):
            monotonic_sequence.append(current_time)
            current_time += 1.0
            monotonic_sequence.append(current_time)
            current_time += 1.0
        # Last step: start and end
        monotonic_sequence.append(current_time)
        current_time += float(last_budget + 10.0)
        monotonic_sequence.append(current_time)

        monotonic_iter = iter(monotonic_sequence)

        with patch("tests.fixtures.quickstart_e2e_runner.subprocess.run", side_effect=mock_subprocess_run):
            with patch("time.monotonic", side_effect=lambda: next(monotonic_iter)):
                ok, msg, timings = run_all(steps)

        self.assertFalse(ok)
        self.assertIn(f"{last_step_name} exceeded budget", msg)
        # All steps' commands ran - did NOT stop early before any earlier step
        self.assertEqual(len(executed_argvs), num_steps)
        for i, (name, argv) in enumerate(steps):
            self.assertEqual(executed_argvs[i], argv)
        self.assertEqual(len(timings), num_steps)

        # Every earlier step recorded elapsed time within budget
        for name, _ in steps[:-1]:
            self.assertIn(name, timings)
            self.assertEqual(timings[name], 1.0)
            self.assertLessEqual(timings[name], STEP_BUDGETS[name])

        # Last step exceeded budget
        self.assertGreater(timings[last_step_name], last_budget)

        assert ok is False
        assert f"{last_step_name} exceeded budget" in msg
        assert len(executed_argvs) == num_steps
        assert len(timings) == num_steps
        assert timings[last_step_name] > last_budget

    def test_run_all_stops_early_at_intermediate_step(self):
        """Invariant: run_all halts at the exact intermediate failing step."""
        executed_argvs = []

        def mock_subprocess_run(argv, *args, **kwargs):
            executed_argvs.append(argv)
            res = MagicMock()
            res.returncode = 0
            return res

        steps = get_pipeline_steps()
        mid_idx = len(steps) // 2
        mid_step_name = steps[mid_idx][0]
        mid_budget = STEP_BUDGETS[mid_step_name]

        # Earlier steps take 1.0s (within budget)
        monotonic_sequence = []
        current_time = 0.0
        for i in range(mid_idx):
            monotonic_sequence.append(current_time)
            current_time += 1.0
            monotonic_sequence.append(current_time)
            current_time += 1.0
        # Intermediate step exceeds budget
        monotonic_sequence.append(current_time)
        current_time += float(mid_budget + 25.0)
        monotonic_sequence.append(current_time)
        # Extra timestamps in case of failure to stop
        for _ in range(10):
            current_time += 1.0
            monotonic_sequence.append(current_time)

        monotonic_iter = iter(monotonic_sequence)

        with patch("tests.fixtures.quickstart_e2e_runner.subprocess.run", side_effect=mock_subprocess_run):
            with patch("time.monotonic", side_effect=lambda: next(monotonic_iter)):
                ok, msg, timings = run_all(steps)

        self.assertFalse(ok)
        self.assertIn(f"{mid_step_name} exceeded budget", msg)
        self.assertEqual(len(executed_argvs), mid_idx + 1)
        for i in range(mid_idx + 1):
            self.assertEqual(executed_argvs[i], steps[i][1])
        self.assertEqual(len(timings), mid_idx + 1)
        for later_name, _ in steps[mid_idx + 1:]:
            self.assertNotIn(later_name, timings)

        assert ok is False
        assert f"{mid_step_name} exceeded budget" in msg
        assert len(executed_argvs) == mid_idx + 1
        assert len(timings) == mid_idx + 1

    def test_run_all_success_when_all_within_budget(self):
        """Invariant: run_all succeeds and records all timings when steps are within budget."""
        executed_argvs = []

        def mock_subprocess_run(argv, *args, **kwargs):
            executed_argvs.append(argv)
            res = MagicMock()
            res.returncode = 0
            return res

        steps = get_pipeline_steps()
        seq = []
        for i in range(len(steps)):
            seq.extend([float(i * 10), float(i * 10 + 2)])
        monotonic_iter = iter(seq)

        with patch("tests.fixtures.quickstart_e2e_runner.subprocess.run", side_effect=mock_subprocess_run):
            with patch("time.monotonic", side_effect=lambda: next(monotonic_iter)):
                ok, msg, timings = run_all(steps)

        self.assertTrue(ok)
        self.assertEqual(msg, "")
        self.assertEqual(len(executed_argvs), len(steps))
        self.assertEqual(list(timings.keys()), list(STEP_BUDGETS.keys()))
        for step in STEP_BUDGETS:
            self.assertEqual(timings[step], 2.0)
        assert ok is True
        assert msg == ""
        assert len(executed_argvs) == len(steps)
        assert list(timings.keys()) == list(STEP_BUDGETS.keys())

    def test_run_all_boundary_exact_step_budget_is_within_budget(self):
        """Invariant: elapsed time exactly equal to step budget is within budget (ok=True)."""
        executed_argvs = []

        def mock_subprocess_run(argv, *args, **kwargs):
            executed_argvs.append(argv)
            res = MagicMock()
            res.returncode = 0
            return res

        steps = get_pipeline_steps()
        seq = []
        current_time = 0.0
        for name, _ in steps:
            seq.append(current_time)
            current_time += float(STEP_BUDGETS[name])
            seq.append(current_time)
        monotonic_iter = iter(seq)

        with patch("tests.fixtures.quickstart_e2e_runner.subprocess.run", side_effect=mock_subprocess_run):
            with patch("time.monotonic", side_effect=lambda: next(monotonic_iter)):
                ok, msg, timings = run_all(steps)

        self.assertTrue(ok)
        self.assertEqual(msg, "")
        self.assertEqual(len(executed_argvs), len(steps))
        for name, _ in steps:
            self.assertEqual(timings[name], float(STEP_BUDGETS[name]))
        assert ok is True
        assert len(executed_argvs) == len(steps)

    def test_run_all_empty_steps_list(self):
        """Invariant: run_all with empty steps list returns ok=True and empty timings."""
        ok, msg, timings = run_all([])
        self.assertTrue(ok)
        self.assertEqual(msg, "")
        self.assertEqual(timings, {})
        assert ok is True
        assert msg == ""
        assert timings == {}

    def test_run_all_handles_command_failure_cleanly(self):
        """Invariant: run_all returns ok=False when a command exits with an error."""
        steps = get_pipeline_steps()
        executed_argvs = []
        failing_step_index = 1
        failing_step_name = steps[failing_step_index][0]

        def mock_subprocess_run(argv, *args, **kwargs):
            executed_argvs.append(argv)
            if argv == steps[failing_step_index][1]:
                raise subprocess.CalledProcessError(returncode=2, cmd=argv)
            res = MagicMock()
            res.returncode = 0
            return res

        seq = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        monotonic_iter = iter(seq)

        with patch("tests.fixtures.quickstart_e2e_runner.subprocess.run", side_effect=mock_subprocess_run):
            with patch("time.monotonic", side_effect=lambda: next(monotonic_iter)):
                ok, msg, timings = run_all(steps)

        self.assertFalse(ok)
        self.assertIn(f"{failing_step_name} failed with exit code 2", msg)
        self.assertEqual(len(executed_argvs), failing_step_index + 1)
        self.assertIn(steps[0][0], timings)
        self.assertNotIn(failing_step_name, timings)
        for later_name, later_argv in steps[failing_step_index + 1:]:
            self.assertNotIn(later_name, timings)
            self.assertNotIn(later_argv, executed_argvs)
        assert ok is False
        assert f"{failing_step_name} failed with exit code 2" in msg

    def test_run_all_handles_generic_exception_cleanly(self):
        """Invariant: run_all catches non-CalledProcessError exceptions and returns ok=False."""
        steps = get_pipeline_steps()
        first_step_name = steps[0][0]

        def mock_subprocess_run(argv, *args, **kwargs):
            raise OSError("Binary not executable")

        with patch("tests.fixtures.quickstart_e2e_runner.subprocess.run", side_effect=mock_subprocess_run):
            ok, msg, timings = run_all(steps)

        self.assertFalse(ok)
        self.assertIn(f"{first_step_name} failed: Binary not executable", msg)
        self.assertEqual(timings, {})
        assert ok is False
        assert "Binary not executable" in msg

    def test_main_success_prints_report_and_returns_zero(self):
        """Invariant: main returns 0 and prints formatted report matching format_report on success."""
        timings_mock = {step: float(i + 1) * 3.0 for i, step in enumerate(STEP_BUDGETS)}

        def mock_run_all(steps, cwd=None, env=None):
            return True, "", timings_mock

        with patch("tests.fixtures.quickstart_e2e_runner.run_all", side_effect=mock_run_all):
            out = io.StringIO()
            err = io.StringIO()
            with patch("sys.stdout", out), patch("sys.stderr", err):
                rc = main([])

        self.assertEqual(rc, 0)
        report = out.getvalue().rstrip("\n")
        expected_report = format_report(timings_mock)
        self.assertEqual(report, expected_report)
        self.assertEqual(err.getvalue(), "")
        assert rc == 0
        assert report == expected_report
        assert len(report) > 0

    def test_main_returns_zero_on_all_within_budget_run(self):
        """Invariant: main returns 0 on an all-within-budget run with mocked subprocess."""
        executed_argvs = []

        def mock_subprocess_run(argv, *args, **kwargs):
            executed_argvs.append(argv)
            res = MagicMock()
            res.returncode = 0
            return res

        steps = get_pipeline_steps()
        seq = []
        for i in range(len(steps)):
            seq.extend([float(i * 10), float(i * 10 + 2)])
        monotonic_iter = iter(seq)

        out = io.StringIO()
        err = io.StringIO()
        with patch("tests.fixtures.quickstart_e2e_runner.subprocess.run", side_effect=mock_subprocess_run):
            with patch("time.monotonic", side_effect=lambda: next(monotonic_iter)):
                with patch("sys.stdout", out), patch("sys.stderr", err):
                    rc = main([])

        self.assertEqual(rc, 0)
        expected_timings = {step: 2.0 for step in STEP_BUDGETS}
        expected_report = format_report(expected_timings)
        self.assertEqual(out.getvalue().rstrip("\n"), expected_report)
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(len(executed_argvs), len(steps))
        assert rc == 0
        assert out.getvalue().rstrip("\n") == expected_report
        assert len(executed_argvs) == len(steps)

    def test_main_step_over_budget_prints_stderr_and_returns_one(self):
        """Invariant: main returns 1 and outputs error to stderr when a step is over budget."""
        first_step = list(STEP_BUDGETS.keys())[0]
        budget = STEP_BUDGETS[first_step]
        fail_msg = f"{first_step} exceeded budget: {budget + 50:.1f}s (budget {budget}s)"

        def mock_run_all(steps, cwd=None, env=None):
            return False, fail_msg, {first_step: float(budget + 50)}

        with patch("tests.fixtures.quickstart_e2e_runner.run_all", side_effect=mock_run_all):
            out = io.StringIO()
            err = io.StringIO()
            with patch("sys.stdout", out), patch("sys.stderr", err):
                rc = main([])

        self.assertEqual(rc, 1)
        self.assertIn(fail_msg, err.getvalue())
        self.assertEqual(out.getvalue(), "")
        assert rc == 1
        assert fail_msg in err.getvalue()

    def test_main_returns_one_on_over_budget_run_with_mocked_subprocess(self):
        """Invariant: main returns 1 on over-budget run when driving through mocked subprocess."""
        executed_argvs = []

        def mock_subprocess_run(argv, *args, **kwargs):
            executed_argvs.append(argv)
            res = MagicMock()
            res.returncode = 0
            return res

        first_step = list(STEP_BUDGETS.keys())[0]
        budget = STEP_BUDGETS[first_step]
        seq = [0.0, float(budget + 15.0), 500.0, 501.0]
        monotonic_iter = iter(seq)

        out = io.StringIO()
        err = io.StringIO()
        with patch("tests.fixtures.quickstart_e2e_runner.subprocess.run", side_effect=mock_subprocess_run):
            with patch("time.monotonic", side_effect=lambda: next(monotonic_iter)):
                with patch("sys.stdout", out), patch("sys.stderr", err):
                    rc = main([])

        self.assertEqual(rc, 1)
        self.assertIn(f"{first_step} exceeded budget", err.getvalue())
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(len(executed_argvs), 1)
        assert rc == 1
        assert f"{first_step} exceeded budget" in err.getvalue()
        assert len(executed_argvs) == 1

    def test_main_total_over_budget_prints_stderr_and_returns_one(self):
        """Invariant: main returns 1 when individual steps pass but total exceeds TOTAL_BUDGET."""
        timings_mock = {step: float(budget) for step, budget in STEP_BUDGETS.items()}
        # Force sum to exceed TOTAL_BUDGET by patching TOTAL_BUDGET low
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
            return True, "", {step: 1.0 for step in STEP_BUDGETS}

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

    def test_main_respects_custom_quickstart_runs_dir_and_fake_docker_dir(self):
        """Invariant: main honors QUICKSTART_RUNS_DIR and QUICKSTART_FAKE_DOCKER_DIR if set."""
        temp_runs = tempfile.mkdtemp(prefix="custom-runs-")
        temp_docker = tempfile.mkdtemp(prefix="custom-docker-")
        captured_env = {}

        def mock_run_all(steps, cwd=None, env=None):
            if env:
                captured_env.update(env)
            return True, "", {step: 1.0 for step in STEP_BUDGETS}

        try:
            with patch.dict(os.environ, {
                "QUICKSTART_RUNS_DIR": temp_runs,
                "QUICKSTART_FAKE_DOCKER_DIR": temp_docker,
            }):
                with patch("tests.fixtures.quickstart_e2e_runner.run_all", side_effect=mock_run_all):
                    with patch("sys.stdout", io.StringIO()):
                        rc = main([])

            self.assertEqual(rc, 0)
            self.assertEqual(captured_env.get("SANDBOX_RUNS_DIR"), temp_runs)
            self.assertIn(temp_docker, captured_env.get("PATH", ""))
            # Custom QUICKSTART_RUNS_DIR is preserved (not deleted by main)
            self.assertTrue(os.path.isdir(temp_runs))
            assert rc == 0
            assert captured_env.get("SANDBOX_RUNS_DIR") == temp_runs
        finally:
            shutil.rmtree(temp_runs, ignore_errors=True)
            shutil.rmtree(temp_docker, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
