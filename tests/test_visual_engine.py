"""Unit tests for the visual gate runner module (bin/lib/visual.py).

Public interface: pytest tests/test_visual_engine.py or python3 -m unittest tests/test_visual_engine.py.
Invariants:
- Empty or whitespace visual_cmd returns immediately without invoking docker.
- Missing docker image returns error naming the image.
- Passing container execution collects sorted PNG basenames from out_dir.
- Failing container execution returns the trailing ~60 lines of combined output.
- Subprocess timeout returns timeout error.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
bin_lib = os.path.join(ROOT, "bin", "lib")
if bin_lib not in sys.path:
    sys.path.insert(0, bin_lib)

import visual


class TestVisualGateRunner(unittest.TestCase):
    """Test suite covering container execution, PNG collection, output tailing, and error handling."""

    def test_noop_when_visual_cmd_empty(self):
        with patch("subprocess.run") as mock_run:
            for empty_val in ("", None, "   "):
                ok, tail, pngs = visual.run_visual_gate(
                    workdir="/workspace", visual_cmd=empty_val, visual_image="worker:latest"
                )
                self.assertTrue(ok)
                self.assertEqual(tail, "")
                self.assertEqual(pngs, [])
            mock_run.assert_not_called()

    def test_image_inspect_failure(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="No such image")
            ok, err, pngs = visual.run_visual_gate(
                workdir="/workspace", visual_cmd="npm test", visual_image="missing-image:latest"
            )
            self.assertFalse(ok)
            self.assertIn("missing-image:latest", err)
            self.assertEqual(err, "Visual gate error: image 'missing-image:latest' not found locally")
            self.assertEqual(pngs, [])
            mock_run.assert_called_once_with(
                ["docker", "image", "inspect", "missing-image:latest"],
                capture_output=True,
                text=True,
                errors="replace",
            )

    def test_passing_run_with_png_collection(self):
        with tempfile.TemporaryDirectory() as out_dir:
            for fname, content in [("screen.png", b"png1"), ("modal.png", b"png2"), ("log.txt", b"text")]:
                with open(os.path.join(out_dir, fname), "wb") as f:
                    f.write(content)

            inspect_res = MagicMock(returncode=0)
            run_res = MagicMock(returncode=0, stdout="Rendering complete\nOK\n", stderr="")

            with patch("subprocess.run", side_effect=[inspect_res, run_res]) as mock_run:
                ok, tail, pngs = visual.run_visual_gate(
                    workdir="/workspace",
                    visual_cmd="npm run test:visual",
                    visual_image="visual-worker:1.0",
                    timeout_sec=120,
                    out_dir=out_dir,
                )
                self.assertTrue(ok)
                self.assertEqual(tail, "Rendering complete\nOK")
                self.assertEqual(pngs, ["modal.png", "screen.png"])
                self.assertEqual(mock_run.call_count, 2)
                run_cmd = mock_run.call_args_list[1][0][0]
                self.assertIn("--read-only", run_cmd)
                self.assertIn("--network", run_cmd)
                self.assertIn("none", run_cmd)

    def test_failing_run_returns_last_60_lines(self):
        output_lines = [f"diagnostic line {i}" for i in range(100)]
        inspect_res = MagicMock(returncode=0)
        run_res = MagicMock(returncode=1, stdout="\n".join(output_lines), stderr="Error: visual diff mismatch\n")

        with patch("subprocess.run", side_effect=[inspect_res, run_res]):
            ok, tail, pngs = visual.run_visual_gate(
                workdir="/workspace",
                visual_cmd="npm run test:visual",
                visual_image="visual-worker:1.0",
            )
            self.assertFalse(ok)
            self.assertEqual(pngs, [])
            tail_lines = tail.splitlines()
            self.assertLessEqual(len(tail_lines), 60)
            self.assertEqual(tail_lines[-1], "Error: visual diff mismatch")
            self.assertEqual(tail_lines[0], output_lines[100 - (60 - 1)])

    def test_timeout_expired(self):
        inspect_res = MagicMock(returncode=0)
        with patch("subprocess.run", side_effect=[inspect_res, subprocess.TimeoutExpired(cmd=["docker"], timeout=45), MagicMock(returncode=0)]) as run:
            ok, err, pngs = visual.run_visual_gate(
                workdir="/workspace",
                visual_cmd="npm run test:visual",
                visual_image="visual-worker:1.0",
                timeout_sec=45,
            )
            self.assertFalse(ok)
            self.assertEqual(err, "Visual gate timed out after 45s")
            self.assertEqual(pngs, [])
            run_cmd, kill_cmd = run.call_args_list[1][0][0], run.call_args_list[2][0][0]
            self.assertEqual(kill_cmd[:2], ["docker", "kill"])
            self.assertEqual(kill_cmd[2], run_cmd[run_cmd.index("--name") + 1])

    def test_container_is_locked_down(self):
        inspect_res = MagicMock(returncode=0)
        done = MagicMock(returncode=0, stdout="", stderr="")
        with patch("subprocess.run", side_effect=[inspect_res, done]) as run:
            visual.run_visual_gate("/workspace", "npm run test:visual", "visual-worker:1.0")
            cmd = run.call_args_list[1][0][0]
            for flag in ("--network", "--read-only", "--tmpfs", "--memory", "--cpus", "--pids-limit", "--cap-drop", "--security-opt"):
                self.assertIn(flag, cmd)
            self.assertEqual(cmd[cmd.index("--network") + 1], "none")
            self.assertEqual(cmd[cmd.index("--cap-drop") + 1], "ALL")
            self.assertTrue(any(a.endswith(":ro") for a in cmd))

    def test_temporary_directory_used_when_out_dir_none(self):
        inspect_res = MagicMock(returncode=0)

        def fake_run(cmd, **kwargs):
            if cmd[1:3] == ["image", "inspect"]:
                return inspect_res
            # Find the host output directory mounted to /out
            out_mount = [arg for arg in cmd if ":/out" in arg][0]
            host_out = out_mount.split(":")[0]
            with open(os.path.join(host_out, "auto.png"), "wb") as f:
                f.write(b"data")
            return MagicMock(returncode=0, stdout="Done", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            ok, tail, pngs = visual.run_visual_gate(
                workdir="/workspace",
                visual_cmd="take-screenshot",
                visual_image="visual-worker:1.0",
                out_dir=None,
            )
            self.assertTrue(ok)
            self.assertEqual(pngs, ["auto.png"])

    def test_the_output_directory_is_writable_for_the_container_user(self):
        """The container runs as another user than the foreman: a 0755 or 0700 /out would store no screenshot at all."""
        seen = {}

        def fake_run(cmd, **kwargs):
            if cmd[1:3] == ["image", "inspect"]:
                return MagicMock(returncode=0)
            host_out = [arg for arg in cmd if arg.endswith(":/out:rw")][0].split(":")[0]
            seen["mode"] = os.stat(host_out).st_mode & 0o777
            return MagicMock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as out_dir:
            os.chmod(out_dir, 0o755)
            with patch("subprocess.run", side_effect=fake_run):
                visual.run_visual_gate("/workspace", "take-screenshot", "visual-worker:1.0", out_dir=out_dir)
            self.assertEqual(seen["mode"], 0o777)
        with patch("subprocess.run", side_effect=fake_run):
            visual.run_visual_gate("/workspace", "take-screenshot", "visual-worker:1.0")
        self.assertEqual(seen["mode"], 0o777, "the temporary directory of a run without out_dir is 0700 by default")

    def test_invalid_workdir(self):
        ok, err, pngs = visual.run_visual_gate(
            workdir="", visual_cmd="npm test", visual_image="worker:1.0"
        )
        self.assertFalse(ok)
        self.assertIn("workdir is required", err)
        self.assertEqual(pngs, [])


class TestVisualCmdValidation(unittest.TestCase):
    def test_invalid_visual_cmd_raises(self):
        import sbx
        from unittest.mock import patch
        with patch.object(sbx, "project_conf", return_value={"VISUAL_CMD": "echo $(id)"}):
            with self.assertRaises(ValueError):
                sbx.visual_cmd()

    def test_valid_visual_cmd_passes(self):
        import sbx
        from unittest.mock import patch
        with patch.object(sbx, "project_conf", return_value={"VISUAL_CMD": "node check.js"}):
            self.assertEqual(sbx.visual_cmd(), "node check.js")


if __name__ == "__main__":
    unittest.main()
