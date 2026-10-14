#!/usr/bin/env python3
"""Tests for GitHub Copilot runtime in bin/lib/runtimes.py against fake-copilot-server.js.

Public interface:
- Run directly: python3 tests/test_runtime_copilot.py
- Run via unittest: python3 -m unittest tests/test_runtime_copilot.py
- Run via pytest: python3 -m pytest -q tests/test_runtime_copilot.py

Important invariants:
- RUNTIMES['copilot'] is registered with image=AGY_IMAGE, key=None, hosts=['api.githubcopilot.com', 'api.github.com'], api=False.
- hosts('copilot') returns ['api.githubcopilot.com', 'api.github.com'] and cost_model('copilot') reports unknown pricing honestly.
- command('copilot', ...) passes prompt on stdin, never in argv, with quiet flags and optional --model/--resume.
- scan('copilot', ...) parses stream-json events from fake-copilot-server.js without crashing.
- Skips malformed/garbage lines without raising, preserving terminal result status, zero network calls and no real keys.
- Requires node on PATH to run fake-copilot-server.js; skips fixture tests cleanly when node is unavailable.

Grounded from bin/lib/runtimes.py (Task 0212):
- RUNTIMES entry (lines 60-62): key=None, hosts=["api.githubcopilot.com", "api.github.com"], api=False
- COPILOT_QUIET (lines 79-80): ["--output-format", "stream-json"]
- cost_model() (lines 126-129): kind="unknown", detail="GitHub Copilot (copilot) is covered by a GitHub Copilot subscription; token pricing is not tracked in Agent-Forge"
- command() (lines 237-241): argv=["copilot"] + COPILOT_QUIET + [--model] + [--resume], stdin=prompt
- scan() (lines 563, 926-999): initial tokens=None, session from sessionID/sessionId/session_id/conversation_id/session/thread_id,
  tool counts, text extraction, usage tokens, step_finish/result/error terminal states, cost_usd is None.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))

# Top-level smoke-import check to fail loudly if import breaks
try:
    import runtimes
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.stderr.write(f"smoke-import failed for runtimes: {e}\n")
    sys.exit(1)

NODE_AVAILABLE = bool(shutil.which("node") or os.environ.get("COPILOT_SERVER_PORT"))


class TestCopilotRuntime(unittest.TestCase):
    """Unit tests for GitHub Copilot runtime against fake server fixture."""

    server_proc = None
    server_port = None
    server_temp_dir = None

    @classmethod
    def _find_free_port(cls):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    @classmethod
    def _start_fake_server(cls, port):
        fixture_path = os.path.join(ROOT, "tests", "fixtures", "fake-copilot-server.js")
        cls.server_proc = subprocess.Popen(
            ["node", fixture_path, str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @classmethod
    def _wait_for_server(cls, port):
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return
            except (OSError, ConnectionRefusedError):
                time.sleep(0.05)
        raise RuntimeError(f"Fake Copilot server failed to accept connections on port {port}")

    @classmethod
    def setUpClass(cls):
        port_env = os.environ.get("COPILOT_SERVER_PORT")
        if port_env:
            cls.server_port = int(port_env)
            cls._wait_for_server(cls.server_port)
        elif shutil.which("node"):
            cls.server_port = cls._find_free_port()
            cls._start_fake_server(cls.server_port)
            os.environ["COPILOT_SERVER_PORT"] = str(cls.server_port)
            cls._wait_for_server(cls.server_port)
        else:
            cls.server_port = None

        # Ensure copilot stub is on PATH if server is available and stub not present
        if cls.server_port and not shutil.which("copilot"):
            cls.server_temp_dir = tempfile.TemporaryDirectory()
            stub_path = os.path.join(cls.server_temp_dir.name, "copilot")
            with open(stub_path, "w", encoding="utf-8") as f:
                f.write(f"""#!/usr/bin/env python3
