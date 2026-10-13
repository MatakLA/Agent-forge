"""Tests for bin/issues-intake malformed webhook payload and polled issue rejection.

Public interface:
  python3 -m unittest tests/test_issues_intake_malformed.py

Invariants:
  - Validates untrusted external webhook input and polled forge issue data offline (no real forge reached).
  - Rejects non-JSON, missing required fields, wrong field types, control/NUL chars, and oversized input cleanly.
  - Exits non-zero with clear stderr message, never leaking a raw Python traceback.
  - Never creates a goal file or updates issues-intake.json seen list on malformed input.
  - Drives tests/test_issues_intake_malformed.sh and verifies each of the 5 malformed payload cases.
  - Verifies normal success operation of the payload_file CLI argument (single goal creation and idempotency).
  - Verifies real poll flow rejects malformed forge responses identically via mocks and a local HTTP server.
  - Every test method contains at least one bare top-level assert statement.
"""
import http.server
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

# Top-level smoke-import check to prevent silent import skips in automated gates
try:
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    INTAKE_BIN = os.path.join(ROOT, "bin", "issues-intake")
    MALFORMED_SH = os.path.join(ROOT, "tests", "test_issues_intake_malformed.sh")

    _loader = importlib.machinery.SourceFileLoader("issues_intake", INTAKE_BIN)
    _spec = importlib.util.spec_from_loader("issues_intake", _loader)
    issues_intake = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(issues_intake)
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)

assert os.path.isfile(INTAKE_BIN)
assert os.path.isfile(MALFORMED_SH)
assert issues_intake is not None
assert hasattr(issues_intake, "intake_issues")
assert hasattr(issues_intake, "validate_issue")


