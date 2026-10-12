#!/usr/bin/env python3
"""Tests for OpenCode runtime in bin/lib/runtimes.py against offline fake server events.

Public interface:
- Run directly: python3 tests/test_runtime_opencode.py
- Run via unittest: python3 -m unittest tests/test_runtime_opencode.py

Important invariants:
- RUNTIMES['opencode'] is a subscription runtime using AGY_IMAGE with endpoint=True.
- uses_endpoint('opencode') returns True; hosts('opencode', url) resolves host from endpoint.
- command('opencode', ...) passes prompt on stdin and never in argv.
- scan('opencode', ...) returns tokens: None for honest unknown cost model, parses conversation_id,
  counts tools, handles malformed/corrupt lines without crashing, and handles error events safely.
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
    """Unit tests for OpenCode runtime definition, commands, and output scanning."""

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

    def test_opencode_command_argv_safety_with_unicode_and_long_prompts(self):
        """command('opencode', ...) keeps multi-line unicode prompt on stdin and never leaks it in argv."""
        prompt = "Long prompt with unicode: 🚀 测试 OpenCode 客户端\nLine 2\nLine 3\n" * 50
        argv, stdin_text = runtimes.command("opencode", prompt, "anthropic/claude-3-5-sonnet", "ses_test", 10, 0.0)
        self.assertEqual(stdin_text, prompt)
        self.assertNotIn("🚀", " ".join(argv))
        self.assertNotIn("测试", " ".join(argv))
        assert stdin_text == prompt
        assert "🚀" not in " ".join(argv)

    def test_opencode_scan_success_representative_fixture_stream(self):
        """scan('opencode', lines) parses representative stream from fake-opencode-server.js correctly."""
        stream = [
            '{"type": "step_start", "sessionID": "ses_opencode_test_01", "part": {"id": "part_start_01"}}',
            '{"type": "tool_use", "name": "bash", "sessionID": "ses_opencode_test_01", "part": {"type": "tool", "tool": "bash", "command": "echo \\"hello from opencode\\" > hello.txt"}}',
            '{"type": "text", "sessionID": "ses_opencode_test_01", "part": {"text": "Done: wrote hello.txt."}}',
            '{"type": "step_finish", "sessionID": "ses_opencode_test_01", "part": {"reason": "stop"}}',
        ]
        res = runtimes.scan("opencode", stream)
        self.assertIsNone(res["tokens"])
        self.assertEqual(res["conversation_id"], "ses_opencode_test_01")
        self.assertEqual(res["tools"], 1)
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertEqual(res["result"]["response"], "Done: wrote hello.txt.")
        self.assertEqual(res["result"]["error"], "")
        self.assertIsNone(res["result"]["cost_usd"])
        assert res["tokens"] is None
        assert res["conversation_id"] == "ses_opencode_test_01"
        assert res["tools"] == 1
        assert res["result"] is not None
        assert res["result"]["status"] == "SUCCESS"
        assert res["result"]["response"] == "Done: wrote hello.txt."

    def test_opencode_scan_error_events_without_raising(self):
        """scan('opencode', ...) reports failure events without raising exceptions."""
        # 1. Step finish error event from fake-opencode-server.js
        fixture_err_stream = [
            '{"type": "step_start", "sessionID": "ses_err_01"}',
            '{"type": "step_finish", "sessionID": "ses_err_01", "part": {"reason": "error", "error": "simulated rate limit"}}',
        ]
        res1 = runtimes.scan("opencode", fixture_err_stream)
        self.assertIsNotNone(res1["result"])
        self.assertEqual(res1["result"]["status"], "ERROR")
        self.assertIn("simulated rate limit", res1["result"]["error"])
        assert res1["result"]["status"] == "ERROR"
        assert "simulated rate limit" in res1["result"]["error"]

        # 2. General error event type
        err_event_stream = [
            '{"type": "step_start", "sessionID": "ses_err_02"}',
            '{"type": "error", "message": "API key revoked"}',
        ]
        res2 = runtimes.scan("opencode", err_event_stream)
        self.assertIsNotNone(res2["result"])
        self.assertEqual(res2["result"]["status"], "ERROR")
        self.assertEqual(res2["result"]["error"], "API key revoked")
        assert res2["result"]["status"] == "ERROR"
        assert res2["result"]["error"] == "API key revoked"

        # 3. turn.failed event type
        turn_failed_stream = [
            '{"type": "step_start", "sessionID": "ses_err_03"}',
            '{"type": "turn.failed", "error": {"message": "Execution timeout"}}',
        ]
        res3 = runtimes.scan("opencode", turn_failed_stream)
        self.assertIsNotNone(res3["result"])
        self.assertEqual(res3["result"]["status"], "ERROR")
        self.assertEqual(res3["result"]["error"], "Execution timeout")
        assert res3["result"]["status"] == "ERROR"
        assert res3["result"]["error"] == "Execution timeout"

        # 4. step_failed event type
        step_failed_stream = [
            '{"type": "step_start", "sessionID": "ses_err_04"}',
            '{"type": "step_failed", "error": "Process killed by signal"}',
        ]
        res4 = runtimes.scan("opencode", step_failed_stream)
        self.assertIsNotNone(res4["result"])
        self.assertEqual(res4["result"]["status"], "ERROR")
        self.assertEqual(res4["result"]["error"], "Process killed by signal")
        assert res4["result"]["status"] == "ERROR"
        assert res4["result"]["error"] == "Process killed by signal"

    def test_opencode_scan_malformed_and_corrupt_lines_skipped(self):
        """scan('opencode', ...) skips malformed, corrupt, non-JSON and non-dict lines without crashing."""
        noisy_stream = [
            "",  # empty string
            "   ",  # whitespace
            "not a json object at all",  # plain non-json text
            '{"type": "step_start", "sessionID": "ses_noisy_01", "part": {',  # truncated json
            "[1, 2, 3]",  # json list (not dict)
            '"just a json string"',  # json primitive string
            "12345",  # json number
            "true",  # json boolean
            "null",  # json null
            "{}",  # empty json object
            b'{"type": "step_start", "sessionID": "ses_noisy_01", "part": {"id": "p1"}}',  # bytes line
            b'\x80\x81\xff\xfe corrupt bytes',  # invalid utf-8 bytes
            '{"type": "tool_use", "name": "bash", "sessionID": "ses_noisy_01"}',  # valid event
            '<<< GARBAGE LINE >>>',
            '{"type": "text", "sessionID": "ses_noisy_01", "part": {"text": "Recovered from noisy stream."}}',
            '{corrupt: json: without: quotes}',
            '{"type": "step_finish", "sessionID": "ses_noisy_01", "part": {"reason": "stop"}}',
            "",
        ]
        res = runtimes.scan("opencode", noisy_stream)
        self.assertIsNone(res["tokens"])
        self.assertEqual(res["conversation_id"], "ses_noisy_01")
        self.assertEqual(res["tools"], 1)
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertEqual(res["result"]["response"], "Recovered from noisy stream.")
        assert res["tokens"] is None
        assert res["conversation_id"] == "ses_noisy_01"
        assert res["tools"] == 1
        assert res["result"] is not None
        assert res["result"]["status"] == "SUCCESS"
        assert res["result"]["response"] == "Recovered from noisy stream."

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

    def test_opencode_scan_session_id_field_variants_and_first_occurrence(self):
        """scan('opencode', ...) extracts session ID across key variants and preserves first occurrence."""
        variants = [
            ({"sessionID": "sid_1"}, "sid_1"),
            ({"session_id": "sid_2"}, "sid_2"),
            ({"sessionId": "sid_3"}, "sid_3"),
            ({"session": "sid_4"}, "sid_4"),
            ({"conversation_id": "sid_5"}, "sid_5"),
            ({"part": {"sessionID": "sid_6"}}, "sid_6"),
            ({"part": {"session_id": "sid_7"}}, "sid_7"),
        ]
        for payload, expected in variants:
            ev = {"type": "step_start", **payload}
            r = runtimes.scan("opencode", [json.dumps(ev)])
            self.assertEqual(r["conversation_id"], expected)
            assert r["conversation_id"] == expected

        # First occurrence is preserved across subsequent events with different session IDs
        multi_stream = [
            json.dumps({"type": "step_start", "sessionID": "first_session"}),
            json.dumps({"type": "tool_use", "sessionID": "second_session"}),
            json.dumps({"type": "step_finish", "sessionID": "third_session", "part": {"reason": "stop"}}),
        ]
        res_multi = runtimes.scan("opencode", multi_stream)
        self.assertEqual(res_multi["conversation_id"], "first_session")
        assert res_multi["conversation_id"] == "first_session"

    def test_opencode_scan_tool_counting_variants(self):
        """scan('opencode', ...) correctly counts tool invocations across event types and part structures."""
        tool_events = [
            {"type": "tool_use", "name": "bash"},
            {"type": "tool_call", "name": "editor"},
            {"type": "tool", "name": "grep"},
            {"type": "tool_invocation", "name": "git"},
            {"type": "other", "part": {"type": "tool"}},
            {"type": "other", "part": {"type": "tool_use"}},
            {"type": "other", "part": {"type": "tool_call"}},
            {"type": "other", "part": {"type": "tool_invocation"}},
            {"type": "other", "part": {"tool": "pytest"}},
        ]
        lines = [json.dumps(ev) for ev in tool_events]
        res = runtimes.scan("opencode", lines)
        self.assertEqual(res["tools"], len(tool_events))
        assert res["tools"] == len(tool_events)

    def test_opencode_scan_text_accumulation_and_truncation(self):
        """scan('opencode', ...) accumulates message text across deltas and truncates response to 4000 chars."""
        long_chunk = "A" * 3000
        stream = [
            json.dumps({"type": "step_start", "sessionID": "ses_txt"}),
            json.dumps({"type": "text", "part": {"text": "Prefix line."}}),
            json.dumps({"type": "message", "message": {"content": [{"text": " Middle chunk."}]}}),
            json.dumps({"type": "message.part.delta", "part": {"text": " Delta chunk."}}),
            json.dumps({"type": "assistant.message", "text": f"\n{long_chunk}\nSuffix line."}),
            json.dumps({"type": "step_finish", "part": {"reason": "stop"}}),
        ]
        res = runtimes.scan("opencode", stream)
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertLessEqual(len(res["result"]["response"]), 4000)
        self.assertTrue(res["result"]["response"].endswith("Suffix line."))
        assert res["result"] is not None
        assert res["result"]["status"] == "SUCCESS"
        assert len(res["result"]["response"]) <= 4000
        assert res["result"]["response"].endswith("Suffix line.")

    def test_opencode_scan_tokens_when_usage_reported(self):
        """scan('opencode', ...) extracts tokens when explicit usage dict or integer is present in events."""
        stream_dict_usage = [
            json.dumps({"type": "step_start", "sessionID": "ses_tok_01"}),
            json.dumps({"type": "step_finish", "part": {"reason": "stop", "tokens": {"input": 100, "output": 50, "total": 150}}}),
        ]
        res1 = runtimes.scan("opencode", stream_dict_usage)
        self.assertEqual(res1["tokens"], 150)
        assert res1["tokens"] == 150

        stream_int_usage = [
            json.dumps({"type": "step_start", "sessionID": "ses_tok_02"}),
            json.dumps({"type": "step_finish", "tokens": 250, "part": {"reason": "stop"}}),
        ]
        res2 = runtimes.scan("opencode", stream_int_usage)
        self.assertEqual(res2["tokens"], 250)
        assert res2["tokens"] == 250

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

    def test_opencode_scan_file_missing_file_graceful(self):
        """scan_file('opencode', non_existent_path) handles missing file without crashing."""
        res = runtimes.scan_file("opencode", "/tmp/does_not_exist_opencode_stream.log")
        self.assertIsNone(res["tokens"])
        self.assertEqual(res["tools"], 0)
        self.assertIsNone(res["conversation_id"])
        self.assertIsNone(res["result"])
        assert res["tokens"] is None
        assert res["tools"] == 0
        assert res["result"] is None

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
