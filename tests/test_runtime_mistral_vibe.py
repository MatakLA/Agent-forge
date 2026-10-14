#!/usr/bin/env python3
"""Tests for Mistral Vibe runtime in bin/lib/runtimes.py against fake-mistral-vibe-server.js.

Public interface:
- Run directly: python3 tests/test_runtime_mistral_vibe.py
- Run via unittest: python3 -m unittest tests/test_runtime_mistral_vibe.py
- Run via pytest: python3 -m pytest -q tests/test_runtime_mistral_vibe.py

Important invariants:
- RUNTIMES['mistral-vibe'] is registered with image=AGY_IMAGE, key='mistral.key', hosts=['api.mistral.ai'], api=False.
- hosts('mistral-vibe') returns ['api.mistral.ai'] and cost_model('mistral-vibe') reports unknown pricing honestly.
- command('mistral-vibe', ...) passes prompt on stdin, never in argv, with quiet flags and optional --model/--resume.
- scan('mistral-vibe', ...) parses stream-json events from fake-mistral-vibe-server.js without crashing.
- Skips malformed/garbage lines without raising, preserving terminal result status, zero network calls and no real keys.
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


class TestMistralVibeRuntime(unittest.TestCase):
    """Unit tests for Mistral Vibe runtime against fake server fixture."""

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
        fixture_path = os.path.join(ROOT, "tests", "fixtures", "fake-mistral-vibe-server.js")
        if shutil.which("node"):
            cls.server_proc = subprocess.Popen(
                ["node", fixture_path, str(port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            # Python server providing identical behavior when node is absent
            code = f"""
import http.server, json, sys
port = {port}
class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length > 0 else ''
        try:
            j = json.loads(body)
        except Exception:
            j = {{}}
        sid = j.get('conversation_id') or j.get('session_id') or j.get('sessionId') or j.get('thread_id') or j.get('session') or 'ses_mistral_vibe_test_01'
        if self.path in ('/health', '/healthz'):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{{"status":"ok"}}')
            return
        if '/error' in self.path or j.get('error'):
            err = j.get('error') or 'Mistral API error: invalid API key'
            events = [
                {{'type': 'session', 'conversation_id': sid}},
                {{'type': 'error', 'message': err}},
                {{'type': 'step_finish', 'part': {{'reason': 'error', 'error': err}}, 'usage': {{'total_tokens': 100, 'input_tokens': 100, 'output_tokens': 0, 'cached_tokens': 0}}}}
            ]
        elif '/failed' in self.path or '/turn-failed' in self.path or j.get('turn_failed'):
            events = [
                {{'type': 'session', 'conversation_id': sid}},
                {{'type': 'turn.failed', 'error': {{'message': j.get('turn_failed') or 'Mistral Vibe task execution failed'}}}}
            ]
        elif '/result' in self.path or j.get('use_result_event'):
            events = [
                {{'type': 'session', 'conversation_id': sid}},
                {{'type': 'tool_call', 'name': 'bash', 'tool': 'bash'}},
                {{'type': 'message', 'text': 'Completed task with Mistral Vibe.'}},
                {{'type': 'result', 'status': 'success', 'response': 'Completed task with Mistral Vibe.', 'usage': {{'total_tokens': 1500, 'input_tokens': 1200, 'output_tokens': 300, 'cached_tokens': 200}}}}
            ]
        else:
            events = [
                {{'type': 'session', 'conversation_id': sid}},
                {{'type': 'tool_use', 'name': 'bash', 'part': {{'type': 'tool_use', 'command': 'git status'}}}},
                {{'type': 'message', 'role': 'assistant', 'text': 'Completed task with Mistral Vibe.'}},
                {{'type': 'step_finish', 'part': {{'reason': 'stop'}}, 'usage': {{'total_tokens': 1500, 'input_tokens': 1200, 'output_tokens': 300, 'cached_tokens': 200}}}}
            ]
        self.send_response(200)
        self.send_header('Content-Type', 'application/x-ndjson')
        self.end_headers()
        for ev in events:
            self.wfile.write((json.dumps(ev) + '\\n').encode('utf-8'))
    def do_GET(self):
        self.do_POST()
    def log_message(self, format, *args):
        pass
