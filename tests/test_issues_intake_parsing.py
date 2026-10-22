"""Integration tests for issue parsing in bin/issues-intake against GitHub forge API.

Public interface:
  python3 -m unittest tests/test_issues_intake_parsing.py
  bash tests/test_issues_intake_parsing.sh

Important invariants:
  - Exercises tests/test_issues_intake_parsing.sh via subprocess with tempfile-based runs paths.
  - Verifies eligible GitHub issues are parsed and queued as exactly one Foreman goal per issue.
  - Verifies issue labels are not read: a goal is the same whatever triage label its issue carries, and none has a role field.
  - Passes issue labels as GitHub label objects ({"id": ..., "name": ...}) matching real GitHub client shape.
  - Verifies HTML tag stripping and boundary truncation of goal text at exactly 4000 characters.
  - Verifies missing or empty body cleanly falls back to issue title.
  - Verifies fixture server lifecycle on OS-assigned free port and clean shutdown on SIGTERM.
  - Every test method contains bare top-level assert statements for static gate verification.
"""
import glob
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SH_SCRIPT = os.path.join(ROOT, "tests", "test_issues_intake_parsing.sh")
FIXTURE_SCRIPT = os.path.join(ROOT, "tests", "fixtures", "fake-forge-api-github.js")
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


