"""Tests for bin/issues-intake GitHub/GitLab goal-intake bridge.

Verifies:
- Fresh issues labelled agent-forge are queued to foreman and persisted to seen list.
- Previously seen issues are skipped and not re-queued.
- --dry-run outputs would-be queued goals without side effects.
- FORGE_REPO validation fails safe on invalid repository formats.
- HTML tags in issue body are cleanly stripped before queueing.
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INTAKE_SCRIPT = os.path.join(ROOT, "bin", "issues-intake")

# Import smoke check at top-level (prints traceback + exits 1 on ImportError)
try:
    _loader = importlib.machinery.SourceFileLoader("issues_intake", INTAKE_SCRIPT)
    _spec = importlib.util.spec_from_loader("issues_intake", _loader)
    issues_intake = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(issues_intake)
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)

assert issues_intake is not None
assert hasattr(issues_intake, "main")
assert hasattr(issues_intake, "REPO_RE")


class TestIssuesIntake(unittest.TestCase):
    """Test suite for bin/issues-intake."""

    def test_queues_new_issue(self):
        """A fresh issue with the label is queued; seen list is updated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_issues = [
                {"id": 101, "number": 1, "title": "Implement feature X", "body": "Please build feature X."}
            ]
            seen_file = os.path.join(tmpdir, "foreman", "issues-intake.json")
            with patch("urllib.request.urlopen") as mock_urlopen, \
                 patch("subprocess.run") as mock_subproc:
                mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(fake_issues).encode("utf-8")
                mock_subproc.return_value = MagicMock(returncode=0, stdout="queued G1\n", stderr="")

                rc = issues_intake.intake_issues(
                    kind="github",
                    repo="acme/shop",
                    token="dummy-token",
                    runs_dir=tmpdir,
                )
                self.assertEqual(rc, 0)
                self.assertEqual(mock_subproc.call_count, 1)
                call_args = mock_subproc.call_args
                self.assertEqual(call_args[0][0][-2:], ["goal", "-"])
                self.assertIn("Implement feature X\n\nPlease build feature X.", call_args[1]["input"])

                self.assertTrue(os.path.exists(seen_file))
                with open(seen_file, "r", encoding="utf-8") as f:
                    seen_data = json.load(f)
                self.assertIn(101, seen_data.get("seen", []))

            assert os.path.exists(seen_file)
            assert 101 in seen_data.get("seen", [])

    def test_skips_seen_issue(self):
        """An issue already in seen list is not re-queued."""
        with tempfile.TemporaryDirectory() as tmpdir:
            foreman_dir = os.path.join(tmpdir, "foreman")
            os.makedirs(foreman_dir, exist_ok=True)
            seen_file = os.path.join(foreman_dir, "issues-intake.json")
            with open(seen_file, "w", encoding="utf-8") as f:
                json.dump({"seen": [101]}, f)

            fake_issues = [
                {"id": 101, "number": 1, "title": "Implement feature X", "body": "Please build feature X."}
            ]
            with patch("urllib.request.urlopen") as mock_urlopen, \
                 patch("subprocess.run") as mock_subproc:
                mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(fake_issues).encode("utf-8")

                rc = issues_intake.intake_issues(
                    kind="github",
                    repo="acme/shop",
                    token="dummy-token",
                    runs_dir=tmpdir,
                )
                self.assertEqual(rc, 0)
                self.assertEqual(mock_subproc.call_count, 0)

            assert mock_subproc.call_count == 0

    def test_dry_run_no_side_effects(self):
        """--dry-run prints output but does not write the seen file or call foreman."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_issues = [
                {"id": 202, "number": 2, "title": "Dry run test", "body": "Body of dry run."}
            ]
            seen_file = os.path.join(tmpdir, "foreman", "issues-intake.json")
            buf = io.StringIO()
            with patch("urllib.request.urlopen") as mock_urlopen, \
                 patch("subprocess.run") as mock_subproc, \
                 patch("sys.stdout", buf):
                mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(fake_issues).encode("utf-8")

                rc = issues_intake.intake_issues(
                    kind="github",
                    repo="acme/shop",
                    token="dummy-token",
                    runs_dir=tmpdir,
                    dry_run=True,
                )
                self.assertEqual(rc, 0)
                self.assertEqual(mock_subproc.call_count, 0)
                self.assertFalse(os.path.exists(seen_file))
                out = buf.getvalue()
                self.assertIn("Dry run test", out)

            assert not os.path.exists(seen_file)
            assert mock_subproc.call_count == 0
            assert "Dry run test" in out

    def test_repo_validation(self):
        """An invalid FORGE_REPO causes exit 1 without a network call."""
        buf = io.StringIO()
        with patch("urllib.request.urlopen") as mock_urlopen, patch("sys.stderr", buf):
            with patch.dict(os.environ, {"FORGE_KIND": "github", "FORGE_REPO": "invalid_repo_no_slash", "FORGE_UPSTREAM_TOKEN": "tok"}):
                with self.assertRaises(SystemExit) as ctx:
                    issues_intake.main([])
                self.assertEqual(ctx.exception.code, 1)
                self.assertEqual(mock_urlopen.call_count, 0)
                self.assertIn("Invalid FORGE_REPO", buf.getvalue())

        assert mock_urlopen.call_count == 0
        assert ctx.exception.code == 1
        assert "Invalid FORGE_REPO" in buf.getvalue()

    def test_html_strip(self):
        """Issue body with <b>bold</b> text is stripped to bold in the goal string."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_issues = [
                {"id": 303, "number": 3, "title": "Style issue", "body": "Text with <b>bold</b> and <p>tags</p>."}
            ]
            with patch("urllib.request.urlopen") as mock_urlopen, \
                 patch("subprocess.run") as mock_subproc:
                mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(fake_issues).encode("utf-8")
                mock_subproc.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

                rc = issues_intake.intake_issues(
                    kind="github",
                    repo="acme/shop",
                    token="dummy-token",
                    runs_dir=tmpdir,
                )
                self.assertEqual(rc, 0)
                call_args = mock_subproc.call_args
                goal_input = call_args[1]["input"]
                self.assertIn("bold", goal_input)
                self.assertNotIn("<b>", goal_input)
                self.assertNotIn("</b>", goal_input)
                self.assertNotIn("<p>", goal_input)

            assert "<b>" not in goal_input
            assert "bold" in goal_input


if __name__ == "__main__":
    unittest.main()
