#!/usr/bin/env python3
"""Tests for anthropic-compat runtime in bin/lib/runtimes.py and bin/doctor.

Public interface:
- Run directly: python3 tests/test_runtime_anthropic_compat.py
- Run via unittest: python3 -m unittest tests/test_runtime_anthropic_compat.py

Important invariants:
- RUNTIMES['anthropic-compat'] is registered using API_IMAGE with endpoint=True, api=True.
- uses_endpoint('anthropic-compat') returns True; is_api('anthropic-compat') returns True.
- hosts('anthropic-compat', url) resolves host from endpoint URL exactly like compat.
- command('anthropic-compat', ...) builds Anthropic Messages JSON payload on stdin, never in argv.
- command('anthropic-compat', ...) maps OpenAI-shape conversation history (no system role in messages).
- command('anthropic-compat', ...) never leaks API keys or secrets in argv.
- scan('anthropic-compat', ...) parses usage.input_tokens and usage.output_tokens into tokens.
- scan('anthropic-compat', ...) returns {tokens, tools, conversation_id, result} where result is None until final event.
- Cost model is unknown (cost_usd is None) unless prices config is supplied.
- Existing runtime behavior is completely preserved.
- bin/doctor contains checks for anthropic-compat endpoint and API key.
"""
import json
import os
import re
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


