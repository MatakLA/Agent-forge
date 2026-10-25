"""Integration test suite using tools/fake-docker for the visual gate runner.

Public interface:
- Run directly: python3 tests/test_visual_gate_docker.py
- Run with pytest: pytest tests/test_visual_gate_docker.py

Invariants:
- Off by default: visual gate is skipped when VISUAL_CMD is empty/unset, invoking no container.
- On and passing: visual verification exiting 0 with PNGs in /out passes and attaches visual artifacts.
- Failing rework: visual verification exiting non-zero fails the gate and triggers rework with trailing 60 lines of output.
- Missing image: nonexistent local container image fails fast with explicit error and does not silently pass.
- Timeout: container exceeding VISUAL_TIMEOUT_SEC is terminated with timeout error.
"""
import ast
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
bin_lib = os.path.join(ROOT, "bin", "lib")
if bin_lib not in sys.path:
    sys.path.insert(0, bin_lib)

import sbx
import visual

REAL_FAKE_DOCKER = os.path.join(ROOT, "tools", "fake-docker")


def create_fake_docker_shim(shim_path: str, real_docker: str = REAL_FAKE_DOCKER) -> None:
    """Create an executable fake-docker shim wrapping tools/fake-docker for visual gate testing."""
    shim_script = f"""#!/bin/sh
MODE="${{FAKE_DOCKER_MODE:-default}}"

# Missing image mode or missing image tag delegates directly to tools/fake-docker (which exits 1)
if [ "$1" = "image" ] && [ "$2" = "inspect" ]; then
    if [ "$MODE" = "MISSING_IMAGE" ] || [ "$3" = "missing-image:1.0" ] || [ "$3" = "nonexistent:latest" ]; then
        exec "{real_docker}" "$@"
    fi
    exit 0
fi

if [ "$1" = "run" ]; then
    case "$MODE" in
        PASS)
            # Find the host output directory mounted to /out:rw or /out
            for arg in "$@"; do
                case "$arg" in
                    *:/out:rw|*:/out)
                        out_host="${{arg%%:*}}"
                        mkdir -p "$out_host"
                        # Generate valid PNG screenshots in /out
                        printf '\\x89PNG\\r\\n\\x1a\\n' > "$out_host/diff.png"
                        printf '\\x89PNG\\r\\n\\x1a\\n' > "$out_host/screen.png"
                        # Also write a non-png file to verify non-png exclusion
                        printf 'non-image log' > "$out_host/report.txt"
                        ;;
                esac
            done
            printf "Rendering complete: 2 screenshots captured\\nOK\\n"
            exit 0
            ;;
        FAIL)
            # Emit >60 lines of combined diagnostic output with visual regression failure at tail
            for i in $(seq 1 75); do
                echo "Diagnostic trace line $i: component render check"
            done
            echo "Error: visual regression detected: layout mismatch on #main-view"
            exit 1
            ;;
        TIMEOUT)
            sleep 10
            exit 0
            ;;
        *)
            exec "{real_docker}" "$@"
            ;;
    esac
fi

exec "{real_docker}" "$@"
"""
    with open(shim_path, "w") as f:
        f.write(shim_script)
    os.chmod(shim_path, 0o755)


def apply_visual_gate(task: dict, ok: bool, tail: str, pngs: list[str]) -> dict:
    """Simulate foreman merge gate integration of visual gate results into task state."""
    if not ok:
        # Trigger rework with actionable feedback from output tail
        task["status"] = "pending"
        task["attempts"] = task.get("attempts", 0) + 1
        task["gate_tail"] = tail
        task["visual_gate_failed"] = True
        history = task.setdefault("history", [])
        last_line = tail.splitlines()[-1] if tail.splitlines() else "Unknown failure"
        history.append({
            "at": "2026-10-25T12:00:00Z",
            "outcome": "rework",
            "feedback": f"Visual gate failed: {last_line}"[:1500],
        })
        return task

    # On pass: task proceeds and visual screenshot artifacts are attached
    if pngs:
        task["visual_artifacts"] = list(pngs)
    task["visual_gate_passed"] = True
    return task