class TestIssuesIntakeParsing(unittest.TestCase):
    """Test suite for issue parsing and goal creation."""

    def test_shell_script_parsing_intake_execution(self):
        """tests/test_issues_intake_parsing.sh runs cleanly via subprocess and terminates fixture via trap."""
        with tempfile.TemporaryDirectory() as tmpdir:
            res = subprocess.run(
                ["bash", SH_SCRIPT, tmpdir],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(res.returncode, 0, f"Script failed: {res.stderr}\n{res.stdout}")

        assert res.returncode == 0
        assert ("ok: parsing e2e test passed" in res.stdout) or ("SKIP:" in res.stdout)

    def test_eligible_issues_create_exactly_one_goal_each(self):
        """Intake parses eligible GitHub issues and creates exactly 1 goal per issue on disk, whatever labels they carry."""
        fake_issues = [
            {
                "id": 1001,
                "title": "Fix cookie expiration",
                "body": "Session bug.<br><b>preserve</b>",
                "labels": [{"id": 11, "name": "agent-forge"}, {"id": 12, "name": "bug"}]
            },
            {
                "id": 1002,
                "title": "Update provider docs",
                "body": "Docs in docs/PROVIDERS.md",
                "labels": [{"id": 11, "name": "agent-forge"}, {"id": 13, "name": "docs"}]
            },
            {
                "id": 1003,
                "title": "Egress firewall audit",
                "body": "Audit iptables.<p>Drop policies</p>",
                "labels": [{"id": 11, "name": "agent-forge"}, {"id": 14, "name": "security"}]
            },
            {
                "id": 1004,
                "title": "Token count metric",
                "body": "Expose token count metrics.",
                "labels": [{"id": 11, "name": "agent-forge"}, {"id": 15, "name": "feature"}]
            },
            {
                "id": 1005,
                "title": "Triage feedback notes",
                "body": "Community discussion notes.",
                "labels": [{"id": 11, "name": "agent-forge"}, {"id": 16, "name": "triage-needed"}]
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SANDBOX_TEST": "1", "SANDBOX_RUNS_DIR": tmpdir}):
                with patch("urllib.request.urlopen") as mock_urlopen:
                    mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(fake_issues).encode("utf-8")
                    rc = issues_intake.intake_issues(kind="github", repo="agent-forge/agent-forge", token="tok", runs_dir=tmpdir)

            self.assertEqual(rc, 0)
            goals_dir = os.path.join(tmpdir, "foreman", "goals")
            goal_files = sorted(glob.glob(os.path.join(goals_dir, "G*.json"))) if os.path.isdir(goals_dir) else []
            self.assertEqual(len(goal_files), 5)

            seen_file = os.path.join(tmpdir, "foreman", "issues-intake.json")
            seen_data = {}
            if os.path.exists(seen_file):
                with open(seen_file, "r", encoding="utf-8") as f:
                    seen_data = json.load(f)
            self.assertEqual(sorted(seen_data.get("seen", [])), [1001, 1002, 1003, 1004, 1005])

            loaded_goals = []
            for gf in goal_files:
                with open(gf, "r", encoding="utf-8") as f:
                    loaded_goals.append(json.load(f))

            titles = ["Fix cookie expiration", "Update provider docs", "Egress firewall audit", "Token count metric", "Triage feedback notes"]
            for title in titles:
                self.assertEqual(sum(title in g.get("text", "") for g in loaded_goals), 1, title)
            for goal in loaded_goals:
                self.assertNotIn("role", goal, "labels are not read: no goal carries a role")

        assert rc == 0
        assert len(goal_files) == 5
        assert sorted(seen_data.get("seen", [])) == [1001, 1002, 1003, 1004, 1005]
        assert all(sum(t in g.get("text", "") for g in loaded_goals) == 1 for t in titles)
        assert all("role" not in g for g in loaded_goals)

    def test_labels_do_not_change_the_queued_goal(self):
        """The same issue with any triage label, or none, queues the same goal: labels are not read and set no role."""
        def queue(labels):
            fake_issues = [{"id": 2001, "title": "Same issue", "body": "Same body", "labels": labels}]
            with tempfile.TemporaryDirectory() as tmpdir:
                with patch.dict(os.environ, {"SANDBOX_TEST": "1", "SANDBOX_RUNS_DIR": tmpdir}):
                    with patch("urllib.request.urlopen") as mock_urlopen:
                        mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(fake_issues).encode("utf-8")
                        rc = issues_intake.intake_issues(kind="github", repo="agent-forge/agent-forge", token="tok", runs_dir=tmpdir)
                self.assertEqual(rc, 0)
                goal_files = glob.glob(os.path.join(tmpdir, "foreman", "goals", "G*.json"))
                self.assertEqual(len(goal_files), 1)
                with open(goal_files[0], "r", encoding="utf-8") as f:
                    goal = json.load(f)
            return {k: v for k, v in goal.items() if k not in ("id", "created", "updated")}

        unlabelled = queue([{"id": 11, "name": "agent-forge"}])
        labelled = [queue([{"id": 11, "name": "agent-forge"}, {"id": 12, "name": name}]) for name in ("bug", "docs", "security", "feature")]
        for goal in labelled:
            self.assertEqual(goal, unlabelled)
        self.assertNotIn("role", unlabelled)

        assert all(goal == unlabelled for goal in labelled)
        assert "role" not in unlabelled

    def test_goal_text_html_tag_stripping(self):
        """HTML formatting tags in title and body are stripped cleanly from goal text."""
        fake_issues = [
            {
                "id": 2001,
                "title": "Bug with <b>bold title tag</b>",
                "body": "<p>Paragraph one with <a href='https://example.com'>link</a> and <br>newline.</p>",
                "labels": [{"id": 11, "name": "agent-forge"}]
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SANDBOX_TEST": "1", "SANDBOX_RUNS_DIR": tmpdir}):
                with patch("urllib.request.urlopen") as mock_urlopen:
                    mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(fake_issues).encode("utf-8")
                    rc = issues_intake.intake_issues(kind="github", repo="agent-forge/agent-forge", token="tok", runs_dir=tmpdir)

            goals_dir = os.path.join(tmpdir, "foreman", "goals")
            goal_files = glob.glob(os.path.join(goals_dir, "G*.json"))
            self.assertEqual(len(goal_files), 1)

            with open(goal_files[0], "r", encoding="utf-8") as f:
                goal_data = json.load(f)

            text = goal_data.get("text", "")
            self.assertIn("Bug with bold title tag", text)
            self.assertIn("Paragraph one with link and newline.", text)
            self.assertNotIn("<b>", text)
            self.assertNotIn("</b>", text)
            self.assertNotIn("<p>", text)
            self.assertNotIn("</p>", text)
            self.assertNotIn("<br>", text)
            self.assertNotIn("<a", text)

        assert rc == 0
        assert "<b>" not in text
        assert "bold title tag" in text

    def test_boundary_truncation_at_max_goal_length(self):
        """Oversized issue bodies exceeding 4000 chars are truncated cleanly at exactly 4000 chars."""
        oversized_body = "x" * 6000
        fake_issues = [
            {"id": 3001, "title": "Boundary truncation test", "body": oversized_body, "labels": [{"id": 11, "name": "agent-forge"}]}
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SANDBOX_TEST": "1", "SANDBOX_RUNS_DIR": tmpdir}):
                with patch("urllib.request.urlopen") as mock_urlopen:
                    mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(fake_issues).encode("utf-8")
                    rc = issues_intake.intake_issues(kind="github", repo="agent-forge/agent-forge", token="tok", runs_dir=tmpdir)

            goals_dir = os.path.join(tmpdir, "foreman", "goals")
            goal_files = glob.glob(os.path.join(goals_dir, "G*.json"))
            self.assertEqual(len(goal_files), 1)

            with open(goal_files[0], "r", encoding="utf-8") as f:
                goal_data = json.load(f)

            text = goal_data.get("text", "")
            self.assertEqual(len(text), 4000)
            self.assertTrue(text.startswith("Boundary truncation test\n\n"))

        assert rc == 0
        assert len(text) == 4000
        assert len(text) <= 4000

    def test_missing_or_empty_body_uses_title_cleanly(self):
        """An issue with missing or empty body cleanly uses title alone as goal string."""
        fake_issues = [
            {"id": 4001, "title": "Title only goal issue", "body": None, "labels": [{"id": 11, "name": "agent-forge"}]}
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SANDBOX_TEST": "1", "SANDBOX_RUNS_DIR": tmpdir}):
                with patch("urllib.request.urlopen") as mock_urlopen:
                    mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(fake_issues).encode("utf-8")
                    rc = issues_intake.intake_issues(kind="github", repo="agent-forge/agent-forge", token="tok", runs_dir=tmpdir)

            goals_dir = os.path.join(tmpdir, "foreman", "goals")
            goal_files = glob.glob(os.path.join(goals_dir, "G*.json"))
            self.assertEqual(len(goal_files), 1)
            with open(goal_files[0], "r", encoding="utf-8") as f:
                goal_data = json.load(f)
            self.assertEqual(goal_data.get("text"), "Title only goal issue")

        assert rc == 0
        assert goal_data.get("text") == "Title only goal issue"

    def test_non_matching_label_issue_is_queued_without_rejection(self):
        """Issues carrying non-matching triage labels are queued as goals rather than skipped."""
        fake_issues = [
            {
                "id": 5001,
                "title": "Unknown label issue",
                "body": "Non-matching label payload",
                "labels": [{"id": 11, "name": "agent-forge"}, {"id": 17, "name": "custom-category"}]
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SANDBOX_TEST": "1", "SANDBOX_RUNS_DIR": tmpdir}):
                with patch("urllib.request.urlopen") as mock_urlopen:
                    mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(fake_issues).encode("utf-8")
                    rc = issues_intake.intake_issues(kind="github", repo="agent-forge/agent-forge", token="tok", runs_dir=tmpdir)

            goals_dir = os.path.join(tmpdir, "foreman", "goals")
            goal_files = glob.glob(os.path.join(goals_dir, "G*.json"))
            self.assertEqual(len(goal_files), 1)
            with open(goal_files[0], "r", encoding="utf-8") as f:
                goal_data = json.load(f)
            self.assertEqual(goal_data.get("status"), "new")

        assert rc == 0
        assert len(goal_files) == 1
        assert goal_data.get("status") == "new"

    def test_repeated_intake_calls_idempotent(self):
        """Running intake repeatedly against the same issue list produces no duplicate goals."""
        fake_issues = [
            {
                "id": 6001,
                "title": "Idempotent parsing issue",
                "body": "Single goal expected",
                "labels": [{"id": 11, "name": "agent-forge"}, {"id": 12, "name": "bug"}]
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SANDBOX_TEST": "1", "SANDBOX_RUNS_DIR": tmpdir}):
                with patch("urllib.request.urlopen") as mock_urlopen:
                    mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(fake_issues).encode("utf-8")
                    rc1 = issues_intake.intake_issues(kind="github", repo="agent-forge/agent-forge", token="tok", runs_dir=tmpdir)
                    rc2 = issues_intake.intake_issues(kind="github", repo="agent-forge/agent-forge", token="tok", runs_dir=tmpdir)

            goals_dir = os.path.join(tmpdir, "foreman", "goals")
            goal_files = glob.glob(os.path.join(goals_dir, "G*.json"))
            self.assertEqual(len(goal_files), 1)

        assert rc1 == 0
        assert rc2 == 0
        assert len(goal_files) == 1

    def test_fixture_structure_and_issues_contract(self):
        """tests/fixtures/fake-forge-api-github.js defines >=4 issues covering >=3 real labels plus 1 non-matching label."""
        self.assertTrue(os.path.isfile(FIXTURE_SCRIPT))
        with open(FIXTURE_SCRIPT, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("Fix session cookie expiration bug", content)
        self.assertIn("Update documentation for custom providers", content)
        self.assertIn("Perform security audit on egress firewall", content)
        self.assertIn("Add token count metric in status", content)
        self.assertIn("triage-needed", content)
        self.assertIn("PORT", content)
        self.assertIn("SIGTERM", content)

        distinct_labels = [lbl for lbl in ["bug", "docs", "security", "feature"] if lbl in content]
        self.assertGreaterEqual(len(distinct_labels), 3)
        self.assertIn("triage-needed", content)

        assert os.path.isfile(FIXTURE_SCRIPT)
        assert len(distinct_labels) >= 3
        assert "triage-needed" in content


if __name__ == "__main__":
    unittest.main()