class TestAnthropicCompatRuntime(unittest.TestCase):
    """Unit tests for anthropic-compat runtime definition, command, scan, and doctor check."""

    def test_anthropic_compat_runtime_entry(self):
        """RUNTIMES['anthropic-compat'] must be registered with API_IMAGE, api=True, and endpoint=True."""
        self.assertIn("anthropic-compat", runtimes.RUNTIMES)
        entry = runtimes.RUNTIMES["anthropic-compat"]
        self.assertEqual(entry["image"], runtimes.API_IMAGE)
        self.assertEqual(entry["key"], "compat.key")
        self.assertEqual(entry["hosts"], [])
        self.assertTrue(entry["api"])
        self.assertTrue(entry.get("endpoint"))
        self.assertTrue(entry.get("optional_key"))
        self.assertEqual(entry.get("prices"), "PRICE_COMPAT")
        self.assertTrue(runtimes.is_api("anthropic-compat"))
        self.assertTrue(runtimes.uses_endpoint("anthropic-compat"))
        assert runtimes.is_api("anthropic-compat") is True
        assert runtimes.uses_endpoint("anthropic-compat") is True

    def test_anthropic_compat_hosts_resolution(self):
        """hosts('anthropic-compat', url) must resolve host from endpoint URL like compat."""
        url = "http://192.168.1.50:8000/v1"
        self.assertEqual(runtimes.hosts("anthropic-compat", url), ["192.168.1.50"])
        self.assertEqual(runtimes.hosts("anthropic-compat", "https://api.anthropic.com/v1"), ["api.anthropic.com"])
        self.assertEqual(runtimes.hosts("anthropic-compat", ""), [])
        self.assertEqual(runtimes.hosts("anthropic-compat", "ftp://invalid"), [])
        assert runtimes.hosts("anthropic-compat", url) == ["192.168.1.50"]
        assert runtimes.hosts("anthropic-compat", "") == []

    def test_anthropic_compat_command_argv_and_stdin(self):
        """command('anthropic-compat', ...) returns argv with prompt in JSON body on stdin, never in argv."""
        prompt = "Implement the new authentication endpoint"
        model = "claude-3-7-sonnet"
        argv, stdin_text = runtimes.command("anthropic-compat", prompt, model, None, 15, 0.0)

        self.assertEqual(argv[0], "af-agent")
        self.assertEqual(argv[1], "anthropic-compat")
        self.assertNotIn(prompt, argv)
        assert argv[0] == "af-agent"
        assert argv[1] == "anthropic-compat"
        assert prompt not in argv

        body = json.loads(stdin_text)
        self.assertEqual(body["model"], model)
        self.assertIn("messages", body)
        self.assertEqual(len(body["messages"]), 1)
        self.assertEqual(body["messages"][0]["role"], "user")
        self.assertEqual(body["messages"][0]["content"], prompt)
        assert body["model"] == model
        assert body["messages"][0]["content"] == prompt

    def test_anthropic_compat_command_no_secrets_in_argv(self):
        """command('anthropic-compat', ...) never leaks API key or secrets in argv elements."""
        secret_key = "secret_api_key_test_value"
        prompt = f"Use secret {secret_key}"
        argv, stdin_text = runtimes.command("anthropic-compat", prompt, "claude-3-5-sonnet")

        for arg in argv:
            self.assertNotIn("x-api-key", arg)
            self.assertNotIn("api-key", arg)
            self.assertNotIn(secret_key, arg)
            assert "x-api-key" not in arg
            assert "api-key" not in arg

        self.assertNotIn(secret_key, argv)
        assert secret_key not in argv

    def test_anthropic_compat_command_openai_conversation_history_mapping(self):
        """command('anthropic-compat', ...) maps OpenAI conversation history into Anthropic messages array without system role."""
        openai_history = [
            {"role": "system", "content": "You are a professional software engineer."},
            {"role": "user", "content": "Can you review this code?"},
            {"role": "assistant", "content": "Yes, please provide the code."},
        ]
        prompt = "Here is the code: def foo(): pass"
        argv, stdin_text = runtimes.command("anthropic-compat", prompt, "claude-3-5-sonnet", conversation=openai_history)

        body = json.loads(stdin_text)
        self.assertEqual(body["system"], "You are a professional software engineer.")
        self.assertNotIn({"role": "system"}, body["messages"])
        for msg in body["messages"]:
            self.assertNotEqual(msg["role"], "system")
            self.assertIn(msg["role"], ("user", "assistant"))
            assert msg["role"] != "system"

        self.assertEqual(len(body["messages"]), 3)
        self.assertEqual(body["messages"][0]["role"], "user")
        self.assertEqual(body["messages"][0]["content"], "Can you review this code?")
        self.assertEqual(body["messages"][1]["role"], "assistant")
        self.assertEqual(body["messages"][1]["content"], "Yes, please provide the code.")
        self.assertEqual(body["messages"][2]["role"], "user")
        self.assertEqual(body["messages"][2]["content"], prompt)
        assert body["system"] == "You are a professional software engineer."
        assert len(body["messages"]) == 3

    def test_anthropic_compat_command_json_string_conversation(self):
        """command('anthropic-compat', ...) handles JSON-serialized OpenAI conversation history string."""
        json_history = json.dumps([
            {"role": "system", "content": "System prompt text"},
            {"role": "user", "content": "User question"},
            {"role": "assistant", "content": "Assistant answer"},
        ])
        prompt = "Next turn"
        argv, stdin_text = runtimes.command("anthropic-compat", prompt, "claude-3-5-sonnet", conversation=json_history)

        body = json.loads(stdin_text)
        self.assertEqual(body["system"], "System prompt text")
        self.assertEqual(len(body["messages"]), 3)
        self.assertEqual(body["messages"][-1]["content"], prompt)
        for msg in body["messages"]:
            self.assertNotEqual(msg["role"], "system")
            assert msg["role"] != "system"
        assert body["system"] == "System prompt text"

    def test_anthropic_compat_command_defaults_and_session_id(self):
        """command('anthropic-compat', ...) uses default model and records string session ID."""
        argv, stdin_text = runtimes.command("anthropic-compat", "hello", "", conversation="ses_test123")
        body = json.loads(stdin_text)
        self.assertTrue(len(body["model"]) > 0)
        self.assertEqual(body["system"], "")
        self.assertEqual(body["conversation_id"], "ses_test123")
        assert len(body["model"]) > 0
        assert body["conversation_id"] == "ses_test123"

    def test_anthropic_compat_scan_streaming_events(self):
        """scan('anthropic-compat', ...) parses SSE events stream into tokens, tools, conversation_id, result."""
        stream = [
            'data: {"type": "message_start", "message": {"id": "msg_001", "type": "message", "role": "assistant", "usage": {"input_tokens": 1200}}}',
            '{"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest"}}}',
            '{"type": "content_block_stop", "index": 0}',
            '{"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}}',
            '{"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "All 15 tests "}}',
            '{"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "passed successfully."}}',
            '{"type": "content_block_stop", "index": 1}',
            '{"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 150}}',
            '{"type": "message_stop"}',
        ]
        res = runtimes.scan("anthropic-compat", stream)
        self.assertEqual(res["tokens"], 1350)
        self.assertEqual(res["tools"], 1)
        self.assertEqual(res["conversation_id"], "msg_001")
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertEqual(res["result"]["response"], "All 15 tests passed successfully.")
        self.assertIsNone(res["result"]["cost_usd"])
        assert res["tokens"] == 1350
        assert res["tools"] == 1
        assert res["conversation_id"] == "msg_001"
        assert res["result"]["status"] == "SUCCESS"
        assert res["result"]["response"] == "All 15 tests passed successfully."

    def test_anthropic_compat_scan_non_streaming_response(self):
        """scan('anthropic-compat', ...) parses full non-streaming message response object."""
        non_streaming = [
            json.dumps({
                "id": "msg_full_123",
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tu_1", "name": "bash", "input": {}},
                    {"type": "text", "text": "Task completed."},
                ],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 800, "output_tokens": 200},
            })
        ]
        res = runtimes.scan("anthropic-compat", non_streaming)
        self.assertEqual(res["tokens"], 1000)
        self.assertEqual(res["tools"], 1)
        self.assertEqual(res["conversation_id"], "msg_full_123")
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertEqual(res["result"]["response"], "Task completed.")
        assert res["tokens"] == 1000
        assert res["tools"] == 1
        assert res["conversation_id"] == "msg_full_123"

    def test_anthropic_compat_scan_result_none_until_final(self):
        """scan('anthropic-compat', ...) returns result=None until final completion event is received."""
        intermediate = [
            '{"type": "message_start", "message": {"id": "msg_inc", "usage": {"input_tokens": 500}}}',
            '{"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": "Draft"}}',
        ]
        res = runtimes.scan("anthropic-compat", intermediate)
        self.assertEqual(res["tokens"], 500)
        self.assertEqual(res["conversation_id"], "msg_inc")
        self.assertIsNone(res["result"])
        assert res["result"] is None
        assert res["tokens"] == 500

    def test_anthropic_compat_scan_error_event(self):
        """scan('anthropic-compat', ...) captures error events and sets status=ERROR."""
        err_stream = [
            '{"type": "error", "error": {"type": "overloaded_error", "message": "Server overloaded, retry later"}}',
        ]
        res = runtimes.scan("anthropic-compat", err_stream)
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "ERROR")
        self.assertIn("Server overloaded", res["result"]["error"])
        assert res["result"]["status"] == "ERROR"
        assert "Server overloaded" in res["result"]["error"]

    def test_anthropic_compat_scan_cost_with_prices(self):
        """scan('anthropic-compat', ...) calculates cost_usd when prices config is provided."""
        stream = [
            json.dumps({
                "id": "msg_cost",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "Done"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1000, "output_tokens": 500},
            })
        ]
        # Rate: 2.0 input, 0.5 cached, 10.0 output per million tokens
        res = runtimes.scan("anthropic-compat", stream, prices="2.0,0.5,10.0")
        expected_cost = round((1000 * 2.0 + 500 * 10.0) / 1e6, 6)
        self.assertEqual(res["result"]["cost_usd"], expected_cost)
        assert res["result"]["cost_usd"] == expected_cost

        # Rate: USD per 1K tokens rate (e.g. 0.003 USD / 1K tokens)
        res_per_k = runtimes.scan("anthropic-compat", stream, prices="0.003")
        expected_k_cost = round((1500 * 0.003) / 1000.0, 6)
        self.assertEqual(res_per_k["result"]["cost_usd"], expected_k_cost)
        assert res_per_k["result"]["cost_usd"] == expected_k_cost

    def test_anthropic_compat_scan_file_helper(self):
        """scan_file('anthropic-compat', path) properly parses lines from file."""
        events = [
            json.dumps({
                "id": "msg_file",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "File test done"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 400, "output_tokens": 100},
            })
        ]
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as tf:
            for ev in events:
                tf.write(ev + "\n")
            tf.flush()
            parsed = runtimes.scan_file("anthropic-compat", tf.name)

        self.assertEqual(parsed["tokens"], 500)
        self.assertEqual(parsed["conversation_id"], "msg_file")
        self.assertEqual(parsed["result"]["response"], "File test done")
        assert parsed["tokens"] == 500
        assert parsed["conversation_id"] == "msg_file"

    def test_existing_runtimes_behavior_preserved(self):
        """All existing runtimes preserve their registered behavior and properties."""
        self.assertFalse(runtimes.is_api("agy"))
        self.assertTrue(runtimes.is_api("claude"))
        self.assertTrue(runtimes.is_api("codex"))
        self.assertTrue(runtimes.is_api("compat"))
        self.assertTrue(runtimes.is_api("aider"))
        self.assertFalse(runtimes.is_api("opencode"))
        self.assertFalse(runtimes.is_api("qwen-code"))

        self.assertTrue(runtimes.uses_endpoint("compat"))
        self.assertTrue(runtimes.uses_endpoint("aider"))
        self.assertTrue(runtimes.uses_endpoint("opencode"))
        self.assertFalse(runtimes.uses_endpoint("qwen-code"))
        self.assertFalse(runtimes.uses_endpoint("claude"))

        compat_cmd, compat_stdin = runtimes.command("compat", "prompt", "model")
        self.assertEqual(compat_stdin, "prompt")
        self.assertNotIn("prompt", compat_cmd)
        self.assertEqual(compat_cmd[:3], ["af-agent", "compat", "exec"])
        assert compat_cmd[:3] == ["af-agent", "compat", "exec"]
        assert compat_stdin == "prompt"

    def test_bin_doctor_anthropic_compat_checks(self):
        """bin/doctor contains dedicated check for anthropic-compat endpoint and key."""
        doc_path = os.path.join(ROOT, "bin", "doctor")
        with open(doc_path, "r", encoding="utf-8") as f:
            src = f.read()

        self.assertIn("anthropic-compat", src)
        self.assertIn("COMPAT_BASE_URL", src)
        self.assertIn("compat.key", src)
        assert "anthropic-compat" in src
        assert "COMPAT_BASE_URL" in src


if __name__ == "__main__":
    unittest.main()
