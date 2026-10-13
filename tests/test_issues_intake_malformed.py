"""Tests for bin/issues-intake malformed webhook payload rejection.

Public interface:
  python3 -m unittest tests/test_issues_intake_malformed.py

Invariants:
  - Validates untrusted external webhook input offline (no real forge reached).
  - Rejects non-JSON, missing required fields, wrong field types, control/NUL chars, and oversized input cleanly.
  - Exits non-zero with clear stderr message, never leaking a raw Python traceback.
  - Never creates a goal file or updates issues-intake.json seen list on malformed input.
  - Drives tests/test_issues_intake_malformed.sh and verifies each of the 5 malformed payload cases.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

# Top-level smoke-import check to prevent silent import skips in automated gates
try:
    import importlib.machinery
    import importlib.util
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INTAKE_BIN = os.path.join(ROOT, "bin", "issues-intake")
MALFORMED_SH = os.path.join(ROOT, "tests", "test_issues_intake_malformed.sh")

assert os.path.isfile(INTAKE_BIN)
assert os.path.isfile(MALFORMED_SH)


class TestIssuesIntakeMalformed(unittest.TestCase):
    """Test suite verifying that bin/issues-intake cleanly rejects malformed payloads."""

    def _execute_intake(self, payload_content, extra_args=None):
        """Helper to run bin/issues-intake against a payload in an isolated temporary directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            payload_path = os.path.join(tmpdir, "payload.json")
            if isinstance(payload_content, bytes):
                with open(payload_path, "wb") as f:
                    f.write(payload_content)
            else:
                with open(payload_path, "w", encoding="utf-8") as f:
                    f.write(payload_content)

            runs_dir = os.path.join(tmpdir, "runs")
            goals_dir = os.path.join(runs_dir, "foreman", "goals")
            os.makedirs(goals_dir, exist_ok=True)

            cmd = [INTAKE_BIN]
            if extra_args:
                cmd.extend(extra_args)
            cmd.append(payload_path)

            env = dict(os.environ, SANDBOX_TEST="1", SANDBOX_RUNS_DIR=runs_dir)
            proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=15)

            goal_files = [f for f in os.listdir(goals_dir) if f.endswith(".json")]
            seen_file = os.path.join(runs_dir, "foreman", "issues-intake.json")
            seen_exists = os.path.exists(seen_file)

            return proc.returncode, proc.stdout, proc.stderr, goal_files, seen_exists

    def test_malformed_shell_suite(self):
        """Integration check: tests/test_issues_intake_malformed.sh passes with ALL OK."""
        r = subprocess.run(["bash", MALFORMED_SH], capture_output=True, text=True, cwd=ROOT, timeout=30)
        self.assertEqual(r.returncode, 0, f"Malformed shell suite failed: {r.stderr}")
        self.assertIn("ALL OK", r.stdout)
        assert r.returncode == 0
        assert "ALL OK" in r.stdout

    def test_case1_not_valid_json(self):
        """Case 1: not valid JSON at all produces non-zero exit, clear stderr error, and no goals."""
        rc, out, err, goals, seen = self._execute_intake("not valid json { [ at all")
        self.assertNotEqual(rc, 0)
        self.assertNotIn("Traceback (most recent call last)", err)
        self.assertIn("invalid JSON", err)
        self.assertEqual(len(goals), 0)
        self.assertFalse(seen)
        assert rc != 0
        assert "Traceback (most recent call last)" not in err
        assert "invalid JSON" in err
        assert len(goals) == 0
        assert not seen

    def test_case2_missing_required_field(self):
        """Case 2: valid JSON missing required fields ('title' or 'labels') is rejected cleanly."""
        # 2a: Missing title
        no_title = json.dumps({"id": 201, "body": "description without title", "labels": ["agent-forge"]})
        rc1, out1, err1, goals1, seen1 = self._execute_intake(no_title)
        self.assertNotEqual(rc1, 0)
        self.assertNotIn("Traceback (most recent call last)", err1)
        self.assertIn("missing required field 'title'", err1)
        self.assertEqual(len(goals1), 0)
        self.assertFalse(seen1)

        # 2b: Missing labels
        no_labels = json.dumps({"id": 202, "title": "Issue without labels", "body": "description"})
        rc2, out2, err2, goals2, seen2 = self._execute_intake(no_labels)
        self.assertNotEqual(rc2, 0)
        self.assertNotIn("Traceback (most recent call last)", err2)
        self.assertIn("missing required field 'labels'", err2)
        self.assertEqual(len(goals2), 0)
        self.assertFalse(seen2)

        assert rc1 != 0
        assert rc2 != 0
        assert "missing required field 'title'" in err1
        assert "missing required field 'labels'" in err2
        assert len(goals1) == 0
        assert len(goals2) == 0

    def test_case3_wrong_field_type(self):
        """Case 3: required fields with wrong types (labels as string, id as non-numeric string) are rejected."""
        # 3a: labels as string instead of list
        str_labels = json.dumps({"id": 301, "title": "Wrong label type", "labels": "agent-forge"})
        rc1, out1, err1, goals1, seen1 = self._execute_intake(str_labels)
        self.assertNotEqual(rc1, 0)
        self.assertNotIn("Traceback (most recent call last)", err1)
        self.assertIn("invalid type for 'labels'", err1)
        self.assertEqual(len(goals1), 0)
        self.assertFalse(seen1)

        # 3b: id as non-numeric garbage
        garbage_id = json.dumps({"id": "non-numeric-garbage-id", "title": "Garbage ID", "labels": ["agent-forge"]})
        rc2, out2, err2, goals2, seen2 = self._execute_intake(garbage_id)
        self.assertNotEqual(rc2, 0)
        self.assertNotIn("Traceback (most recent call last)", err2)
        self.assertIn("invalid type for 'id'", err2)
        self.assertEqual(len(goals2), 0)
        self.assertFalse(seen2)

        assert rc1 != 0
        assert rc2 != 0
        assert "invalid type for 'labels'" in err1
        assert "invalid type for 'id'" in err2
        assert len(goals1) == 0
        assert len(goals2) == 0

    def test_case4_control_characters_or_nul_byte(self):
        """Case 4: title or body containing control characters or embedded NUL byte is rejected cleanly."""
        # 4a: embedded NUL byte in title
        nul_title = '{"id": 401, "title": "evil\\u0000title", "labels": ["agent-forge"]}'
        rc1, out1, err1, goals1, seen1 = self._execute_intake(nul_title)
        self.assertNotEqual(rc1, 0)
        self.assertNotIn("Traceback (most recent call last)", err1)
        self.assertIn("control characters or NUL byte", err1)
        self.assertEqual(len(goals1), 0)
        self.assertFalse(seen1)

        # 4b: ANSI escape sequences and bell control characters
        ctrl_title = '{"id": 402, "title": "escape\\u001b[31mcolor\\u0007bell", "labels": ["agent-forge"]}'
        rc2, out2, err2, goals2, seen2 = self._execute_intake(ctrl_title)
        self.assertNotEqual(rc2, 0)
        self.assertNotIn("Traceback (most recent call last)", err2)
        self.assertIn("control characters or NUL byte", err2)
        self.assertEqual(len(goals2), 0)
        self.assertFalse(seen2)

        # 4c: raw unescaped NUL byte in payload stream
        raw_nul = b'{"id": 403, "title": "raw\x00nul", "labels": ["agent-forge"]}'
        rc3, out3, err3, goals3, seen3 = self._execute_intake(raw_nul)
        self.assertNotEqual(rc3, 0)
        self.assertNotIn("Traceback (most recent call last)", err3)
        self.assertEqual(len(goals3), 0)
        self.assertFalse(seen3)

        assert rc1 != 0
        assert rc2 != 0
        assert rc3 != 0
        assert "control characters or NUL byte" in err1
        assert "control characters or NUL byte" in err2
        assert len(goals1) == 0
        assert len(goals2) == 0
        assert len(goals3) == 0

    def test_case5_oversized_payload(self):
        """Case 5: oversized payload (multi-MB title) rejects bounded input without hanging or crashing."""
        oversized = json.dumps({"id": 501, "title": "A" * 2000000, "labels": ["agent-forge"]})
        rc, out, err, goals, seen = self._execute_intake(oversized)
        self.assertNotEqual(rc, 0)
        self.assertNotIn("Traceback (most recent call last)", err)
        self.assertIn("exceeds", err)
        self.assertEqual(len(goals), 0)
        self.assertFalse(seen)
        assert rc != 0
        assert "Traceback (most recent call last)" not in err
        assert "exceeds" in err
        assert len(goals) == 0
        assert not seen

    def test_webhook_wrapper_envelopes(self):
        """Webhook envelope shapes (GitHub and GitLab) are unwrapped and validated cleanly."""
        gh_missing_title = json.dumps({
            "action": "opened",
            "issue": {"id": 601, "body": "only body in webhook", "labels": ["agent-forge"]}
        })
        rc1, out1, err1, goals1, seen1 = self._execute_intake(gh_missing_title)
        self.assertNotEqual(rc1, 0)
        self.assertNotIn("Traceback (most recent call last)", err1)
        self.assertIn("missing required field 'title'", err1)
        self.assertEqual(len(goals1), 0)

        gl_nul = '{"object_kind": "issue", "object_attributes": {"id": 602, "title": "gl\\u0000nul"}, "labels": [{"title": "agent-forge"}]}'
        rc2, out2, err2, goals2, seen2 = self._execute_intake(gl_nul)
        self.assertNotEqual(rc2, 0)
        self.assertNotIn("Traceback (most recent call last)", err2)
        self.assertIn("control characters or NUL byte", err2)
        self.assertEqual(len(goals2), 0)

        assert rc1 != 0
        assert rc2 != 0
        assert "missing required field 'title'" in err1
        assert "control characters or NUL byte" in err2
        assert len(goals1) == 0
        assert len(goals2) == 0


if __name__ == "__main__":
    unittest.main()