server = http.server.HTTPServer(('127.0.0.1', port), Handler)
server.serve_forever()
"""
            cls.server_proc = subprocess.Popen(
                [sys.executable, "-c", code],
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
        raise RuntimeError(f"Fake Mistral Vibe server failed to accept connections on port {port}")

    @classmethod
    def setUpClass(cls):
        port_env = os.environ.get("MISTRAL_VIBE_SERVER_PORT")
        if port_env:
            cls.server_port = int(port_env)
        else:
            cls.server_port = cls._find_free_port()
            cls._start_fake_server(cls.server_port)
            os.environ["MISTRAL_VIBE_SERVER_PORT"] = str(cls.server_port)

        cls._wait_for_server(cls.server_port)

        # Ensure mistral-vibe stub is on PATH if not present
        if not shutil.which("mistral-vibe"):
            cls.server_temp_dir = tempfile.TemporaryDirectory()
            stub_path = os.path.join(cls.server_temp_dir.name, "mistral-vibe")
            with open(stub_path, "w", encoding="utf-8") as f:
                f.write(f"""#!/usr/bin/env python3
import json, os, sys, urllib.request
port = os.environ.get("MISTRAL_VIBE_SERVER_PORT", "{cls.server_port}")
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
elif "TRIGGER_TURN_FAILED" in prompt:
    url = f"http://127.0.0.1:{{port}}/turn-failed"
elif "TRIGGER_RESULT_EVENT" in prompt:
    url = f"http://127.0.0.1:{{port}}/result"
