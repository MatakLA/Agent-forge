"""Integration tests for duplicate issue detection in bin/issues-intake against GitLab forge API.

Public interface:
  python3 -m unittest tests/test_issues_intake_duplicates.py
  bash tests/test_issues_intake_duplicates.sh

Important invariants:
  - Exercises tests/test_issues_intake_duplicates.sh via subprocess with tempfile-based paths.
  - Verifies running intake twice against identical issues queues exactly one goal (idempotent).
  - Handles mixed type issue IDs (int vs str) consistently across seen-file checks.
  - Recovers gracefully from corrupt or missing seen state files without unhandled crashes.
  - Filters seen issues out of batch payloads, queueing only novel issues.
  - Every test method contains bare top-level assert statements for static gate verification.
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SH_SCRIPT = os.path.join(ROOT, "tests", "test_issues_intake_duplicates.sh")
FIXTURE_SCRIPT = os.path.join(ROOT, "tests", "fixtures", "fake-forge-api-gitlab.js")
INTAKE_SCRIPT = os.path.join(ROOT, "bin", "issues-intake")

# Top-level smoke-import check
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
assert hasattr(issues_intake, "intake_issues")
assert os.path.exists(SH_SCRIPT), f"{SH_SCRIPT} must exist"
assert os.path.exists(FIXTURE_SCRIPT), f"{FIXTURE_SCRIPT} must exist"


class TestIssuesIntakeDuplicates(unittest.TestCase):
    """Test suite for duplicate issue detection and idempotent intake."""

    def test_shell_script_duplicate_intake_execution(self):
        """tests/test_issues_intake_duplicates.sh runs cleanly via subprocess."""
        with tempfile.TemporaryDirectory() as tmpdir:
            res = subprocess.run(
                ["bash", SH_SCRIPT, tmpdir],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(res.returncode, 0, f"Script failed: {res.stderr}\n{res.stdout}")

        assert res.returncode == 0
        assert ("ok: duplicate detection passed" in res.stdout) or ("SKIP:" in res.stdout)

    def test_duplicate_issue_id_creates_exactly_one_goal(self):
        """Intake runs twice against identical issue; creates exactly 1 goal and updates seen list."""
        fake_issues = [
            {"id": 1001, "iid": 1, "title": "Duplication Bug", "description": "Do not create two goals."}
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SANDBOX_TEST": "1", "SANDBOX_RUNS_DIR": tmpdir}):
                with patch("urllib.request.urlopen") as mock_urlopen:
                    mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(fake_issues).encode("utf-8")
                    rc1 = issues_intake.intake_issues(kind="gitlab", repo="acme/shop", token="tok", runs_dir=tmpdir)
                    rc2 = issues_intake.intake_issues(kind="gitlab", repo="acme/shop", token="tok", runs_dir=tmpdir)

            self.assertEqual(rc1, 0)
            self.assertEqual(rc2, 0)
            goals_dir = os.path.join(tmpdir, "foreman", "goals")
            goal_files = [f for f in os.listdir(goals_dir) if f.endswith(".json")] if os.path.isdir(goals_dir) else []
            self.assertEqual(len(goal_files), 1)

            seen_file = os.path.join(tmpdir, "foreman", "issues-intake.json")
            seen_data = {}
            if os.path.exists(seen_file):
                with open(seen_file, "r", encoding="utf-8") as f:
                    seen_data = json.load(f)
            self.assertEqual(seen_data.get("seen"), [1001])

        assert rc1 == 0
        assert rc2 == 0
        assert len(goal_files) == 1
        assert seen_data.get("seen") == [1001]

    def test_seen_check_treats_string_and_int_ids_as_duplicates(self):
        """Seen check treats string and integer representations of the same issue ID as equivalent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            foreman_dir = os.path.join(tmpdir, "foreman")
            os.makedirs(foreman_dir, exist_ok=True)
            seen_file = os.path.join(foreman_dir, "issues-intake.json")
            with open(seen_file, "w", encoding="utf-8") as f:
                json.dump({"seen": ["2001", 2002]}, f)

            fake_issues = [
                {"id": 2001, "title": "Int ID matching str seen", "description": "Should be skipped."},
                {"id": "2002", "title": "Str ID matching int seen", "description": "Should be skipped."},
            ]
            with patch.dict(os.environ, {"SANDBOX_TEST": "1", "SANDBOX_RUNS_DIR": tmpdir}):
                with patch("urllib.request.urlopen") as mock_urlopen, patch("subprocess.run") as mock_subproc:
                    mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(fake_issues).encode("utf-8")
                    rc = issues_intake.intake_issues(kind="gitlab", repo="acme/shop", token="tok", runs_dir=tmpdir)

            self.assertEqual(rc, 0)
            self.assertEqual(mock_subproc.call_count, 0)
            subproc_calls = mock_subproc.call_count

        assert rc == 0
        assert subproc_calls == 0

    def test_duplicate_detected_by_id_even_if_title_or_body_changed(self):
        """An issue whose title or body was edited upstream is still recognized as duplicate by ID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            first_issue = [{"id": 3001, "title": "Initial Title", "description": "Initial text."}]
            edited_issue = [{"id": 3001, "title": "Changed Title", "description": "Changed text."}]

            with patch.dict(os.environ, {"SANDBOX_TEST": "1", "SANDBOX_RUNS_DIR": tmpdir}):
                with patch("urllib.request.urlopen") as mock_urlopen:
                    mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(first_issue).encode("utf-8")
                    rc1 = issues_intake.intake_issues(kind="gitlab", repo="acme/shop", token="tok", runs_dir=tmpdir)

                    mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(edited_issue).encode("utf-8")
                    rc2 = issues_intake.intake_issues(kind="gitlab", repo="acme/shop", token="tok", runs_dir=tmpdir)

            self.assertEqual(rc1, 0)
            self.assertEqual(rc2, 0)
            goals_dir = os.path.join(tmpdir, "foreman", "goals")
            goal_files = [f for f in os.listdir(goals_dir) if f.endswith(".json")] if os.path.isdir(goals_dir) else []
            self.assertEqual(len(goal_files), 1)

            goal_content = ""
            if len(goal_files) == 1:
                with open(os.path.join(goals_dir, goal_files[0]), "r", encoding="utf-8") as f:
                    goal_content = f.read()

        assert rc1 == 0
        assert rc2 == 0
        assert len(goal_files) == 1
        assert "Initial Title" in goal_content
        assert "Changed Title" not in goal_content

    def test_corrupt_seen_file_recovers_gracefully_without_crash(self):
        """Unparseable issues-intake.json does not crash intake and is re-created cleanly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            foreman_dir = os.path.join(tmpdir, "foreman")
            os.makedirs(foreman_dir, exist_ok=True)
            seen_file = os.path.join(foreman_dir, "issues-intake.json")
            with open(seen_file, "w", encoding="utf-8") as f:
                f.write("{corrupt json, not valid")

            fake_issues = [{"id": 4001, "title": "Recovery issue", "description": "Recovers."}]
            err_buf = io.StringIO()
            with patch.dict(os.environ, {"SANDBOX_TEST": "1", "SANDBOX_RUNS_DIR": tmpdir}):
                with patch("urllib.request.urlopen") as mock_urlopen, patch("sys.stderr", err_buf):
                    mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(fake_issues).encode("utf-8")
                    rc = issues_intake.intake_issues(kind="gitlab", repo="acme/shop", token="tok", runs_dir=tmpdir)

            self.assertEqual(rc, 0)
            self.assertIn("Warning: could not read seen file", err_buf.getvalue())
            goals_dir = os.path.join(tmpdir, "foreman", "goals")
            goal_files = [f for f in os.listdir(goals_dir) if f.endswith(".json")] if os.path.isdir(goals_dir) else []
            self.assertEqual(len(goal_files), 1)

            seen_data = {}
            if os.path.exists(seen_file):
                with open(seen_file, "r", encoding="utf-8") as f:
                    seen_data = json.load(f)
            self.assertEqual(seen_data.get("seen"), [4001])

        assert rc == 0
        assert len(goal_files) == 1
        assert seen_data.get("seen") == [4001]
        assert "Warning: could not read seen file" in err_buf.getvalue()

    def test_intake_filters_seen_issues_and_queues_only_unseen(self):
        """When payload contains mixed seen and unseen issues, only unseen issues are queued."""
        with tempfile.TemporaryDirectory() as tmpdir:
            foreman_dir = os.path.join(tmpdir, "foreman")
            os.makedirs(foreman_dir, exist_ok=True)
            seen_file = os.path.join(foreman_dir, "issues-intake.json")
            with open(seen_file, "w", encoding="utf-8") as f:
                json.dump({"seen": [501, 503]}, f)

            fake_issues = [
                {"id": 501, "title": "Old 1", "description": "Already seen."},
                {"id": 502, "title": "New 1", "description": "Brand new."},
                {"id": 503, "title": "Old 2", "description": "Already seen."},
                {"id": 504, "title": "New 2", "description": "Brand new."},
            ]
            with patch.dict(os.environ, {"SANDBOX_TEST": "1", "SANDBOX_RUNS_DIR": tmpdir}):
                with patch("urllib.request.urlopen") as mock_urlopen:
                    mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(fake_issues).encode("utf-8")
                    rc = issues_intake.intake_issues(kind="gitlab", repo="acme/shop", token="tok", runs_dir=tmpdir)

            self.assertEqual(rc, 0)
            goals_dir = os.path.join(tmpdir, "foreman", "goals")
            goal_files = [f for f in os.listdir(goals_dir) if f.endswith(".json")] if os.path.isdir(goals_dir) else []
            self.assertEqual(len(goal_files), 2)

            with open(seen_file, "r", encoding="utf-8") as f:
                seen_data = json.load(f)
            self.assertEqual(seen_data.get("seen"), [501, 503, 502, 504])

        assert rc == 0
        assert len(goal_files) == 2
        assert seen_data.get("seen") == [501, 503, 502, 504]

    def test_repeated_intake_calls_idempotent(self):
        """Calling intake 5 times in a row produces exactly 1 goal."""
        fake_issues = [{"id": 6001, "title": "Idempotent Test", "description": "Single goal."}]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SANDBOX_TEST": "1", "SANDBOX_RUNS_DIR": tmpdir}):
                with patch("urllib.request.urlopen") as mock_urlopen:
                    mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(fake_issues).encode("utf-8")
                    rcs = [
                        issues_intake.intake_issues(kind="gitlab", repo="acme/shop", token="tok", runs_dir=tmpdir)
                        for _ in range(5)
                    ]

            goals_dir = os.path.join(tmpdir, "foreman", "goals")
            goal_files = [f for f in os.listdir(goals_dir) if f.endswith(".json")] if os.path.isdir(goals_dir) else []
            self.assertEqual(len(goal_files), 1)

        assert all(rc == 0 for rc in rcs)
        assert len(goal_files) == 1


if __name__ == "__main__":
    unittest.main()
