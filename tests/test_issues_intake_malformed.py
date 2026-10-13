"""Tests for bin/issues-intake malformed payload and forge response rejection.

Public interface:
  python3 -m unittest tests/test_issues_intake_malformed.py

Invariants:
  - Validates untrusted external forge responses offline via fake forge HTTP server (no real forge reached).
  - Rejects non-JSON, missing required fields, wrong field types, control/NUL chars, and oversized input cleanly.
  - Exits non-zero with clear stderr message, never leaking a raw Python traceback.
  - Never creates a goal file or updates issues-intake.json seen list on malformed input.
  - Drives tests/test_issues_intake_malformed.sh and verifies each of the 5 malformed cases individually.
  - Verifies normal success operation of the poll flow (single goal creation and idempotency).
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

    def _execute_intake_poll(self, response_bytes_or_str):
        """Helper to run bin/issues-intake via subprocess against a local fake forge server."""
        resp_data = response_bytes_or_str if isinstance(response_bytes_or_str, bytes) else response_bytes_or_str.encode("utf-8")

        class SingleResponseHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp_data)))
                self.end_headers()
                self.wfile.write(resp_data)

            def log_message(self, format, *args):
                pass

        httpd = http.server.HTTPServer(("127.0.0.1", 0), SingleResponseHandler)
        port = httpd.server_port
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        with tempfile.TemporaryDirectory() as tmpdir:
            runs_dir = os.path.join(tmpdir, "runs")
            goals_dir = os.path.join(runs_dir, "foreman", "goals")
            os.makedirs(goals_dir, exist_ok=True)

            cmd = [sys.executable, INTAKE_BIN]
            env = dict(
                os.environ,
                SANDBOX_TEST="1",
                SANDBOX_RUNS_DIR=runs_dir,
                FORGE_KIND="github",
                FORGE_REPO="acme/shop",
                FORGE_UPSTREAM_TOKEN="dummy-token",
                AF_UPSTREAM_API=f"http://127.0.0.1:{port}",
            )

            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=15)
                goal_files = [f for f in os.listdir(goals_dir) if f.endswith(".json")]
                seen_file = os.path.join(runs_dir, "foreman", "issues-intake.json")
                seen_exists = os.path.exists(seen_file)
                return proc.returncode, proc.stdout, proc.stderr, goal_files, seen_exists
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_malformed_shell_suite(self):
        """Integration check: tests/test_issues_intake_malformed.sh passes with ALL OK."""
        r = subprocess.run(["bash", MALFORMED_SH], capture_output=True, text=True, cwd=ROOT, timeout=30)
        self.assertEqual(r.returncode, 0, f"Malformed shell suite failed: {r.stderr}")
        self.assertIn("ALL OK", r.stdout)
        assert r.returncode == 0
        assert "ALL OK" in r.stdout

    def test_case1_not_valid_json(self):
        """Case 1: not valid JSON at all produces non-zero exit, clear stderr error, and no goals."""
        # 1a: Invalid JSON syntax
        rc, out, err, goals, seen = self._execute_intake_poll("not valid json { [ at all")
        self.assertNotEqual(rc, 0)
        self.assertNotIn("Traceback (most recent call last)", err)
        self.assertIn("Failed to parse JSON issues response", err)
        self.assertEqual(len(goals), 0)
        self.assertFalse(seen)

        # 1b: Invalid UTF-8 bytes in stream
        rc_utf8, out_utf8, err_utf8, goals_utf8, seen_utf8 = self._execute_intake_poll(b"\xff\xfe\x00\x00")
        self.assertNotEqual(rc_utf8, 0)
        self.assertNotIn("Traceback (most recent call last)", err_utf8)
        self.assertIn("Failed to parse JSON issues response", err_utf8)
        self.assertEqual(len(goals_utf8), 0)
        self.assertFalse(seen_utf8)

        assert rc != 0
        assert rc_utf8 != 0
        assert "Traceback (most recent call last)" not in err
        assert "Traceback (most recent call last)" not in err_utf8
        assert "Failed to parse JSON issues response" in err
        assert "Failed to parse JSON issues response" in err_utf8
        assert len(goals) == 0
        assert len(goals_utf8) == 0
        assert not seen
        assert not seen_utf8

    def test_case2_missing_required_field(self):
        """Case 2: valid JSON missing required fields ('title' or 'id') is rejected cleanly."""
        # 2a: Missing title
        no_title = json.dumps([{"id": 201, "body": "description without title", "labels": ["agent-forge"]}])
        rc1, out1, err1, goals1, seen1 = self._execute_intake_poll(no_title)
        self.assertNotEqual(rc1, 0)
        self.assertNotIn("Traceback (most recent call last)", err1)
        self.assertIn("missing required field 'title'", err1)
        self.assertEqual(len(goals1), 0)
        self.assertFalse(seen1)

        # 2b: Missing id
        no_id = json.dumps([{"title": "Issue without id", "body": "description", "labels": ["agent-forge"]}])
        rc2, out2, err2, goals2, seen2 = self._execute_intake_poll(no_id)
        self.assertNotEqual(rc2, 0)
        self.assertNotIn("Traceback (most recent call last)", err2)
        self.assertIn("missing required field 'id'", err2)
        self.assertEqual(len(goals2), 0)
        self.assertFalse(seen2)

        assert rc1 != 0
        assert rc2 != 0
        assert "missing required field 'title'" in err1
        assert "missing required field 'id'" in err2
        assert len(goals1) == 0
        assert len(goals2) == 0
        assert not seen1
        assert not seen2

    def test_case3_wrong_field_type(self):
        """Case 3: required fields with wrong types (labels as string, id as non-numeric string) are rejected."""
        # 3a: labels as string instead of list
        str_labels = json.dumps([{"id": 301, "title": "Wrong label type", "labels": "agent-forge"}])
        rc1, out1, err1, goals1, seen1 = self._execute_intake_poll(str_labels)
        self.assertNotEqual(rc1, 0)
        self.assertNotIn("Traceback (most recent call last)", err1)
        self.assertIn("invalid type for 'labels'", err1)
        self.assertEqual(len(goals1), 0)
        self.assertFalse(seen1)

        # 3b: id as non-numeric garbage
        garbage_id = json.dumps([{"id": "non-numeric-garbage-id", "title": "Garbage ID", "labels": ["agent-forge"]}])
        rc2, out2, err2, goals2, seen2 = self._execute_intake_poll(garbage_id)
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
        assert not seen1
        assert not seen2

    def test_case4_control_characters_or_nul_byte(self):
        """Case 4: title or body containing control characters or embedded NUL byte is rejected cleanly."""
        # 4a: embedded NUL byte in title (\u0000)
        nul_title = '[{"id": 401, "title": "evil\\u0000title", "labels": ["agent-forge"]}]'
        rc1, out1, err1, goals1, seen1 = self._execute_intake_poll(nul_title)
        self.assertNotEqual(rc1, 0)
        self.assertNotIn("Traceback (most recent call last)", err1)
        self.assertIn("control characters or NUL byte", err1)
        self.assertEqual(len(goals1), 0)
        self.assertFalse(seen1)

        # 4b: ANSI escape sequences and bell control characters
        ctrl_title = '[{"id": 402, "title": "escape\\u001b[31mcolor\\u0007bell", "labels": ["agent-forge"]}]'
        rc2, out2, err2, goals2, seen2 = self._execute_intake_poll(ctrl_title)
        self.assertNotEqual(rc2, 0)
        self.assertNotIn("Traceback (most recent call last)", err2)
        self.assertIn("control characters or NUL byte", err2)
        self.assertEqual(len(goals2), 0)
        self.assertFalse(seen2)

        # 4c: raw unescaped NUL byte in payload stream
        raw_nul = b'[{"id": 403, "title": "raw\x00nul", "labels": ["agent-forge"]}]'
        rc3, out3, err3, goals3, seen3 = self._execute_intake_poll(raw_nul)
        self.assertNotEqual(rc3, 0)
        self.assertNotIn("Traceback (most recent call last)", err3)
        self.assertEqual(len(goals3), 0)
        self.assertFalse(seen3)

        # 4d: control characters or embedded NUL byte in body
        nul_body = '[{"id": 404, "title": "Safe title", "body": "evil\\u0000body", "labels": ["agent-forge"]}]'
        rc4, out4, err4, goals4, seen4 = self._execute_intake_poll(nul_body)
        self.assertNotEqual(rc4, 0)
        self.assertNotIn("Traceback (most recent call last)", err4)
        self.assertIn("control characters or NUL byte", err4)
        self.assertEqual(len(goals4), 0)
        self.assertFalse(seen4)

        assert rc1 != 0
        assert rc2 != 0
        assert rc3 != 0
        assert rc4 != 0
        assert "control characters or NUL byte" in err1
        assert "control characters or NUL byte" in err2
        assert "control characters or NUL byte" in err4
        assert len(goals1) == 0
        assert len(goals2) == 0
        assert len(goals3) == 0
        assert len(goals4) == 0

    def test_case5_oversized_payload(self):
        """Case 5: oversized title (50,000 and 2,000,000 chars) rejects bounded input without hanging or crashing."""
        # 5a: Title exceeding MAX_TITLE_LEN (50,000 chars)
        oversized_title = json.dumps([{"id": 501, "title": "A" * 50000, "labels": ["agent-forge"]}])
        rc1, out1, err1, goals1, seen1 = self._execute_intake_poll(oversized_title)
        self.assertNotEqual(rc1, 0)
        self.assertNotIn("Traceback (most recent call last)", err1)
        self.assertIn("title exceeds maximum allowed length", err1)
        self.assertEqual(len(goals1), 0)
        self.assertFalse(seen1)

        # 5b: Multi-MB title (2,000,000 chars)
        multi_mb_title = json.dumps([{"id": 502, "title": "A" * 2000000, "labels": ["agent-forge"]}])
        rc2, out2, err2, goals2, seen2 = self._execute_intake_poll(multi_mb_title)
        self.assertNotEqual(rc2, 0)
        self.assertNotIn("Traceback (most recent call last)", err2)
        self.assertIn("title exceeds maximum allowed length", err2)
        self.assertEqual(len(goals2), 0)
        self.assertFalse(seen2)

        assert rc1 != 0
        assert rc2 != 0
        assert "Traceback (most recent call last)" not in err1
        assert "Traceback (most recent call last)" not in err2
        assert "title exceeds maximum allowed length" in err1
        assert "title exceeds maximum allowed length" in err2
        assert len(goals1) == 0
        assert len(goals2) == 0
        assert not seen1
        assert not seen2

    def test_poll_success_path_and_idempotency(self):
        """Success-path: well-formed issue from forge creates exactly one goal and is idempotent on rerun."""
        valid_issue = json.dumps([{
            "id": 1001,
            "title": "Legitimate goal issue",
            "body": "Detailed task instructions.",
            "labels": ["agent-forge"]
        }])

        class SuccessHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                data = valid_issue.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *args):
                pass

        httpd = http.server.HTTPServer(("127.0.0.1", 0), SuccessHandler)
        port = httpd.server_port
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        p1_code = -1
        p2_code = -1
        p1_err = ""
        goals1_len = 0
        goals2_len = 0
        seen_issue_ids = []

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                runs_dir = os.path.join(tmpdir, "runs")
                goals_dir = os.path.join(runs_dir, "foreman", "goals")
                os.makedirs(goals_dir, exist_ok=True)

                cmd = [sys.executable, INTAKE_BIN]
                env = dict(
                    os.environ,
                    SANDBOX_TEST="1",
                    SANDBOX_RUNS_DIR=runs_dir,
                    FORGE_KIND="github",
                    FORGE_REPO="acme/shop",
                    FORGE_UPSTREAM_TOKEN="dummy-token",
                    AF_UPSTREAM_API=f"http://127.0.0.1:{port}",
                )

                # First poll run: creates exactly one goal
                p1 = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=15)
                p1_code = p1.returncode
                p1_err = p1.stderr
                self.assertEqual(p1.returncode, 0)
                self.assertNotIn("Traceback (most recent call last)", p1.stderr)
                goals1 = [f for f in os.listdir(goals_dir) if f.endswith(".json")]
                goals1_len = len(goals1)
                self.assertEqual(len(goals1), 1)

                seen_file = os.path.join(runs_dir, "foreman", "issues-intake.json")
                self.assertTrue(os.path.isfile(seen_file))
                with open(seen_file, "r", encoding="utf-8") as f:
                    seen_data = json.load(f)
                seen_issue_ids = seen_data.get("seen", [])
                self.assertIn(1001, seen_issue_ids)

                # Second poll run: identical issue recognized as seen, no duplicate goal created
                p2 = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=15)
                p2_code = p2.returncode
                self.assertEqual(p2.returncode, 0)
                goals2 = [f for f in os.listdir(goals_dir) if f.endswith(".json")]
                goals2_len = len(goals2)
                self.assertEqual(len(goals2), 1)
        finally:
            httpd.shutdown()
            httpd.server_close()

        assert p1_code == 0
        assert p2_code == 0
        assert "Traceback (most recent call last)" not in p1_err
        assert goals1_len == 1
        assert goals2_len == 1
        assert 1001 in seen_issue_ids

    def test_direct_validate_issue_unit_checks(self):
        """Direct unit checks on issues_intake.validate_issue for boundary and type rejection."""
        cases = [
            ("not_dict", "not-a-dict", "issue entry must be a JSON object"),
            ("missing_id", {"title": "T"}, "missing required field 'id'"),
            ("bool_id", {"id": True, "title": "T"}, "invalid type for 'id'"),
            ("string_garbage_id", {"id": "xyz", "title": "T"}, "invalid type for 'id'"),
            ("missing_title", {"id": 1}, "missing required field 'title'"),
            ("int_title", {"id": 1, "title": 123}, "invalid type for 'title'"),
            ("empty_title", {"id": 1, "title": "   "}, "'title' cannot be empty"),
            ("oversized_title", {"id": 1, "title": "X" * 10001}, "title exceeds maximum allowed length"),
            ("control_chars", {"id": 1, "title": "A\x00B"}, "control characters or NUL byte"),
            ("invalid_labels_type", {"id": 1, "title": "T", "labels": "str"}, "invalid type for 'labels'"),
            ("invalid_label_item", {"id": 1, "title": "T", "labels": [123]}, "invalid label format in 'labels'"),
        ]

        exit_codes = []
        captured_errors = []

        for name, bad_issue, expected_msg in cases:
            stderr_buf = io.StringIO()
            with patch("sys.stderr", stderr_buf):
                with self.assertRaises(SystemExit) as ctx:
                    issues_intake.validate_issue(bad_issue)
                exit_codes.append(ctx.exception.code)
                err = stderr_buf.getvalue()
                captured_errors.append(err)
                self.assertEqual(ctx.exception.code, 1, f"Failed exit code for {name}")
                self.assertIn(expected_msg, err, f"Expected '{expected_msg}' in stderr for {name}")

        assert hasattr(issues_intake, "validate_issue")
        assert len(exit_codes) == len(cases)
        assert all(code == 1 for code in exit_codes)
        assert all("Traceback (most recent call last)" not in err for err in captured_errors)

    def test_security_injection_payloads(self):
        """Security: command injection $(id), path traversal, SQL injection, and XSS do not compromise host."""
        class InjectionHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                payload = json.dumps([{
                    "id": 9999,
                    "title": "Fix bug with $(id) and `reboot` and '; DROP TABLE goals;--",
                    "body": "Attack path ../../../../etc/passwd with <script>alert(1)</script>",
                    "labels": ["agent-forge"]
                }]).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                pass

        httpd = http.server.HTTPServer(("127.0.0.1", 0), InjectionHandler)
        port = httpd.server_port
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        rc = -1
        err = ""
        goal_contents = []

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                runs_dir = os.path.join(tmpdir, "runs")
                goals_dir = os.path.join(runs_dir, "foreman", "goals")
                os.makedirs(goals_dir, exist_ok=True)

                cmd = [sys.executable, INTAKE_BIN]
                env = dict(
                    os.environ,
                    SANDBOX_TEST="1",
                    SANDBOX_RUNS_DIR=runs_dir,
                    FORGE_KIND="github",
                    FORGE_REPO="acme/shop",
                    FORGE_UPSTREAM_TOKEN="secret-test-token-do-not-leak",
                    AF_UPSTREAM_API=f"http://127.0.0.1:{port}",
                )

                proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=15)
                rc = proc.returncode
                err = proc.stderr

                # Verify files created in storage
                goal_files = [os.path.join(goals_dir, f) for f in os.listdir(goals_dir) if f.endswith(".json")]
                for gf in goal_files:
                    with open(gf, "r", encoding="utf-8") as f:
                        goal_contents.append(f.read())
        finally:
            httpd.shutdown()
            httpd.server_close()

        # No command executed, no traceback leaked, no secrets exposed in stderr
        assert rc == 0
        assert "Traceback (most recent call last)" not in err
        assert "secret-test-token-do-not-leak" not in err
        # Direct storage verification
        assert len(goal_contents) == 1
        # HTML tags were stripped (<script>alert(1)</script> -> alert(1))
        assert "<script>" not in goal_contents[0]
        # Token was not written into goal text
        assert "secret-test-token-do-not-leak" not in goal_contents[0]


if __name__ == "__main__":
    unittest.main()