import json, os, sys, urllib.request
port = os.environ.get("COPILOT_SERVER_PORT", "{cls.server_port}")
url = f"http://127.0.0.1:{{port}}/run"
prompt = sys.stdin.read()
args = sys.argv[1:]
session_id = None
model = None
for i, arg in enumerate(args):
    if arg in ("--resume", "-r") and i + 1 < len(args):
        session_id = args[i + 1]
    elif arg in ("-m", "--model") and i + 1 < len(args):
        model = args[i + 1]
if "TRIGGER_ERROR" in prompt:
    url = f"http://127.0.0.1:{{port}}/error"
payload = json.dumps({{
    "prompt": prompt,
    "args": args,
    "sessionID": session_id,
    "model": model,
}}).encode("utf-8")
req = urllib.request.Request(url, data=payload, headers={{"Content-Type": "application/json"}})
with urllib.request.urlopen(req) as resp:
    for line in resp:
        sys.stdout.write(line.decode("utf-8"))
""")
            os.chmod(stub_path, 0o755)
            os.environ["PATH"] = f"{cls.server_temp_dir.name}:{os.environ.get('PATH', '')}"

    @classmethod
    def tearDownClass(cls):
        if cls.server_proc:
            try:
                cls.server_proc.terminate()
                cls.server_proc.wait(timeout=2)
            except Exception:
                pass
        if cls.server_temp_dir:
            try:
                cls.server_temp_dir.cleanup()
            except Exception:
                pass

    def _fetch_events(self, endpoint="/run", payload=None):
        url = f"http://127.0.0.1:{self.server_port}{endpoint}"
        data = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            return [line.decode("utf-8").strip() for line in resp if line.decode("utf-8").strip()]

    def test_copilot_runtime_metadata(self):
        """RUNTIMES['copilot'] metadata is properly configured with image, key=None, hosts and api=False."""
        self.assertIn("copilot", runtimes.RUNTIMES)
        entry = runtimes.RUNTIMES["copilot"]
        self.assertEqual(entry["image"], runtimes.AGY_IMAGE)
        self.assertIsNone(entry["key"])
        self.assertEqual(entry["hosts"], ["api.githubcopilot.com", "api.github.com"])
        self.assertFalse(entry["api"])
        self.assertFalse(runtimes.is_api("copilot"))
        self.assertFalse(runtimes.uses_endpoint("copilot"))
        self.assertEqual(runtimes.hosts("copilot"), ["api.githubcopilot.com", "api.github.com"])
        self.assertEqual(runtimes.hosts("copilot", endpoint_url="http://other:8080"), ["api.githubcopilot.com", "api.github.com"])
        assert runtimes.is_api("copilot") is False
        assert runtimes.uses_endpoint("copilot") is False
        assert "api.githubcopilot.com" in runtimes.hosts("copilot")
        assert "api.github.com" in runtimes.hosts("copilot")

    def test_copilot_cost_model(self):
        """cost_model('copilot') reports unknown pricing honestly without fabricated USD figure."""
        cm = runtimes.cost_model("copilot")
        self.assertIsInstance(cm, dict)
        self.assertEqual(set(cm.keys()), {"kind", "detail"})
        self.assertEqual(cm["kind"], "unknown")
        self.assertIn("GitHub Copilot", cm["detail"])
        self.assertIn("not tracked in Agent-Forge", cm["detail"])
        self.assertNotIn("USD", cm["detail"])
        self.assertNotIn("$", cm["detail"])
        assert cm["kind"] == "unknown"
        assert "not tracked" in cm["detail"]

    def test_copilot_command_argv_and_stdin(self):
        """command('copilot', ...) passes prompt on stdin, omitted from argv with quiet flags."""
        prompt = "Fix database transaction leak"
        model = "gpt-4o"
        conv = "ses_copilot_42"
        argv, stdin = runtimes.command("copilot", prompt, model, conversation=conv)
        self.assertEqual(stdin, prompt)
        self.assertNotIn(prompt, argv)
        self.assertEqual(argv[0], "copilot")
        for flag in runtimes.COPILOT_QUIET:
            self.assertIn(flag, argv)
        self.assertIn("--model", argv)
        self.assertIn(model, argv)
        self.assertIn("--resume", argv)
        self.assertIn(conv, argv)
        assert stdin == prompt
        assert prompt not in argv
        assert argv[0] == "copilot"

    def test_copilot_command_omits_optional_flags(self):
        """command('copilot', ...) omits --model and --resume when model and conversation are None."""
        argv, stdin = runtimes.command("copilot", "simple task", None, conversation=None)
        self.assertEqual(stdin, "simple task")
        self.assertNotIn("--model", argv)
        self.assertNotIn("--resume", argv)
        self.assertEqual(argv, ["copilot"] + runtimes.COPILOT_QUIET)
        assert "--model" not in argv
        assert "--resume" not in argv

    def test_copilot_command_never_leaks_secrets(self):
        """command('copilot', ...) never includes API keys or secret references in argv."""
        argv, stdin = runtimes.command("copilot", "security audit", "gpt-4o", budget_usd=5.0)
        argv_str = " ".join(argv)
        self.assertNotIn("github.token", argv_str)
        self.assertNotIn("GITHUB_TOKEN", argv_str)
        self.assertNotIn("copilot.key", argv_str)
        self.assertNotIn("ghp_", argv_str)
        self.assertNotIn("gho_", argv_str)
        self.assertNotIn("sk-", argv_str)
        assert "github.token" not in argv_str
        assert "ghp_" not in argv_str

    @unittest.skipUnless(NODE_AVAILABLE, "node not available to run fake-copilot-server.js")
    def test_copilot_command_end_to_end_execution(self):
        """command('copilot', ...) drives stub binary against fake server and scan() parses result."""
        if not self.server_port:
            self.skipTest("node not available to run fake-copilot-server.js")
        prompt = "End-to-end integration test run"
        argv, stdin = runtimes.command("copilot", prompt, "gpt-4o")
        proc = subprocess.run(argv, input=stdin, text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0)
        res = runtimes.scan("copilot", proc.stdout.splitlines())
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertEqual(res["result"]["response"], "Done: wrote hello.txt.")
        self.assertEqual(res["tools"], 1)
        self.assertIsNone(res["tokens"])
        self.assertIsNone(res["result"]["cost_usd"])
        self.assertEqual(res["conversation_id"], "ses_copilot_test_01")
        assert proc.returncode == 0
        assert res["result"]["status"] == "SUCCESS"
        assert res["result"]["response"] == "Done: wrote hello.txt."

    @unittest.skipUnless(NODE_AVAILABLE, "node not available to run fake-copilot-server.js")
    def test_copilot_command_resume_session_via_stub(self):
        """command('copilot', ...) resumes existing conversation session and scan() preserves conversation_id."""
        if not self.server_port:
            self.skipTest("node not available to run fake-copilot-server.js")
        conv_id = "ses_copilot_resumed_77"
        argv, stdin = runtimes.command("copilot", "Follow-up question", "gpt-4o", conversation=conv_id)
        proc = subprocess.run(argv, input=stdin, text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0)
        res = runtimes.scan("copilot", proc.stdout.splitlines())
        self.assertEqual(res["conversation_id"], conv_id)
        assert proc.returncode == 0
        assert res["conversation_id"] == conv_id

    @unittest.skipUnless(NODE_AVAILABLE, "node not available to run fake-copilot-server.js")
    def test_copilot_scan_success_default_events(self):
        """Normal successful run returns SUCCESS status with sensible tools and conversation_id."""
        if not self.server_port:
            self.skipTest("node not available to run fake-copilot-server.js")
        events = self._fetch_events("/run", {"sessionID": "ses_copilot_test_01"})
        res = runtimes.scan("copilot", events)
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertEqual(res["result"]["response"], "Done: wrote hello.txt.")
        self.assertEqual(res["result"]["error"], "")
        self.assertIsNone(res["tokens"])
        self.assertEqual(res["tools"], 1)
        self.assertEqual(res["conversation_id"], "ses_copilot_test_01")
        self.assertIsNone(res["result"]["cost_usd"])
        assert res["result"]["status"] == "SUCCESS"
        assert res["tools"] == 1

    @unittest.skipUnless(NODE_AVAILABLE, "node not available to run fake-copilot-server.js")
    def test_copilot_scan_error_events(self):
        """Error-event path returns ERROR status without crashing."""
        if not self.server_port:
            self.skipTest("node not available to run fake-copilot-server.js")
        events = self._fetch_events("/error", {"sessionID": "ses_copilot_err", "error": "simulated rate limit"})
        res = runtimes.scan("copilot", events)
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "ERROR")
        self.assertIn("simulated rate limit", res["result"]["error"])
        self.assertIsNone(res["result"]["cost_usd"])
        assert res["result"]["status"] == "ERROR"
        assert "simulated rate limit" in res["result"]["error"]

    def test_copilot_scan_turn_failed_and_step_failed_events(self):
        """scan('copilot', ...) parses turn.failed, step_failed, and failed events as ERROR status."""
        for ev_type in ("turn.failed", "step_failed", "failed", "error"):
            stream = [
                json.dumps({"type": "start", "sessionID": "ses_fail"}),
                json.dumps({"type": ev_type, "error": {"message": f"Execution failed via {ev_type}"}})
            ]
            res = runtimes.scan("copilot", stream)
            self.assertEqual(res["result"]["status"], "ERROR")
            self.assertIn(f"Execution failed via {ev_type}", res["result"]["error"])
            self.assertIsNone(res["result"]["cost_usd"])
            assert res["result"]["status"] == "ERROR"

    def test_copilot_scan_step_finish_variants(self):
        """scan('copilot', ...) handles different step_finish reasons and finish texts."""
        # reason='error'
        s_err = [
            json.dumps({"type": "step_finish", "part": {"reason": "error", "error": "Internal server error"}})
        ]
        r_err = runtimes.scan("copilot", s_err)
        self.assertEqual(r_err["result"]["status"], "ERROR")
        self.assertEqual(r_err["result"]["error"], "Internal server error")
        self.assertIsNone(r_err["result"]["cost_usd"])

        # reason='completed' with finish_txt
        s_ok = [
            json.dumps({"type": "step_finish", "part": {"reason": "completed", "text": "All tasks finished successfully"}})
        ]
        r_ok = runtimes.scan("copilot", s_ok)
        self.assertEqual(r_ok["result"]["status"], "SUCCESS")
        self.assertEqual(r_ok["result"]["response"], "All tasks finished successfully")
        self.assertIsNone(r_ok["result"]["cost_usd"])

        # reason='failure'
        s_fail = [
            json.dumps({"type": "step_finish", "part": {"reason": "failure", "error": {"message": "Execution timeout"}}})
        ]
        r_fail = runtimes.scan("copilot", s_fail)
        self.assertEqual(r_fail["result"]["status"], "ERROR")
        self.assertEqual(r_fail["result"]["error"], "Execution timeout")

        # is_error=True
        s_is_err = [
            json.dumps({"type": "step_finish", "is_error": True, "part": {"reason": "stop"}})
        ]
        r_is_err = runtimes.scan("copilot", s_is_err)
        self.assertEqual(r_is_err["result"]["status"], "ERROR")

        assert r_err["result"]["status"] == "ERROR"
        assert r_ok["result"]["status"] == "SUCCESS"
        assert r_fail["result"]["status"] == "ERROR"
        assert r_is_err["result"]["status"] == "ERROR"

    def test_copilot_scan_result_event_variants(self):
        """scan('copilot', ...) parses result events with various statuses and reasons."""
        # result status: ok
        stream_ok = [
            json.dumps({"type": "result", "status": "ok", "response": "Task completed"})
        ]
        res_ok = runtimes.scan("copilot", stream_ok)
        self.assertEqual(res_ok["result"]["status"], "SUCCESS")
        self.assertEqual(res_ok["result"]["response"], "Task completed")
        self.assertIsNone(res_ok["result"]["cost_usd"])

        # result status: error
        stream_err = [
            json.dumps({"type": "result", "status": "error", "error": "Quota exhausted"})
        ]
        res_err = runtimes.scan("copilot", stream_err)
        self.assertEqual(res_err["result"]["status"], "ERROR")
        self.assertIn("Quota exhausted", res_err["result"]["error"])

        # result reason: stop
        stream_stop = [
            json.dumps({"type": "result", "part": {"reason": "stop", "text": "Finished output"}})
        ]
        res_stop = runtimes.scan("copilot", stream_stop)
        self.assertEqual(res_stop["result"]["status"], "SUCCESS")
        self.assertEqual(res_stop["result"]["response"], "Finished output")

        assert res_ok["result"]["status"] == "SUCCESS"
        assert res_err["result"]["status"] == "ERROR"
        assert res_stop["result"]["status"] == "SUCCESS"

    def test_copilot_scan_cost_usd_always_none_even_with_prices(self):
        """scan('copilot', ...) preserves cost_usd as None even when prices argument is provided."""
        stream = [
            json.dumps({"type": "start", "sessionID": "ses_price_01"}),
            json.dumps({"type": "message", "part": {"text": "Task finished"}}),
            json.dumps({
                "type": "result",
                "status": "success",
                "usage": {
                    "input_tokens": 1200,
                    "cached_tokens": 200,
                    "output_tokens": 300,
                    "total_tokens": 1500,
                }
            })
        ]
        res = runtimes.scan("copilot", stream, prices="2.0,0.5,6.0")
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertIsNone(res["result"]["cost_usd"])
        self.assertEqual(res["tokens"], 1300)

        res_no_price = runtimes.scan("copilot", stream)
        self.assertIsNone(res_no_price["result"]["cost_usd"])
        assert res["result"]["cost_usd"] is None
        assert res_no_price["result"]["cost_usd"] is None

    def test_copilot_scan_malformed_lines_skipped(self):
        """Malformed and garbage lines in the event stream are skipped without raising."""
        corrupted_events = [
            "NOT_A_JSON_LINE",
            "{broken json",
            "[1, 2, 3]",
            "42",
            "   ",
            json.dumps({"type": "start", "sessionID": "ses_copilot_malformed"}),
            "corrupted middle line",
            json.dumps({"type": "message", "sessionID": "ses_copilot_malformed", "part": {"text": "Done: wrote hello.txt."}}),
            '{"unknown": "event"}',
            json.dumps({"type": "tool", "name": "bash", "sessionID": "ses_copilot_malformed"}),
            json.dumps({"type": "result", "status": "success", "sessionID": "ses_copilot_malformed"}),
            "corrupted trailing line",
        ]
        res = runtimes.scan("copilot", corrupted_events)
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertEqual(res["result"]["response"], "Done: wrote hello.txt.")
        self.assertEqual(res["conversation_id"], "ses_copilot_malformed")
        self.assertEqual(res["tools"], 1)
        assert res["result"]["status"] == "SUCCESS"
        assert res["conversation_id"] == "ses_copilot_malformed"

    def test_copilot_scan_empty_and_incomplete_streams(self):
        """scan('copilot', ...) handles empty stream and incomplete stream without result gracefully."""
        empty_res = runtimes.scan("copilot", [])
        self.assertIsNone(empty_res["tokens"])
        self.assertEqual(empty_res["tools"], 0)
        self.assertIsNone(empty_res["conversation_id"])
        self.assertIsNone(empty_res["result"])

        none_res = runtimes.scan("copilot", None)
        self.assertIsNone(none_res["tokens"])
        self.assertEqual(none_res["tools"], 0)
        self.assertIsNone(none_res["conversation_id"])
        self.assertIsNone(none_res["result"])

        inc_res = runtimes.scan("copilot", [
            json.dumps({"type": "start", "sessionID": "ses_incomplete"}),
            json.dumps({"type": "tool", "name": "bash"}),
        ])
        self.assertIsNone(inc_res["tokens"])
        self.assertEqual(inc_res["tools"], 1)
        self.assertEqual(inc_res["conversation_id"], "ses_incomplete")
        self.assertIsNone(inc_res["result"])

        assert empty_res["tokens"] is None
        assert empty_res["result"] is None
        assert inc_res["tools"] == 1
        assert inc_res["result"] is None

    def test_copilot_scan_token_calculation_edge_cases(self):
        """scan('copilot', ...) correctly computes tokens across various usage formats."""
        # When total_tokens missing, sum input + output - cached
        stream_no_total = [
            json.dumps({"type": "step_finish", "part": {"reason": "stop"}, "usage": {"input_tokens": 500, "output_tokens": 150, "cached_tokens": 50}})
        ]
        res1 = runtimes.scan("copilot", stream_no_total)
        self.assertEqual(res1["tokens"], 600)

        # When cached exceeds total, clamp at 0
        stream_cached_exceeds = [
            json.dumps({"type": "step_finish", "part": {"reason": "stop"}, "usage": {"total_tokens": 100, "cached": 200}})
        ]
        res2 = runtimes.scan("copilot", stream_cached_exceeds)
        self.assertEqual(res2["tokens"], 0)

        # When usage is a raw number (int or float)
        stream_raw_num = [
            json.dumps({"type": "step_finish", "part": {"reason": "stop"}, "usage": 850})
        ]
        res3 = runtimes.scan("copilot", stream_raw_num)
        self.assertEqual(res3["tokens"], 850)

        # When usage is completely absent, tokens stays None (honest unknown cost)
        stream_no_usage = [
            json.dumps({"type": "step_finish", "part": {"reason": "stop"}})
        ]
        res4 = runtimes.scan("copilot", stream_no_usage)
        self.assertIsNone(res4["tokens"])

        assert res1["tokens"] == 600
        assert res2["tokens"] == 0
        assert res3["tokens"] == 850
        assert res4["tokens"] is None

    def test_copilot_scan_session_id_field_variations(self):
        """scan('copilot', ...) extracts session ID from various fields and preserves first occurrence."""
        for field, sid in [("sessionID", "sid_1"), ("sessionId", "sid_2"), ("session_id", "sid_3"), ("conversation_id", "sid_4"), ("session", "sid_5"), ("thread_id", "sid_6")]:
            r = runtimes.scan("copilot", [json.dumps({field: sid})])
            self.assertEqual(r["conversation_id"], sid)

        # Part-nested session ID
        r_part = runtimes.scan("copilot", [json.dumps({"part": {"sessionID": "sid_part"}})])
        self.assertEqual(r_part["conversation_id"], "sid_part")

        # Duplicate preservation: first session ID wins
        r_dup = runtimes.scan("copilot", [
            json.dumps({"type": "start", "sessionID": "first_session"}),
            json.dumps({"type": "message", "sessionID": "second_session"}),
        ])
        self.assertEqual(r_dup["conversation_id"], "first_session")

        assert r_dup["conversation_id"] == "first_session"
        assert r_part["conversation_id"] == "sid_part"

    def test_copilot_scan_tool_use_variations(self):
        """scan('copilot', ...) increments tool count on all supported tool event formats."""
        stream = [
            json.dumps({"type": "tool"}),
            json.dumps({"type": "tool_use"}),
            json.dumps({"type": "tool_call"}),
            json.dumps({"type": "tool_invocation"}),
            json.dumps({"type": "tool_execution"}),
            json.dumps({"part": {"type": "tool"}}),
            json.dumps({"part": {"type": "tool_use"}}),
            json.dumps({"part": {"tool": "read_file"}}),
            json.dumps({"tool": "grep"}),
        ]
        r = runtimes.scan("copilot", stream)
        self.assertEqual(r["tools"], 9)
        assert r["tools"] == 9

    def test_copilot_scan_message_formats(self):
        """scan('copilot', ...) handles text strings, lists of content blocks, and assistant message types."""
        stream = [
            json.dumps({"type": "message", "part": {"text": "Line 1"}}),
            json.dumps({"type": "text", "text": "Line 2"}),
            json.dumps({"type": "assistant_message", "message": {"content": "Line 3"}}),
            json.dumps({"type": "assistant.message", "content": [{"text": "Line 4a "}, {"text": "Line 4b"}]}),
            json.dumps({"type": "step_finish", "part": {"reason": "stop"}})
        ]
        r = runtimes.scan("copilot", stream)
        self.assertEqual(r["result"]["status"], "SUCCESS")
        expected_lines = ["Line 1", "Line 2", "Line 3", "Line 4a Line 4b"]
        for expected in expected_lines:
            self.assertIn(expected, r["result"]["response"])
        assert "Line 1" in r["result"]["response"]
        assert "Line 4a Line 4b" in r["result"]["response"]

    def test_copilot_scan_response_truncation_and_unicode(self):
        """scan('copilot', ...) truncates response to last 4000 characters and supports unicode text."""
        long_text = "C" * 5000
        r_long = runtimes.scan("copilot", [
            json.dumps({"type": "text", "text": long_text}),
            json.dumps({"type": "step_finish", "part": {"reason": "stop"}}),
        ])
        self.assertEqual(len(r_long["result"]["response"]), 4000)

        unicode_msg = "Copilot CLI integration complete 🚀 ✨ 测试"
        r_uni = runtimes.scan("copilot", [
            json.dumps({"type": "message", "part": {"text": unicode_msg}}),
            json.dumps({"type": "step_finish", "part": {"reason": "stop"}}),
        ])
        self.assertIn("🚀 ✨", r_uni["result"]["response"])
        self.assertIn("测试", r_uni["result"]["response"])

        assert len(r_long["result"]["response"]) == 4000
        assert "🚀 ✨" in r_uni["result"]["response"]

    def test_copilot_scan_file_helper(self):
        """scan_file('copilot', path) reads events from file and handles missing files safely."""
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as tf:
            tf.write(json.dumps({"type": "start", "sessionID": "ses_file_01"}) + "\n")
            tf.write(json.dumps({"type": "tool", "name": "bash"}) + "\n")
            tf.write(json.dumps({"type": "step_finish", "part": {"reason": "stop"}, "usage": {"total_tokens": 800, "cached_tokens": 100}}) + "\n")
            tf.flush()
            parsed = runtimes.scan_file("copilot", tf.name)
            self.assertEqual(parsed["conversation_id"], "ses_file_01")
            self.assertEqual(parsed["tools"], 1)
            self.assertEqual(parsed["tokens"], 700)
            self.assertEqual(parsed["result"]["status"], "SUCCESS")
            self.assertIsNone(parsed["result"]["cost_usd"])

        missing = runtimes.scan_file("copilot", "/nonexistent/path/copilot_events.jsonl")
        self.assertIsNone(missing["tokens"])
        self.assertEqual(missing["tools"], 0)
        self.assertIsNone(missing["result"])

        assert parsed["tokens"] == 700
        assert parsed["result"]["status"] == "SUCCESS"
        assert missing["result"] is None


if __name__ == "__main__":
    unittest.main()
