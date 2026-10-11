#!/usr/bin/env python3
"""Tests for OpenCode runtime in bin/lib/runtimes.py.

Public interface:
- Run directly: python3 tests/test_runtime_opencode.py
- Run via unittest: python3 -m unittest tests/test_runtime_opencode.py

Important invariants:
- RUNTIMES['opencode'] is a subscription runtime using AGY_IMAGE with endpoint=True.
- uses_endpoint('opencode') returns True; hosts('opencode', url) resolves host from endpoint.
- command('opencode', ...) passes prompt on stdin and never in argv.
- scan('opencode', ...) returns tokens: None when OpenCode output does not expose token counts.
- Existing runtime behavior is completely preserved.
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))

# Top-level smoke-import check to fail loudly if import breaks
try:
    import runtimes
except Exception as e:
    sys.stderr.write(f"smoke-import failed for runtimes: {e}\n")
    sys.exit(1)


class TestOpenCodeRuntime(unittest.TestCase):
    """Unit tests for OpenCode runtime definition and helpers."""

    def test_opencode_runtime_entry(self):
        """RUNTIMES['opencode'] must be properly configured as subscription runtime with endpoint support."""
        self.assertIn("opencode", runtimes.RUNTIMES)
        entry = runtimes.RUNTIMES["opencode"]
        self.assertEqual(entry["image"], runtimes.AGY_IMAGE)
        self.assertIsNone(entry["key"])
        self.assertEqual(entry["hosts"], [])
        self.assertFalse(entry["api"])
        self.assertTrue(entry.get("endpoint"))
        self.assertFalse(runtimes.is_api("opencode"))
        self.assertTrue(runtimes.uses_endpoint("opencode"))
        assert runtimes.is_api("opencode") is False
        assert runtimes.uses_endpoint("opencode") is True

    def test_opencode_hosts_resolution(self):
        """hosts('opencode', url) must resolve host from endpoint URL like compat."""
        url = "http://192.168.1.50:8000/v1"
        self.assertEqual(runtimes.hosts("opencode", url), ["192.168.1.50"])
        self.assertEqual(runtimes.hosts("opencode", "https://api.openai.com/v1"), ["api.openai.com"])
        self.assertEqual(runtimes.hosts("opencode", ""), [])
        self.assertEqual(runtimes.hosts("opencode", "ftp://invalid"), [])
        assert runtimes.hosts("opencode", url) == ["192.168.1.50"]
        assert runtimes.hosts("opencode", "") == []

    def test_opencode_command_argv_and_stdin(self):
        """command('opencode', ...) returns argv with prompt on stdin, never in argv."""
        prompt = "Refactor the authentication handler to use tokens"
        model = "anthropic/claude-3-5-sonnet"
        conv = "ses_abcdef123456"
        argv, stdin_text = runtimes.command("opencode", prompt, model, conv, 15, 0.0)

        self.assertEqual(stdin_text, prompt)
        self.assertNotIn(prompt, argv)
        self.assertEqual(argv[0], "opencode")
        self.assertEqual(argv[1], "run")
        for flag in runtimes.OPENCODE_QUIET:
            self.assertIn(flag, argv)
        self.assertIn("--model", argv)
        self.assertIn(model, argv)
        self.assertIn("--session", argv)
        self.assertIn(conv, argv)

        assert prompt not in argv
        assert stdin_text == prompt
        assert "--session" in argv

    def test_opencode_command_omits_empty_options(self):
        """command('opencode', ...) omits optional model and session flags when not specified."""
        argv, stdin_text = runtimes.command("opencode", "Simple task", "", None)
        self.assertEqual(stdin_text, "Simple task")
        self.assertNotIn("--model", argv)
        self.assertNotIn("--session", argv)
        assert stdin_text == "Simple task"
        assert "--model" not in argv
        assert "--session" not in argv

    def test_opencode_scan_tokens_none_when_unreported(self):
        """scan('opencode', ...) returns tokens: None when output has no token count."""
        stream = [
            '{"type": "step_start", "sessionID": "ses_99", "part": {"id": "p1"}}',
            '{"type": "tool_use", "name": "bash", "sessionID": "ses_99"}',
            '{"type": "text", "sessionID": "ses_99", "part": {"text": "All tests pass."}}',
            '{"type": "step_finish", "sessionID": "ses_99", "part": {"reason": "stop"}}',
        ]
        res = runtimes.scan("opencode", stream)
        self.assertIsNone(res["tokens"])
        self.assertEqual(res["tools"], 1)
        self.assertEqual(res["conversation_id"], "ses_99")
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertEqual(res["result"]["response"], "All tests pass.")
        self.assertIsNone(res["result"]["cost_usd"])
        assert res["tokens"] is None
        assert res["tools"] == 1
        assert res["result"]["status"] == "SUCCESS"

    def test_opencode_scan_result_none_until_final_event(self):
        """scan('opencode', ...) returns result: None until step_finish stop or result event."""
        intermediate = [
            '{"type": "step_start", "sessionID": "ses_42"}',
            '{"type": "step_finish", "sessionID": "ses_42", "part": {"reason": "tool-calls"}}',
        ]
        res = runtimes.scan("opencode", intermediate)
        self.assertIsNone(res["result"])
        self.assertEqual(res["conversation_id"], "ses_42")
        assert res["result"] is None
        assert res["conversation_id"] == "ses_42"

    def test_opencode_scan_step_finish_error(self):
        """scan('opencode', ...) marks error status when step_finish has error reason or field."""
        error_stream = [
            '{"type": "step_start", "sessionID": "ses_err"}',
            '{"type": "step_finish", "sessionID": "ses_err", "part": {"reason": "error", "error": "rate limit reached"}}',
        ]
        res = runtimes.scan("opencode", error_stream)
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "ERROR")
        self.assertIn("rate limit reached", res["result"]["error"])
        assert res["result"]["status"] == "ERROR"
        assert "rate limit reached" in res["result"]["error"]

    def test_opencode_scan_file_helper(self):
        """scan_file('opencode', path) parses lines from a temporary file properly."""
        stream_data = [
            '{"type": "step_start", "sessionID": "ses_file"}',
            '{"type": "step_finish", "sessionID": "ses_file", "part": {"reason": "stop"}}',
        ]
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as tf:
            for item in stream_data:
                tf.write(item + "\n")
            tf.flush()
            parsed = runtimes.scan_file("opencode", tf.name)
        self.assertIsNone(parsed["tokens"])
        self.assertEqual(parsed["conversation_id"], "ses_file")
        self.assertEqual(parsed["result"]["status"], "SUCCESS")
        assert parsed["tokens"] is None
        assert parsed["result"]["status"] == "SUCCESS"

    def test_existing_runtimes_behavior_unchanged(self):
        """Existing runtimes must retain their original behavior and configuration."""
        self.assertFalse(runtimes.is_api("agy"))
        self.assertTrue(runtimes.is_api("claude"))
        self.assertTrue(runtimes.is_api("codex"))
        self.assertTrue(runtimes.is_api("compat"))
        self.assertTrue(runtimes.is_api("aider"))
        self.assertTrue(runtimes.uses_endpoint("compat"))
        self.assertTrue(runtimes.uses_endpoint("aider"))
        self.assertFalse(runtimes.uses_endpoint("claude"))
        agy_cmd, agy_stdin = runtimes.command("agy", "prompt", "model")
        self.assertIsNone(agy_stdin)
        self.assertIn("prompt", agy_cmd)
        compat_cmd, compat_stdin = runtimes.command("compat", "prompt", "model")
        self.assertEqual(compat_stdin, "prompt")
        self.assertNotIn("prompt", compat_cmd)
        assert runtimes.is_api("agy") is False
        assert runtimes.is_api("compat") is True
        assert runtimes.uses_endpoint("compat") is True


if __name__ == "__main__":
    unittest.main()