payload = json.dumps({{
    "prompt": prompt,
    "args": args,
    "conversation_id": session_id,
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

    def test_mistral_vibe_runtime_metadata(self):
        """RUNTIMES['mistral-vibe'] metadata is properly configured with image, key, hosts and api=False."""
        self.assertIn("mistral-vibe", runtimes.RUNTIMES)
        entry = runtimes.RUNTIMES["mistral-vibe"]
        self.assertEqual(entry["image"], runtimes.AGY_IMAGE)
        self.assertEqual(entry["key"], "mistral.key")
        self.assertEqual(entry["hosts"], ["api.mistral.ai"])
        self.assertFalse(entry["api"])
        self.assertFalse(runtimes.is_api("mistral-vibe"))
        self.assertFalse(runtimes.uses_endpoint("mistral-vibe"))
        self.assertEqual(runtimes.hosts("mistral-vibe"), ["api.mistral.ai"])
        self.assertEqual(runtimes.hosts("mistral-vibe", endpoint_url="http://other:8080"), ["api.mistral.ai"])
        assert runtimes.is_api("mistral-vibe") is False
        assert runtimes.uses_endpoint("mistral-vibe") is False
        assert "api.mistral.ai" in runtimes.hosts("mistral-vibe")

    def test_mistral_vibe_cost_model(self):
        """cost_model('mistral-vibe') reports unknown pricing honestly without fabricated USD figure."""
        cm = runtimes.cost_model("mistral-vibe")
        self.assertIsInstance(cm, dict)
        self.assertEqual(set(cm.keys()), {"kind", "detail"})
        self.assertEqual(cm["kind"], "unknown")
        self.assertIn("Mistral Vibe", cm["detail"])
        self.assertIn("not tracked in Agent-Forge", cm["detail"])
        self.assertNotIn("USD", cm["detail"])
        assert cm["kind"] == "unknown"
        assert "not tracked" in cm["detail"]

    def test_mistral_vibe_command_argv_and_stdin(self):
        """command('mistral-vibe', ...) passes prompt on stdin, omitted from argv with quiet flags."""
        prompt = "Fix database transaction leak"
        model = "codestral-latest"
        conv = "ses_mistral_vibe_42"
        argv, stdin = runtimes.command("mistral-vibe", prompt, model, conversation=conv)
        self.assertEqual(stdin, prompt)
        self.assertNotIn(prompt, argv)
        self.assertEqual(argv[0], "mistral-vibe")
        for flag in runtimes.MISTRAL_VIBE_QUIET:
            self.assertIn(flag, argv)
        self.assertIn("--model", argv)
        self.assertIn(model, argv)
        self.assertIn("--resume", argv)
        self.assertIn(conv, argv)
        assert stdin == prompt
        assert prompt not in argv
        assert argv[0] == "mistral-vibe"

    def test_mistral_vibe_command_omits_optional_flags(self):
        """command('mistral-vibe', ...) omits --model and --resume when model and conversation are None."""
        argv, stdin = runtimes.command("mistral-vibe", "simple task", None, conversation=None)
        self.assertEqual(stdin, "simple task")
        self.assertNotIn("--model", argv)
        self.assertNotIn("--resume", argv)
        self.assertEqual(argv, ["mistral-vibe"] + runtimes.MISTRAL_VIBE_QUIET)
        assert "--model" not in argv
        assert "--resume" not in argv

    def test_mistral_vibe_command_never_leaks_secrets(self):
        """command('mistral-vibe', ...) never includes API keys or secret references in argv."""
        argv, stdin = runtimes.command("mistral-vibe", "security audit", "codestral-latest", budget_usd=5.0)
        argv_str = " ".join(argv)
        self.assertNotIn("mistral.key", argv_str)
        self.assertNotIn("MISTRAL_API_KEY", argv_str)
        self.assertNotIn("sk-", argv_str)
        assert "mistral.key" not in argv_str
        assert "MISTRAL_API_KEY" not in argv_str
        assert "sk-" not in argv_str

    def test_mistral_vibe_command_end_to_end_execution(self):
        """command('mistral-vibe', ...) drives stub binary against fake server and scan() parses result."""
        prompt = "End-to-end integration test run"
        argv, stdin = runtimes.command("mistral-vibe", prompt, "codestral-latest")
        proc = subprocess.run(argv, input=stdin, text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0)
        res = runtimes.scan("mistral-vibe", proc.stdout.splitlines())
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertEqual(res["result"]["response"], "Completed task with Mistral Vibe.")
        self.assertEqual(res["tokens"], 1300)
        self.assertEqual(res["tools"], 1)
        assert proc.returncode == 0
        assert res["result"]["status"] == "SUCCESS"
        assert res["result"]["response"] == "Completed task with Mistral Vibe."

    def test_mistral_vibe_command_resume_session_via_stub(self):
        """command('mistral-vibe', ...) resumes existing conversation session and scan() preserves conversation_id."""
        conv_id = "ses_mistral_vibe_resumed_77"
        argv, stdin = runtimes.command("mistral-vibe", "Follow-up question", "codestral-latest", conversation=conv_id)
        proc = subprocess.run(argv, input=stdin, text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0)
        res = runtimes.scan("mistral-vibe", proc.stdout.splitlines())
        self.assertEqual(res["conversation_id"], conv_id)
        assert proc.returncode == 0
        assert res["conversation_id"] == conv_id

    def test_mistral_vibe_scan_success_default_events(self):
        """Normal successful run (defaultEvents) returns SUCCESS status with sensible tokens and tools."""
        events = self._fetch_events("/run", {"conversation_id": "ses_mistral_vibe_test_01"})
        res = runtimes.scan("mistral-vibe", events)
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertEqual(res["result"]["response"], "Completed task with Mistral Vibe.")
        self.assertEqual(res["result"]["error"], "")
        self.assertEqual(res["tokens"], 1300)
        self.assertEqual(res["tools"], 1)
        self.assertEqual(res["conversation_id"], "ses_mistral_vibe_test_01")
        self.assertIsNone(res["result"]["cost_usd"])
        assert res["result"]["status"] == "SUCCESS"
        assert res["tokens"] == 1300
        assert res["tools"] == 1

    def test_mistral_vibe_scan_error_events(self):
        """Error-event path (errorEvents) returns ERROR status without crashing."""
        events = self._fetch_events("/error", {"conversation_id": "ses_mistral_err", "error": "Mistral API error: invalid API key"})
        res = runtimes.scan("mistral-vibe", events)
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "ERROR")
        self.assertIn("Mistral API error: invalid API key", res["result"]["error"])
        assert res["result"]["status"] == "ERROR"
        assert "Mistral API error" in res["result"]["error"]

    def test_mistral_vibe_scan_failed_events(self):
        """Failed-event path (failedEvents) returns ERROR status without crashing."""
        events = self._fetch_events("/turn-failed", {"conversation_id": "ses_mistral_fail", "turn_failed": "Mistral Vibe task execution failed"})
        res = runtimes.scan("mistral-vibe", events)
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "ERROR")
        self.assertIn("Mistral Vibe task execution failed", res["result"]["error"])
        assert res["result"]["status"] == "ERROR"
        assert "Mistral Vibe task execution failed" in res["result"]["error"]

    def test_mistral_vibe_scan_step_finish_variants(self):
        """scan('mistral-vibe', ...) handles different step_finish reasons and finish texts."""
        # reason='error'
        s_err = [
            json.dumps({"type": "step_finish", "part": {"reason": "error", "error": "Internal server error"}})
        ]
        r_err = runtimes.scan("mistral-vibe", s_err)
        self.assertEqual(r_err["result"]["status"], "ERROR")
        self.assertEqual(r_err["result"]["error"], "Internal server error")

        # reason='completed' with finish_txt
        s_ok = [
            json.dumps({"type": "step_finish", "part": {"reason": "completed", "text": "All tasks finished successfully"}})
        ]
        r_ok = runtimes.scan("mistral-vibe", s_ok)
        self.assertEqual(r_ok["result"]["status"], "SUCCESS")
        self.assertEqual(r_ok["result"]["response"], "All tasks finished successfully")

        # reason='failure'
        s_fail = [
            json.dumps({"type": "step_finish", "part": {"reason": "failure", "error": {"message": "Execution timeout"}}})
        ]
        r_fail = runtimes.scan("mistral-vibe", s_fail)
        self.assertEqual(r_fail["result"]["status"], "ERROR")
        self.assertEqual(r_fail["result"]["error"], "Execution timeout")

        assert r_err["result"]["status"] == "ERROR"
        assert r_ok["result"]["status"] == "SUCCESS"
        assert r_fail["result"]["status"] == "ERROR"

    def test_mistral_vibe_scan_result_event_with_prices(self):
        """scan('mistral-vibe', ...) computes cost_usd when result event is present and prices are given."""
        stream = [
            json.dumps({"type": "session", "conversation_id": "ses_price_01"}),
            json.dumps({"type": "message", "text": "Task completed"}),
            json.dumps({
                "type": "result",
                "status": "success",
                "response": "Final output",
                "usage": {
                    "input_tokens": 1200,
                    "cached_tokens": 200,
                    "output_tokens": 300,
                    "total_tokens": 1500,
                }
            })
        ]
        # prices: input=2.0, cached=0.5, output=6.0 per million tokens
        # cost = (1200 - 200) * 2.0 + 200 * 0.5 + 300 * 6.0 = 2000 + 100 + 1800 = 3900 -> 0.0039 USD
        res = runtimes.scan("mistral-vibe", stream, prices="2.0,0.5,6.0")
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertEqual(res["result"]["response"], "Final output")
        self.assertEqual(res["result"]["cost_usd"], 0.0039)
        self.assertEqual(res["tokens"], 1300)

        # Without prices: cost_usd is None
        res_no_price = runtimes.scan("mistral-vibe", stream)
        self.assertIsNone(res_no_price["result"]["cost_usd"])

        assert res["result"]["cost_usd"] == 0.0039
        assert res_no_price["result"]["cost_usd"] is None

    def test_mistral_vibe_scan_malformed_lines_skipped(self):
        """Malformed/garbage lines in the event stream are skipped without raising, reaching terminal result."""
        valid_events = self._fetch_events("/run", {"conversation_id": "ses_mistral_malformed"})
        corrupted_events = [
            "NOT_JSON_AT_ALL",
            "{broken json",
            "[1, 2, 3]",
            "42",
            "   ",
            valid_events[0],
            "corrupted middle line",
            valid_events[1],
            '{"unknown": "event"}',
            valid_events[2],
            valid_events[3],
            "corrupted trailing line",
        ]
        res = runtimes.scan("mistral-vibe", corrupted_events)
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertEqual(res["result"]["response"], "Completed task with Mistral Vibe.")
        self.assertEqual(res["conversation_id"], "ses_mistral_malformed")
        self.assertEqual(res["tokens"], 1300)
        self.assertEqual(res["tools"], 1)
        assert res["result"]["status"] == "SUCCESS"
        assert res["conversation_id"] == "ses_mistral_malformed"

    def test_mistral_vibe_scan_empty_and_incomplete_streams(self):
        """scan('mistral-vibe', ...) handles empty stream and incomplete stream without result gracefully."""
        empty_res = runtimes.scan("mistral-vibe", [])
        self.assertIsNone(empty_res["tokens"])
        self.assertEqual(empty_res["tools"], 0)
        self.assertIsNone(empty_res["conversation_id"])
        self.assertIsNone(empty_res["result"])

        none_res = runtimes.scan("mistral-vibe", None)
        self.assertIsNone(none_res["tokens"])
        self.assertEqual(none_res["tools"], 0)
        self.assertIsNone(none_res["conversation_id"])
        self.assertIsNone(none_res["result"])

        inc_res = runtimes.scan("mistral-vibe", [
            json.dumps({"type": "session", "conversation_id": "ses_incomplete"}),
            json.dumps({"type": "tool_use", "name": "shell"}),
        ])
        self.assertIsNone(inc_res["tokens"])
        self.assertEqual(inc_res["tools"], 1)
        self.assertEqual(inc_res["conversation_id"], "ses_incomplete")
        self.assertIsNone(inc_res["result"])

        assert empty_res["tokens"] is None
        assert empty_res["result"] is None
        assert inc_res["tools"] == 1
        assert inc_res["result"] is None

    def test_mistral_vibe_scan_token_calculation_edge_cases(self):
        """scan('mistral-vibe', ...) correctly computes tokens across various usage formats."""
        # When total_tokens missing, sum input + output - cached
        stream_no_total = [
            json.dumps({"type": "step_finish", "part": {"reason": "stop"}, "usage": {"input_tokens": 500, "output_tokens": 150, "cached_tokens": 50}})
        ]
        res1 = runtimes.scan("mistral-vibe", stream_no_total)
        self.assertEqual(res1["tokens"], 600)

        # When cached exceeds total, clamp at 0
        stream_cached_exceeds = [
            json.dumps({"type": "step_finish", "part": {"reason": "stop"}, "usage": {"total_tokens": 100, "cached": 200}})
        ]
        res2 = runtimes.scan("mistral-vibe", stream_cached_exceeds)
        self.assertEqual(res2["tokens"], 0)

        # When usage is a raw number (int or float)
        stream_raw_num = [
            json.dumps({"type": "step_finish", "part": {"reason": "stop"}, "usage": 850})
        ]
        res3 = runtimes.scan("mistral-vibe", stream_raw_num)
        self.assertEqual(res3["tokens"], 850)

        # When usage is completely absent, tokens stays None (honest unknown cost)
        stream_no_usage = [
            json.dumps({"type": "step_finish", "part": {"reason": "stop"}})
        ]
        res4 = runtimes.scan("mistral-vibe", stream_no_usage)
        self.assertIsNone(res4["tokens"])

        assert res1["tokens"] == 600
        assert res2["tokens"] == 0
        assert res3["tokens"] == 850
        assert res4["tokens"] is None

    def test_mistral_vibe_scan_session_id_field_variations(self):
        """scan('mistral-vibe', ...) extracts session ID from various fields and preserves first occurrence."""
        for field, sid in [("conversation_id", "sid_1"), ("session_id", "sid_2"), ("sessionId", "sid_3"), ("thread_id", "sid_4"), ("session", "sid_5")]:
            r = runtimes.scan("mistral-vibe", [json.dumps({field: sid})])
            self.assertEqual(r["conversation_id"], sid)

        # Part-nested session ID
        r_part = runtimes.scan("mistral-vibe", [json.dumps({"part": {"conversation_id": "sid_part"}})])
        self.assertEqual(r_part["conversation_id"], "sid_part")

        # Duplicate preservation
        r_dup = runtimes.scan("mistral-vibe", [
            json.dumps({"type": "session", "conversation_id": "first_session"}),
            json.dumps({"type": "message", "conversation_id": "second_session"}),
        ])
        self.assertEqual(r_dup["conversation_id"], "first_session")

        assert r_dup["conversation_id"] == "first_session"
        assert r_part["conversation_id"] == "sid_part"

    def test_mistral_vibe_scan_tool_use_variations(self):
        """scan('mistral-vibe', ...) increments tool count on all supported tool event formats."""
        stream = [
            json.dumps({"type": "tool_use"}),
            json.dumps({"type": "tool_call"}),
            json.dumps({"type": "tool"}),
            json.dumps({"type": "tool_invocation"}),
            json.dumps({"type": "tool_execution"}),
            json.dumps({"type": "other", "part": {"type": "tool"}}),
            json.dumps({"type": "other", "part": {"type": "tool_use"}}),
            json.dumps({"type": "other", "part": {"tool": "read_file"}}),
            json.dumps({"type": "other", "tool": "grep"}),
        ]
        r = runtimes.scan("mistral-vibe", stream)
        self.assertEqual(r["tools"], 9)
        assert r["tools"] == 9

    def test_mistral_vibe_scan_message_formats(self):
        """scan('mistral-vibe', ...) handles text strings, lists of content blocks, and assistant message types."""
        stream = [
            json.dumps({"type": "message", "text": "Line 1"}),
            json.dumps({"type": "text", "content": "Line 2"}),
            json.dumps({"type": "assistant_message", "message": {"content": "Line 3"}}),
            json.dumps({"type": "assistant.message", "content": [{"text": "Line 4a "}, {"text": "Line 4b"}]}),
            json.dumps({"type": "step_finish", "part": {"reason": "stop"}})
        ]
        r = runtimes.scan("mistral-vibe", stream)
        self.assertEqual(r["result"]["status"], "SUCCESS")
        expected_lines = ["Line 1", "Line 2", "Line 3", "Line 4a Line 4b"]
        for expected in expected_lines:
            self.assertIn(expected, r["result"]["response"])
        assert "Line 1" in r["result"]["response"]
        assert "Line 4a Line 4b" in r["result"]["response"]

    def test_mistral_vibe_scan_response_truncation_and_unicode(self):
        """scan('mistral-vibe', ...) truncates response to last 4000 characters and supports unicode text."""
        long_text = "M" * 5000
        r_long = runtimes.scan("mistral-vibe", [
            json.dumps({"type": "text", "text": long_text}),
            json.dumps({"type": "step_finish", "part": {"reason": "stop"}}),
        ])
        self.assertEqual(len(r_long["result"]["response"]), 4000)

        unicode_msg = "Intégration de Mistral Vibe réussie 🚀 ✨"
        r_uni = runtimes.scan("mistral-vibe", [
            json.dumps({"type": "message", "role": "assistant", "text": unicode_msg}),
            json.dumps({"type": "step_finish", "part": {"reason": "stop"}}),
        ])
        self.assertIn("🚀 ✨", r_uni["result"]["response"])
        self.assertIn("Intégration", r_uni["result"]["response"])

        assert len(r_long["result"]["response"]) == 4000
        assert "🚀 ✨" in r_uni["result"]["response"]

    def test_mistral_vibe_scan_file_helper(self):
        """scan_file('mistral-vibe', path) reads events from temporary file and handles missing files safely."""
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as tf:
            tf.write(json.dumps({"type": "session", "conversation_id": "ses_file_01"}) + "\n")
            tf.write(json.dumps({"type": "tool_call", "name": "bash"}) + "\n")
            tf.write(json.dumps({"type": "step_finish", "part": {"reason": "stop"}, "usage": {"total_tokens": 800, "cached_tokens": 100}}) + "\n")
            tf.flush()
            parsed = runtimes.scan_file("mistral-vibe", tf.name)
            self.assertEqual(parsed["conversation_id"], "ses_file_01")
            self.assertEqual(parsed["tools"], 1)
            self.assertEqual(parsed["tokens"], 700)
            self.assertEqual(parsed["result"]["status"], "SUCCESS")

        missing = runtimes.scan_file("mistral-vibe", "/nonexistent/path/mistral_vibe_events.jsonl")
        self.assertIsNone(missing["tokens"])
        self.assertEqual(missing["tools"], 0)
        self.assertIsNone(missing["result"])

        assert parsed["tokens"] == 700
        assert parsed["result"]["status"] == "SUCCESS"
        assert missing["result"] is None


if __name__ == "__main__":
    unittest.main()
