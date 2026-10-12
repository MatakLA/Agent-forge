#!/usr/bin/env python3
"""Tests for the Goose runtime in bin/lib/runtimes.py, doctor check, and docs.

Public interface:
- Run directly: python3 tests/test_runtime_goose.py
- Run via unittest: python3 -m unittest tests/test_runtime_goose.py

Important invariants:
- RUNTIMES['goose'] is registered with image=AGY_IMAGE, endpoint=True, api=False.
- uses_endpoint('goose') is True; hosts('goose', url) resolves host from endpoint URL.
- command('goose', ...) supplies prompt on stdin, never in argv.
- scan('goose', ...) parses SSE event streams, accumulates tokens when present, counts tools, sets ResultStr only on genuine terminal events, and skips malformed frames safely.
- Doctor check and RUNTIMES documentation cover goose properly.
- Existing runtime behavior is completely preserved.
"""
import json
import os
import subprocess
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

    def test_goose_command_and_quiet_flags(self):
        """command('goose', ...) includes GOOSE_QUIET, passes prompt on stdin, and omits secrets/model."""
        self.assertTrue(hasattr(runtimes, "GOOSE_QUIET"))
        self.assertIsInstance(runtimes.GOOSE_QUIET, list)
        prompt = "Confidential instructions with unicode: 修复测试漏洞"
        model = "gpt-4o"
        conv = "session-test-456"
        argv, stdin_text = runtimes.command("goose", prompt, model, conv, 15, 1.5)
        self.assertEqual(stdin_text, prompt)
        self.assertEqual(argv[:2], ["goose", "run"])
        for flag in runtimes.GOOSE_QUIET:
            self.assertIn(flag, argv)
        self.assertEqual(argv, ["goose", "run"] + runtimes.GOOSE_QUIET + ["--instructions", "-"])
        self.assertNotIn(prompt, argv)
        self.assertNotIn(model, argv)
        self.assertNotIn(conv, argv)
        assert hasattr(runtimes, "GOOSE_QUIET")
        assert stdin_text == prompt
        assert argv == ["goose", "run"] + runtimes.GOOSE_QUIET + ["--instructions", "-"]
        assert prompt not in argv

    def test_goose_egress_endpoint_wiring(self):
        """hosts('goose', url) derives egress host dynamically via endpoint mechanism."""
        self.assertTrue(runtimes.uses_endpoint("goose"))
        self.assertEqual(runtimes.RUNTIMES["goose"]["hosts"], [])
        test_cases = [
            ("http://127.0.0.1:8099/v1", ["127.0.0.1"]),
            ("https://api.openai.com/v1", ["api.openai.com"]),
            ("http://model-server.local:8000/v1", ["model-server.local"]),
            ("http://192.168.1.50:11434", ["192.168.1.50"]),
            ("", []),
            ("ftp://invalid-proto", []),
            ("not-a-valid-url", []),
        ]
        for url, expected in test_cases:
            resolved = runtimes.hosts("goose", url)
            self.assertEqual(resolved, expected)
            assert runtimes.hosts("goose", url) == expected
        assert runtimes.uses_endpoint("goose") is True
        assert runtimes.RUNTIMES["goose"]["hosts"] == []

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

    def test_fake_goose_server_fixture_exists(self):
        """tests/fixtures/fake-goose-server.js exists and is non-empty."""
        fixture_path = os.path.join(ROOT, "tests", "fixtures", "fake-goose-server.js")
        self.assertTrue(os.path.isfile(fixture_path))
        self.assertGreater(os.path.getsize(fixture_path), 0)
        assert os.path.isfile(fixture_path)
        assert os.path.getsize(fixture_path) > 0

    def test_goose_runtime_shell_script_exists_and_runs(self):
        """tests/test_runtime_goose.sh runs successfully against fake server."""
        script_path = os.path.join(ROOT, "tests", "test_runtime_goose.sh")
        self.assertTrue(os.path.isfile(script_path))
        assert os.path.isfile(script_path)
        proc = subprocess.run(["bash", script_path], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("goose runtime tests: ALL OK", proc.stdout)
        assert proc.returncode == 0
        assert "goose runtime tests: ALL OK" in proc.stdout

    def test_goose_scan_tool_count_arithmetic(self):
        """scan('goose', ...) counts plain-text and JSON tool-call syntaxes correctly."""
        tool_lines = [
            "Tool call: git status",
            "Invoking tool: bash with command pytest",
            "Running tool: pytest",
            json.dumps({"type": "tool_call", "name": "editor"}),
            json.dumps({"type": "tool_use", "name": "shell"}),
        ]
        for line in tool_lines:
            res = runtimes.scan("goose", [line])
            self.assertEqual(res["tools"], 1)
            assert res["tools"] == 1
        stream = ["Starting session"] + tool_lines + ["All finished successfully."]
        res = runtimes.scan("goose", stream)
        self.assertEqual(res["tools"], len(tool_lines))
        self.assertIsNone(res["tokens"])
        self.assertIsInstance(res["result"], str)
        self.assertEqual(res["result"], "All finished successfully.")
        assert res["tools"] == len(tool_lines)
        assert res["tokens"] is None
        assert isinstance(res["result"], str)
        assert res["result"] == "All finished successfully."

    def test_goose_scan_error_output_string(self):
        """scan('goose', ...) captures error message in result string."""
        err_lines = [
            "Starting Goose session",
            "Error: Goose execution failed due to API error",
        ]
        res = runtimes.scan("goose", err_lines)
        self.assertIsNone(res["tokens"])
        self.assertIsInstance(res["result"], str)
        self.assertIn("Error: Goose execution failed", res["result"])
        assert res["tokens"] is None
        assert isinstance(res["result"], str)
        assert "Error: Goose execution failed" in res["result"]

    def test_goose_scan_sse_success_stream_openai(self):
        """scan('goose', ...) parses OpenAI Chat Completions SSE stream with tool calls and usage."""
        stream = [
            'data: {"id": "chatcmpl-1", "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "bash", "arguments": "{\\"command\\": \\"git status\\"}"}}]}}]}',
            "",
            'data: {"id": "chatcmpl-1", "choices": [{"index": 0, "delta": {"content": "Task completed successfully."}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 120, "completion_tokens": 35, "total_tokens": 155}}',
            "",
        ]
        res = runtimes.scan("goose", stream)
        self.assertEqual(res["tokens"], 155)
        self.assertEqual(res["tools"], 1)
        self.assertIsNone(res["conversation_id"])
        self.assertIsNotNone(res["result"])
        self.assertIsInstance(res["result"], str)
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertIn("Task completed successfully.", res["result"])
        assert res["tokens"] == 155
        assert res["tools"] == 1
        assert res["conversation_id"] is None
        assert res["result"] is not None
        assert isinstance(res["result"], str)
        assert res["result"]["status"] == "SUCCESS"
        assert "Task completed successfully." in res["result"]

    def test_goose_scan_sse_success_stream_anthropic(self):
        """scan('goose', ...) parses Anthropic Messages SSE stream exactly as fake-goose-server.js emits it."""
        stream = [
            'event: message_start',
            'data: {"type": "message_start", "message": {"id": "msg_01", "role": "assistant", "usage": {"input_tokens": 100, "output_tokens": 30}}}',
            "",
            'event: content_block_start',
            'data: {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_01", "name": "bash", "input": {}}}',
            "",
            'event: content_block_delta',
            'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "{\\"command\\":\\"git status\\"}"}}',
            "",
            'event: content_block_stop',
            'data: {"type": "content_block_stop", "index": 0}',
            "",
            'event: content_block_start',
            'data: {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}}',
            "",
            'event: content_block_delta',
            'data: {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Task completed successfully."}}',
            "",
            'event: content_block_stop',
            'data: {"type": "content_block_stop", "index": 1}',
            "",
            'event: message_delta',
            'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 30}}',
            "",
            'event: message_stop',
            'data: {"type": "message_stop"}',
            "",
        ]
        res = runtimes.scan("goose", stream)
        self.assertIsNotNone(res["tokens"])
        self.assertGreaterEqual(res["tokens"], 130)
        self.assertEqual(res["tools"], 1)
        self.assertIsNotNone(res["result"])
        self.assertIsInstance(res["result"], str)
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertIn("Task completed successfully.", res["result"])
        assert res["tokens"] is not None
        assert res["tools"] == 1
        assert res["result"] is not None
        assert isinstance(res["result"], str)
        assert res["result"]["status"] == "SUCCESS"
        assert "Task completed successfully." in res["result"]

    def test_goose_scan_sse_error_events(self):
        """scan('goose', ...) extracts error status and error messages from SSE error streams."""
        # 1. Event: error with error object
        stream_err = [
            'event: error',
            'data: {"error": {"message": "simulated goose endpoint error", "type": "invalid_request_error", "code": "simulated_error"}}',
            "",
        ]
        res1 = runtimes.scan("goose", stream_err)
        self.assertIsNotNone(res1["result"])
        self.assertIsInstance(res1["result"], str)
        self.assertEqual(res1["result"]["status"], "ERROR")
        self.assertIn("simulated goose endpoint error", res1["result"])
        self.assertIn("simulated goose endpoint error", res1["result"]["error"])
        assert res1["result"] is not None
        assert isinstance(res1["result"], str)
        assert res1["result"]["status"] == "ERROR"
        assert "simulated goose endpoint error" in res1["result"]

        # 2. Event: turn.failed with error message
        stream_failed = [
            'event: turn.failed',
            'data: {"error": "Turn execution failed unexpectedly"}',
            "",
        ]
        res2 = runtimes.scan("goose", stream_failed)
        self.assertIsNotNone(res2["result"])
        self.assertEqual(res2["result"]["status"], "ERROR")
        self.assertIn("Turn execution failed unexpectedly", res2["result"])
        assert res2["result"] is not None
        assert res2["result"]["status"] == "ERROR"
        assert "Turn execution failed unexpectedly" in res2["result"]

        # 3. Terminal event with status failed
        stream_status_failed = [
            'event: stop',
            'data: {"status": "failed", "error": "Internal worker error"}',
            "",
        ]
        res3 = runtimes.scan("goose", stream_status_failed)
        self.assertIsNotNone(res3["result"])
        self.assertEqual(res3["result"]["status"], "ERROR")
        self.assertIn("Internal worker error", res3["result"])
        assert res3["result"] is not None
        assert res3["result"]["status"] == "ERROR"
        assert "Internal worker error" in res3["result"]

    def test_goose_scan_sse_malformed_frames_skipped(self):
        """scan('goose', ...) skips malformed, partial, non-JSON and corrupt SSE frames without crashing."""
        noisy_stream = [
            "",
            "event: ",
            "data: {truncated json without closing brace",
            ": this is an sse comment line",
            "data: not json at all",
            "data: [1, 2, 3]",
            "data: 42",
            "data: true",
            "data: null",
            b"b'\\x80\\x81\\xff\\xfe corrupt bytes'",
            "event: content_block_start",
            'data: {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "t1", "name": "bash"}}',
            "",
            "data: {corrupt: json: without: quotes}",
            "data: ",
            "event: content_block_delta",
            'data: {"type": "content_block_delta", "delta": {"text_delta": "Recovered from malformed frames."}}',
            "",
            "event: message_stop",
            'data: {"type": "message_stop"}',
            "",
        ]
        res = runtimes.scan("goose", noisy_stream)
        self.assertEqual(res["tools"], 1)
        self.assertIsNotNone(res["result"])
        self.assertIsInstance(res["result"], str)
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertIn("Recovered from malformed frames.", res["result"])
        assert res["tools"] == 1
        assert res["result"] is not None
        assert isinstance(res["result"], str)
        assert res["result"]["status"] == "SUCCESS"
        assert "Recovered from malformed frames." in res["result"]

    def test_goose_scan_sse_intermediate_stream_without_terminal_event(self):
        """scan('goose', ...) returns result=None until a genuine terminal event is received."""
        intermediate = [
            'event: message_start',
            'data: {"type": "message_start"}',
            "",
            'event: content_block_delta',
            'data: {"type": "content_block_delta", "delta": {"text_delta": "Generation in progress..."}}',
            "",
        ]
        res = runtimes.scan("goose", intermediate)
        self.assertIsNone(res["result"])
        self.assertIsNone(res["tokens"])
        assert res["result"] is None
        assert res["tokens"] is None

    def test_goose_scan_sse_entirely_malformed_stream(self):
        """scan('goose', ...) safely handles entirely corrupt SSE streams returning result=None."""
        corrupt = [
            "data: {truncated json",
            "event: invalid_event_header",
            "just garbage plaintext inside sse expectation",
            "data: [broken, array]",
        ]
        res = runtimes.scan("goose", corrupt)
        self.assertIsNone(res["result"])
        self.assertEqual(res["tools"], 0)
        assert res["result"] is None
        assert res["tools"] == 0

    def test_goose_scan_sse_done_marker_terminal(self):
        """scan('goose', ...) recognizes [DONE] as a genuine terminal event."""
        stream = [
            'data: {"choices": [{"delta": {"content": "Finished all work."}}]}',
            "",
            "data: [DONE]",
            "",
        ]
        res = runtimes.scan("goose", stream)
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertIn("Finished all work.", res["result"])
        assert res["result"] is not None
        assert res["result"]["status"] == "SUCCESS"
        assert "Finished all work." in res["result"]


if __name__ == "__main__":
    unittest.main()