class TestIssuesIntakeMalformed(unittest.TestCase):
    """Test suite verifying that bin/issues-intake cleanly rejects malformed payloads and polled issues."""

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

    def test_payload_file_success_path(self):
        """Success-path: well-formed payload file creates exactly one goal and is idempotent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runs_dir = os.path.join(tmpdir, "runs")
            goals_dir = os.path.join(runs_dir, "foreman", "goals")
            os.makedirs(goals_dir, exist_ok=True)

            payload_path = os.path.join(tmpdir, "valid.json")
            valid_payload = {
                "id": 1001,
                "title": "Legitimate goal issue",
                "body": "Detailed task instructions.",
                "labels": ["agent-forge"]
            }
            with open(payload_path, "w", encoding="utf-8") as f:
                json.dump(valid_payload, f)

            env = dict(os.environ, SANDBOX_TEST="1", SANDBOX_RUNS_DIR=runs_dir)

            # First intake run: should create exactly one goal
            proc1 = subprocess.run([INTAKE_BIN, payload_path], capture_output=True, text=True, env=env, timeout=15)
            self.assertEqual(proc1.returncode, 0, f"Expected returncode 0, got {proc1.returncode}: {proc1.stderr}")
            self.assertNotIn("Traceback (most recent call last)", proc1.stderr)

            goal_files1 = [f for f in os.listdir(goals_dir) if f.endswith(".json")]
            self.assertEqual(len(goal_files1), 1)

            seen_file = os.path.join(runs_dir, "foreman", "issues-intake.json")
            self.assertTrue(os.path.isfile(seen_file))
            with open(seen_file, "r", encoding="utf-8") as f:
                seen_data = json.load(f)
            self.assertIn(1001, seen_data.get("seen", []))

            # Second intake run: identical payload must be recognized as duplicate (no second goal)
            proc2 = subprocess.run([INTAKE_BIN, payload_path], capture_output=True, text=True, env=env, timeout=15)
            self.assertEqual(proc2.returncode, 0)
            goal_files2 = [f for f in os.listdir(goals_dir) if f.endswith(".json")]
            self.assertEqual(len(goal_files2), 1)

            # Test valid GitHub webhook envelope unwrapping
            gh_payload_path = os.path.join(tmpdir, "gh_valid.json")
            gh_valid = {
                "action": "opened",
                "issue": {
                    "id": 1002,
                    "title": "Valid GH issue",
                    "body": "GH body",
                    "labels": [{"name": "agent-forge"}]
                }
            }
            with open(gh_payload_path, "w", encoding="utf-8") as f:
                json.dump(gh_valid, f)
            proc_gh = subprocess.run([INTAKE_BIN, gh_payload_path], capture_output=True, text=True, env=env, timeout=15)
            self.assertEqual(proc_gh.returncode, 0)
            goal_files_gh = [f for f in os.listdir(goals_dir) if f.endswith(".json")]
            self.assertEqual(len(goal_files_gh), 2)

            # Test valid GitLab webhook envelope unwrapping
            gl_payload_path = os.path.join(tmpdir, "gl_valid.json")
            gl_valid = {
                "object_kind": "issue",
                "object_attributes": {
                    "id": 1003,
                    "title": "Valid GL issue",
                    "description": "GL body"
                },
                "labels": [{"title": "agent-forge"}]
            }
            with open(gl_payload_path, "w", encoding="utf-8") as f:
                json.dump(gl_valid, f)
            proc_gl = subprocess.run([INTAKE_BIN, gl_payload_path], capture_output=True, text=True, env=env, timeout=15)
            self.assertEqual(proc_gl.returncode, 0)
            goal_files_gl = [f for f in os.listdir(goals_dir) if f.endswith(".json")]
            self.assertEqual(len(goal_files_gl), 3)

        assert proc1.returncode == 0
        assert proc2.returncode == 0
        assert proc_gh.returncode == 0
        assert proc_gl.returncode == 0
        assert len(goal_files1) == 1
        assert len(goal_files2) == 1
        assert len(goal_files_gh) == 2
        assert len(goal_files_gl) == 3
        assert 1001 in seen_data.get("seen", [])

    def test_poll_path_rejects_malformed_fetch_issues(self):
        """Real poll flow: validate_issue cleanly rejects malformed issue dictionaries returned by forge API."""
        malformed_test_cases = [
            ("missing_title", [{"id": 2001, "body": "only body", "labels": ["agent-forge"]}], "missing required field 'title'"),
            ("empty_title", [{"id": 2002, "title": "   ", "labels": ["agent-forge"]}], "'title' cannot be empty"),
            ("non_numeric_id", [{"id": "bad-id-xyz", "title": "Bad ID", "labels": ["agent-forge"]}], "invalid type for 'id'"),
            ("control_chars", [{"id": 2003, "title": "poll\x00title", "labels": ["agent-forge"]}], "control characters or NUL byte"),
            ("invalid_labels_type", [{"id": 2004, "title": "Valid title", "labels": "not-a-list"}], "invalid type for 'labels'"),
            ("oversized_title", [{"id": 2005, "title": "X" * 20000, "labels": ["agent-forge"]}], "exceeds maximum allowed length"),
            ("not_a_dict_entry", ["not-a-dict-string"], "issue entry must be a JSON object"),
        ]

        exit_codes = []
        captured_errors = []

        for name, fake_data, expected_err in malformed_test_cases:
            with tempfile.TemporaryDirectory() as tmpdir:
                runs_dir = os.path.join(tmpdir, "runs")
                goals_dir = os.path.join(runs_dir, "foreman", "goals")
                os.makedirs(goals_dir, exist_ok=True)

                stderr_buf = io.StringIO()
                with patch.object(issues_intake, "fetch_issues", return_value=fake_data), \
                     patch("sys.stderr", stderr_buf):
                    with self.assertRaises(SystemExit) as ctx:
                        issues_intake.intake_issues(
                            kind="github",
                            repo="acme/shop",
                            token="test-token",
                            runs_dir=runs_dir
                        )

                    exit_codes.append(ctx.exception.code)
                    err_text = stderr_buf.getvalue()
                    captured_errors.append(err_text)

                    self.assertEqual(ctx.exception.code, 1, f"Expected exit code 1 for case {name}")
                    self.assertNotIn("Traceback (most recent call last)", err_text)
                    self.assertIn(expected_err, err_text, f"Expected substring '{expected_err}' in stderr for case {name}")

                    goal_files = [f for f in os.listdir(goals_dir) if f.endswith(".json")]
                    self.assertEqual(len(goal_files), 0, f"Goal file unexpectedly created for case {name}")

                    seen_file = os.path.join(runs_dir, "foreman", "issues-intake.json")
                    self.assertFalse(os.path.exists(seen_file), f"Seen file unexpectedly updated for case {name}")

        assert len(exit_codes) == len(malformed_test_cases)
        assert all(code == 1 for code in exit_codes)
        assert all("Traceback (most recent call last)" not in err for err in captured_errors)

    def test_poll_path_subprocess_against_fake_forge_server(self):
        """Real poll flow via subprocess: cleanly rejects malformed API responses from an HTTP forge fixture."""
        server_state = {"response_bytes": b"[]", "status": 200}

        class MockForgeHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(server_state["status"])
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(server_state["response_bytes"])

            def log_message(self, format, *args):
                pass

        httpd = http.server.HTTPServer(("127.0.0.1", 0), MockForgeHandler)
        server_port = httpd.server_port
        server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        server_thread.start()

        def _run_poll_subprocess(response_content):
            server_state["response_bytes"] = response_content if isinstance(response_content, bytes) else response_content.encode("utf-8")
            with tempfile.TemporaryDirectory() as tmpdir:
                runs_dir = os.path.join(tmpdir, "runs")
                goals_dir = os.path.join(runs_dir, "foreman", "goals")
                os.makedirs(goals_dir, exist_ok=True)

                env = dict(
                    os.environ,
                    SANDBOX_TEST="1",
                    SANDBOX_RUNS_DIR=runs_dir,
                    FORGE_KIND="github",
                    FORGE_REPO="acme/shop",
                    FORGE_UPSTREAM_TOKEN="dummy-token",
                    AF_UPSTREAM_API=f"http://127.0.0.1:{server_port}",
                )

                proc = subprocess.run([INTAKE_BIN], capture_output=True, text=True, env=env, timeout=15)
                goals = [f for f in os.listdir(goals_dir) if f.endswith(".json")]
                seen_exists = os.path.exists(os.path.join(runs_dir, "foreman", "issues-intake.json"))
                return proc.returncode, proc.stdout, proc.stderr, goals, seen_exists

        try:
            # 1. Forge server returns issue with embedded NUL byte
            rc1, out1, err1, goals1, seen1 = _run_poll_subprocess(
                json.dumps([{"id": 3001, "title": "poll evil\x00title", "labels": ["agent-forge"]}])
            )
            self.assertNotEqual(rc1, 0)
            self.assertNotIn("Traceback (most recent call last)", err1)
            self.assertIn("control characters or NUL byte", err1)
            self.assertEqual(len(goals1), 0)
            self.assertFalse(seen1)

            # 2. Forge server returns invalid JSON
            rc2, out2, err2, goals2, seen2 = _run_poll_subprocess("this is not { json")
            self.assertNotEqual(rc2, 0)
            self.assertNotIn("Traceback (most recent call last)", err2)
            self.assertIn("Failed to parse JSON issues response", err2)
            self.assertEqual(len(goals2), 0)
            self.assertFalse(seen2)

            # 3. Forge server returns non-list response
            rc3, out3, err3, goals3, seen3 = _run_poll_subprocess(json.dumps({"error": "not a list"}))
            self.assertNotEqual(rc3, 0)
            self.assertNotIn("Traceback (most recent call last)", err3)
            self.assertIn("expected issue list", err3)
            self.assertEqual(len(goals3), 0)
            self.assertFalse(seen3)

            # 4. Forge server returns issue missing title
            rc4, out4, err4, goals4, seen4 = _run_poll_subprocess(
                json.dumps([{"id": 3002, "body": "no title", "labels": ["agent-forge"]}])
            )
            self.assertNotEqual(rc4, 0)
            self.assertNotIn("Traceback (most recent call last)", err4)
            self.assertIn("missing required field 'title'", err4)
            self.assertEqual(len(goals4), 0)
            self.assertFalse(seen4)

            assert rc1 != 0
            assert rc2 != 0
            assert rc3 != 0
            assert rc4 != 0
            assert "Traceback (most recent call last)" not in err1
            assert "Traceback (most recent call last)" not in err2
            assert "Traceback (most recent call last)" not in err3
            assert "Traceback (most recent call last)" not in err4
            assert "control characters or NUL byte" in err1
            assert "Failed to parse JSON issues response" in err2
            assert "expected issue list" in err3
            assert "missing required field 'title'" in err4
            assert len(goals1) == 0
            assert len(goals2) == 0
            assert len(goals3) == 0
            assert len(goals4) == 0
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
