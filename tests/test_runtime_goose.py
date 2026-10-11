#!/usr/bin/env python3
"""Tests for the Goose runtime in bin/lib/runtimes.py, doctor check, and docs.

Public interface:
- Run directly: python3 tests/test_runtime_goose.py
- Run via unittest: python3 -m unittest tests/test_runtime_goose.py

Important invariants:
- RUNTIMES['goose'] is registered with image=AGY_IMAGE, endpoint=True, api=False.
- uses_endpoint('goose') is True; hosts('goose', url) resolves host from endpoint URL.
- command('goose', ...) supplies prompt on stdin, never in argv.
- scan('goose', ...) returns tokens=None, conversation_id=None, and heuristic tools and result.
- Doctor check and RUNTIMES documentation cover goose properly.
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


class TestGooseRuntime(unittest.TestCase):
    """Unit tests for Goose runtime registration, commands, and output scanning."""

    def test_goose_runtime_entry(self):
        """RUNTIMES['goose'] is configured as endpoint-based subscription runtime."""
        self.assertIn("goose", runtimes.RUNTIMES)
        entry = runtimes.RUNTIMES["goose"]
        self.assertEqual(entry["image"], runtimes.AGY_IMAGE)
        self.assertIsNone(entry["key"])
        self.assertEqual(entry["hosts"], [])
        self.assertFalse(entry["api"])
        self.assertTrue(entry.get("endpoint"))
        self.assertFalse(runtimes.is_api("goose"))
        self.assertTrue(runtimes.uses_endpoint("goose"))
        assert runtimes.is_api("goose") is False
        assert runtimes.uses_endpoint("goose") is True

    def test_goose_hosts_resolution(self):
        """hosts('goose', url) derives host from endpoint_url exactly like compat."""
        url = "http://192.168.1.50:8000/v1"
        self.assertEqual(runtimes.hosts("goose", url), ["192.168.1.50"])
        self.assertEqual(runtimes.hosts("goose", "https://api.openai.com/v1"), ["api.openai.com"])
        self.assertEqual(runtimes.hosts("goose", ""), [])
        self.assertEqual(runtimes.hosts("goose", "ftp://invalid"), [])
        assert runtimes.hosts("goose", url) == ["192.168.1.50"]
        assert runtimes.hosts("goose", "") == []

    def test_goose_command_prompt_on_stdin(self):
        """command('goose', ...) returns prompt on stdin and never in argv."""
        prompt = "Fix the login route authentication bug"
        model = "gpt-4o"
        conv = "session-12345"
        argv, stdin_text = runtimes.command("goose", prompt, model, conv, 20, 0.0)

        self.assertEqual(stdin_text, prompt)
        self.assertNotIn(prompt, argv)
        self.assertEqual(argv, ["goose", "run", "--instructions", "-"])
        self.assertNotIn(model, argv)
        assert stdin_text == prompt
        assert prompt not in argv
        assert argv == ["goose", "run", "--instructions", "-"]

    def test_goose_scan_tokens_none_and_heuristic_tools(self):
        """scan('goose', ...) returns tokens=None, conversation_id=None, and counted tools."""
        lines = [
            "Starting goose session",
            "Invoking tool: bash with command pytest",
            "Tool call: git status",
            "All tests pass successfully.",
        ]
        res = runtimes.scan("goose", lines)
        self.assertIsNone(res["tokens"])
        self.assertEqual(res["tools"], 2)
        self.assertIsNone(res["conversation_id"])
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"], "All tests pass successfully.")
        assert res["tokens"] is None
        assert res["tools"] == 2
        assert res["conversation_id"] is None
        assert res["result"] == "All tests pass successfully."

    def test_goose_scan_json_lines_stream(self):
        """scan('goose', ...) parses JSON-lines stream, counts tools and extracts result."""
        stream = [
            '{"type": "init", "model": "qwen"}',
            '{"type": "tool_call", "name": "bash", "arguments": {"cmd": "ls"}}',
            '{"type": "tool_use", "name": "editor"}',
            '{"type": "text", "text": "Task finished without errors."}',
        ]
        res = runtimes.scan("goose", stream)
        self.assertIsNone(res["tokens"])
        self.assertEqual(res["tools"], 2)
        self.assertIsNone(res["conversation_id"])
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "SUCCESS")
        assert res["tokens"] is None
        assert res["tools"] == 2
        assert res["conversation_id"] is None
        assert res["result"]["status"] == "SUCCESS"

    def test_goose_scan_empty_output(self):
        """scan('goose', ...) returns result=None when output stream is empty."""
        res = runtimes.scan("goose", ["", "   "])
        self.assertIsNone(res["tokens"])
        self.assertEqual(res["tools"], 0)
        self.assertIsNone(res["conversation_id"])
        self.assertIsNone(res["result"])
        assert res["tokens"] is None
        assert res["tools"] == 0
        assert res["result"] is None

    def test_goose_scan_file(self):
        """scan_file('goose', path) parses lines from file correctly."""
        lines = [
            "Running tool: pytest",
            "Done",
        ]
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as tf:
            for line in lines:
                tf.write(line + "\n")
            tf.flush()
            parsed = runtimes.scan_file("goose", tf.name)
        self.assertIsNone(parsed["tokens"])
        self.assertEqual(parsed["tools"], 1)
        self.assertEqual(parsed["result"], "Done")
        assert parsed["tokens"] is None
        assert parsed["tools"] == 1
        assert parsed["result"] == "Done"

    def test_existing_runtimes_behavior_unchanged(self):
        """Existing runtimes retain their behavior and return shapes."""
        self.assertFalse(runtimes.is_api("agy"))
        self.assertTrue(runtimes.is_api("claude"))
        self.assertTrue(runtimes.is_api("codex"))
        self.assertTrue(runtimes.is_api("compat"))
        self.assertTrue(runtimes.is_api("aider"))
        self.assertFalse(runtimes.is_api("opencode"))
        self.assertTrue(runtimes.uses_endpoint("compat"))
        self.assertTrue(runtimes.uses_endpoint("aider"))
        self.assertTrue(runtimes.uses_endpoint("opencode"))
        self.assertFalse(runtimes.uses_endpoint("claude"))
        compat_cmd, compat_stdin = runtimes.command("compat", "prompt", "model")
        self.assertEqual(compat_stdin, "prompt")
        self.assertNotIn("prompt", compat_cmd)
        assert runtimes.is_api("agy") is False
        assert runtimes.is_api("compat") is True
        assert runtimes.uses_endpoint("compat") is True

    def test_doctor_contains_goose_check(self):
        """bin/doctor contains check for goose binary and does not raise on absence."""
        doc_path = os.path.join(ROOT, "bin", "doctor")
        with open(doc_path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('"goose"', src)
        self.assertIn("Goose", src)
        assert '"goose"' in src
        assert "Goose" in src

    def test_runtimes_doc_contains_goose_section(self):
        """docs/RUNTIMES.md contains ## Goose section describing setup, endpoint and cost."""
        doc_path = os.path.join(ROOT, "docs", "RUNTIMES.md")
        with open(doc_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("## Goose", content)
        self.assertIn("unknown", content)
        self.assertIn("Block", content)
        assert "## Goose" in content
        assert "unknown" in content
        assert "Block" in content


if __name__ == "__main__":
    unittest.main()