class TestVisualGateDocker(unittest.TestCase):
    """Test suite executing the 5 visual gate container scenarios using tools/fake-docker."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="vis-test-")
        self.workdir = os.path.join(self.temp_dir, "workspace")
        os.makedirs(self.workdir, exist_ok=True)
        self.out_dir = os.path.join(self.temp_dir, "out")
        os.makedirs(self.out_dir, exist_ok=True)
        self.shim_bin = os.path.join(self.temp_dir, "fake-docker")
        create_fake_docker_shim(self.shim_bin, REAL_FAKE_DOCKER)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_visual_gate_off_by_default(self):
        """1. Off by default: verify visual gate is skipped when VISUAL_CMD is not set."""
        task = {"id": "0321-off", "status": "gate", "attempts": 0, "history": []}

        # Scenario A: empty string
        ok, tail, pngs = visual.run_visual_gate(
            workdir=self.workdir,
            visual_cmd="",
            visual_image="sandbox-worker:1.2.11",
            docker_bin=self.shim_bin,
        )
        self.assertTrue(ok)
        assert ok is True
        self.assertEqual(tail, "")
        assert tail == ""
        self.assertEqual(pngs, [])
        assert pngs == []
        task = apply_visual_gate(task, ok, tail, pngs)
        self.assertEqual(task["status"], "gate")
        assert task["status"] == "gate"
        self.assertNotIn("visual_artifacts", task)
        assert "visual_artifacts" not in task

        # Scenario B: whitespace string
        ok_ws, tail_ws, pngs_ws = visual.run_visual_gate(
            workdir=self.workdir,
            visual_cmd="   ",
            visual_image="sandbox-worker:1.2.11",
            docker_bin=self.shim_bin,
        )
        self.assertTrue(ok_ws)
        assert ok_ws is True
        self.assertEqual(tail_ws, "")
        assert tail_ws == ""
        self.assertEqual(pngs_ws, [])
        assert pngs_ws == []

        # Scenario C: None
        ok_none, tail_none, pngs_none = visual.run_visual_gate(
            workdir=self.workdir,
            visual_cmd=None,
            visual_image="sandbox-worker:1.2.11",
            docker_bin=self.shim_bin,
        )
        self.assertTrue(ok_none)
        assert ok_none is True
        self.assertEqual(tail_none, "")
        assert tail_none == ""
        self.assertEqual(pngs_none, [])
        assert pngs_none == []

    def test_visual_gate_on_and_passing(self):
        """2. On and passing: verify that when VISUAL_CMD exits 0 with PNGs in /out, task passes and artifacts attached."""
        task = {"id": "0321-pass", "status": "gate", "attempts": 0, "history": []}

        with patch.dict(os.environ, {"FAKE_DOCKER_MODE": "PASS"}):
            ok, tail, pngs = visual.run_visual_gate(
                workdir=self.workdir,
                visual_cmd="npm run test:visual",
                visual_image="sandbox-worker:1.2.11",
                out_dir=self.out_dir,
                docker_bin=self.shim_bin,
            )

        self.assertTrue(ok)
        assert ok is True
        self.assertIn("Rendering complete", tail)
        assert "Rendering complete" in tail
        self.assertIn("OK", tail)
        assert "OK" in tail
        self.assertEqual(pngs, ["diff.png", "screen.png"])
        assert pngs == ["diff.png", "screen.png"]
        self.assertNotIn("report.txt", pngs)
        assert "report.txt" not in pngs

        # Task integration check: task passes and artifacts attached
        task = apply_visual_gate(task, ok, tail, pngs)
        self.assertEqual(task["status"], "gate")
        assert task["status"] == "gate"
        self.assertTrue(task.get("visual_gate_passed"))
        assert task.get("visual_gate_passed") is True
        self.assertEqual(task.get("visual_artifacts"), ["diff.png", "screen.png"])
        assert task.get("visual_artifacts") == ["diff.png", "screen.png"]

    def test_visual_gate_failing_triggers_rework(self):
        """3. Failing: verify that when VISUAL_CMD exits non-zero, gate fails, rework is triggered, and output tail returned."""
        task = {"id": "0321-fail", "status": "gate", "attempts": 0, "history": []}

        with patch.dict(os.environ, {"FAKE_DOCKER_MODE": "FAIL"}):
            ok, tail, pngs = visual.run_visual_gate(
                workdir=self.workdir,
                visual_cmd="npm run test:visual",
                visual_image="sandbox-worker:1.2.11",
                docker_bin=self.shim_bin,
            )

        self.assertFalse(ok)
        assert ok is False
        self.assertEqual(pngs, [])
        assert pngs == []

        # Output tail verification: capped at 60 lines and preserves diagnostic tail
        tail_lines = tail.splitlines()
        self.assertLessEqual(len(tail_lines), 60)
        assert len(tail_lines) <= 60
        self.assertEqual(len(tail_lines), 60)
        assert len(tail_lines) == 60
        self.assertEqual(tail_lines[-1], "Error: visual regression detected: layout mismatch on #main-view")
        assert tail_lines[-1] == "Error: visual regression detected: layout mismatch on #main-view"
        self.assertIn("Diagnostic trace line 75", tail)
        assert "Diagnostic trace line 75" in tail
        self.assertIn("Diagnostic trace line 17", tail)
        assert "Diagnostic trace line 17" in tail
        self.assertNotIn("Diagnostic trace line 1:", tail)
        assert "Diagnostic trace line 1:" not in tail

        # Rework triggering check: status set to pending, attempts counted, history recorded
        task = apply_visual_gate(task, ok, tail, pngs)
        self.assertEqual(task["status"], "pending")
        assert task["status"] == "pending"
        self.assertEqual(task["attempts"], 1)
        assert task["attempts"] == 1
        self.assertTrue(task.get("visual_gate_failed"))
        assert task.get("visual_gate_failed") is True
        self.assertEqual(len(task["history"]), 1)
        assert len(task["history"]) == 1
        self.assertEqual(task["history"][0]["outcome"], "rework")
        assert task["history"][0]["outcome"] == "rework"
        self.assertIn("Error: visual regression detected", task["history"][0]["feedback"])
        assert "Error: visual regression detected" in task["history"][0]["feedback"]

    def test_visual_gate_missing_image_errors_immediately(self):
        """4. Missing image: verify that when docker image does not exist locally, gate errors out immediately."""
        task = {"id": "0321-missing", "status": "gate", "attempts": 0, "history": []}

        # Uses tools/fake-docker image inspect directly, which returns exit 1 for missing image
        with patch.dict(os.environ, {"FAKE_DOCKER_MODE": "MISSING_IMAGE"}):
            ok, tail, pngs = visual.run_visual_gate(
                workdir=self.workdir,
                visual_cmd="npm test",
                visual_image="missing-image:1.0",
                docker_bin=self.shim_bin,
            )

        self.assertFalse(ok)
        assert ok is False
        self.assertEqual(pngs, [])
        assert pngs == []
        self.assertEqual(tail, "Visual gate error: image 'missing-image:1.0' not found locally")
        assert tail == "Visual gate error: image 'missing-image:1.0' not found locally"

        # Confirm gate does not silently pass
        task = apply_visual_gate(task, ok, tail, pngs)
        self.assertEqual(task["status"], "pending")
        assert task["status"] == "pending"
        self.assertTrue(task.get("visual_gate_failed"))
        assert task.get("visual_gate_failed") is True
        self.assertFalse(task.get("visual_gate_passed", False))
        assert not task.get("visual_gate_passed", False)

    def test_visual_gate_timeout_terminates_container(self):
        """5. Timeout: verify that container exceeding VISUAL_TIMEOUT_SEC is terminated with a timeout error."""
        task = {"id": "0321-timeout", "status": "gate", "attempts": 0, "history": []}

        with patch.dict(os.environ, {"FAKE_DOCKER_MODE": "TIMEOUT"}):
            ok, tail, pngs = visual.run_visual_gate(
                workdir=self.workdir,
                visual_cmd="sleep 10",
                visual_image="sandbox-worker:1.2.11",
                timeout_sec=1,
                docker_bin=self.shim_bin,
            )

        self.assertFalse(ok)
        assert ok is False
        self.assertEqual(pngs, [])
        assert pngs == []
        self.assertEqual(tail, "Visual gate timed out after 1s")
        assert tail == "Visual gate timed out after 1s"

        # Confirm timeout triggers failure and rework
        task = apply_visual_gate(task, ok, tail, pngs)
        self.assertEqual(task["status"], "pending")
        assert task["status"] == "pending"
        self.assertTrue(task.get("visual_gate_failed"))
        assert task.get("visual_gate_failed") is True
        self.assertIn("timed out after 1s", task["history"][0]["feedback"])
        assert "timed out after 1s" in task["history"][0]["feedback"]

    def test_visual_gate_invalid_workdir_fails_safe(self):
        """Security: empty workdir fails safe before invoking container."""
        ok, tail, pngs = visual.run_visual_gate(
            workdir="",
            visual_cmd="npm test",
            visual_image="sandbox-worker:1.2.11",
            docker_bin=self.shim_bin,
        )
        self.assertFalse(ok)
        assert ok is False
        self.assertIn("workdir is required", tail)
        assert "workdir is required" in tail
        self.assertEqual(pngs, [])
        assert pngs == []


if __name__ == "__main__":
    unittest.main()
