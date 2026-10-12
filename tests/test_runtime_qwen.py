#!/usr/bin/env python3
"""Tests for Qwen Code runtime in bin/lib/runtimes.py against fake-qwen-server.js.

Public interface:
- Run directly: python3 tests/test_runtime_qwen.py
- Run via unittest: python3 -m unittest tests/test_runtime_qwen.py
- Run via pytest: python3 -m pytest -q tests/test_runtime_qwen.py

Important invariants:
- RUNTIMES['qwen-code'] is registered with image=AGY_IMAGE, key='qwen.key', api=False.
- hosts('qwen-code') returns DashScope API endpoints.
- command('qwen-code', ...) supplies prompt on stdin, never in argv.
- scan('qwen-code', ...) parses stream-json events from fake-qwen-server.js (defaultEvents, errorEvents, failedEvents).
- Skips malformed/garbage lines without raising, preserving terminal result status.
- Zero network calls and zero real API keys.
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


class TestQwenRuntime(unittest.TestCase):
    """Unit tests for Qwen Code runtime against fake server fixture."""

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
        fixture_path = os.path.join(ROOT, "tests", "fixtures", "fake-qwen-server.js")
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
        sid = j.get('session_id') or j.get('sessionId') or j.get('thread_id') or j.get('conversation_id') or j.get('sessionID') or 'ses_qwen_test_01'
        if self.path in ('/health', '/healthz'):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{{"status":"ok"}}')
            return
        if '/error' in self.path or j.get('error'):
            err = j.get('error') or 'DashScope API error: invalid API key'
            events = [
                {{'type': 'init', 'session_id': sid}},
                {{'type': 'error', 'message': err}},
                {{'type': 'result', 'status': 'error', 'error': err, 'stats': {{'total_tokens': 100, 'input_tokens': 100, 'output_tokens': 0, 'cached': 0}}}}
            ]
        elif '/failed' in self.path or '/turn-failed' in self.path or j.get('turn_failed'):
            events = [
                {{'type': 'init', 'session_id': sid}},
                {{'type': 'turn.failed', 'error': {{'message': j.get('turn_failed') or 'DashScope task execution failed'}}}}
            ]
        else:
            events = [
                {{'type': 'init', 'session_id': sid}},
                {{'type': 'tool_use', 'name': 'shell', 'command': 'git status'}},
                {{'type': 'message', 'role': 'assistant', 'content': 'Completed task successfully.'}},
                {{'type': 'result', 'status': 'success', 'stats': {{'total_tokens': 1500, 'input_tokens': 1200, 'output_tokens': 300, 'cached': 200}}}}
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
        raise RuntimeError(f"Fake Qwen server failed to accept connections on port {port}")

    @classmethod
    def setUpClass(cls):
        port_env = os.environ.get("QWEN_SERVER_PORT")
        if port_env:
            cls.server_port = int(port_env)
        else:
            cls.server_port = cls._find_free_port()
            cls._start_fake_server(cls.server_port)
            os.environ["QWEN_SERVER_PORT"] = str(cls.server_port)

        cls._wait_for_server(cls.server_port)

        # Ensure qwen stub is on PATH if not present
        if not shutil.which("qwen"):
            cls.server_temp_dir = tempfile.TemporaryDirectory()
            stub_path = os.path.join(cls.server_temp_dir.name, "qwen")
            with open(stub_path, "w", encoding="utf-8") as f:
                f.write(f"""#!/usr/bin/env python3
import json, os, sys, urllib.request
port = os.environ.get("QWEN_SERVER_PORT", "{cls.server_port}")
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
payload = json.dumps({{
    "prompt": prompt,
    "args": args,
    "session_id": session_id,
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

    def test_qwen_scan_success_default_events(self):
        """Scenario 1: Normal successful run (defaultEvents) returns ok status with sensible tokens and tools."""
        events = self._fetch_events("/run", {"session_id": "ses_qwen_test_01"})
        res = runtimes.scan("qwen-code", events, prices="1.5,0.2,3.0")
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertEqual(res["result"]["response"], "Completed task successfully.")
        self.assertEqual(res["result"]["error"], "")
        self.assertEqual(res["tokens"], 1300)
        self.assertEqual(res["tools"], 1)
        self.assertEqual(res["conversation_id"], "ses_qwen_test_01")
        self.assertEqual(res["result"]["cost_usd"], 0.00244)
        assert res["result"]["status"] == "SUCCESS"
        assert res["tokens"] == 1300
        assert res["tools"] == 1

    def test_qwen_scan_error_events(self):
        """Scenario 2: Error-event path (errorEvents) returns not-ok result without crashing."""
        events = self._fetch_events("/error", {"session_id": "ses_qwen_err", "error": "DashScope API error: invalid API key"})
        res = runtimes.scan("qwen-code", events)
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "ERROR")
        self.assertIn("DashScope API error: invalid API key", res["result"]["error"])
        assert res["result"]["status"] == "ERROR"
        assert "DashScope API error" in res["result"]["error"]

    def test_qwen_scan_failed_events(self):
        """Scenario 3: Failed-event path (failedEvents) returns not-ok result without crashing."""
        events = self._fetch_events("/turn-failed", {"session_id": "ses_qwen_fail", "turn_failed": "DashScope task execution failed"})
        res = runtimes.scan("qwen-code", events)
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "ERROR")
        self.assertIn("DashScope task execution failed", res["result"]["error"])
        assert res["result"]["status"] == "ERROR"
        assert "DashScope task execution failed" in res["result"]["error"]

    def test_qwen_scan_malformed_lines_skipped(self):
        """Scenario 4: Malformed/garbage lines in the event stream are skipped without raising, reaching terminal result."""
        valid_events = self._fetch_events("/run", {"session_id": "ses_qwen_malformed"})
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
        res = runtimes.scan("qwen-code", corrupted_events)
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertEqual(res["result"]["response"], "Completed task successfully.")
        self.assertEqual(res["conversation_id"], "ses_qwen_malformed")
        self.assertEqual(res["tokens"], 1300)
        self.assertEqual(res["tools"], 1)
        assert res["result"]["status"] == "SUCCESS"
        assert res["conversation_id"] == "ses_qwen_malformed"
        assert res["tokens"] == 1300

    def test_qwen_runtime_metadata(self):
        """RUNTIMES['qwen-code'] metadata is properly configured with image, key, hosts and api=False."""
        self.assertIn("qwen-code", runtimes.RUNTIMES)
        entry = runtimes.RUNTIMES["qwen-code"]
        self.assertEqual(entry["image"], runtimes.AGY_IMAGE)
        self.assertEqual(entry["key"], "qwen.key")
        self.assertEqual(entry["hosts"], ["dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"])
        self.assertFalse(entry["api"])
        self.assertFalse(runtimes.is_api("qwen-code"))
        self.assertFalse(runtimes.uses_endpoint("qwen-code"))
        self.assertEqual(runtimes.hosts("qwen-code"), ["dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"])
        assert runtimes.is_api("qwen-code") is False
        assert runtimes.uses_endpoint("qwen-code") is False
        assert "dashscope.aliyuncs.com" in runtimes.hosts("qwen-code")

    def test_qwen_command_argv_and_stdin(self):
        """command('qwen-code', ...) passes prompt on stdin and omitted from argv with quiet flags and trailer."""
        prompt = "Fix database transaction leak"
        model = "qwen-max"
        conv = "ses_resume_42"
        argv, stdin = runtimes.command("qwen-code", prompt, model, conversation=conv)
        self.assertEqual(stdin, prompt)
        self.assertNotIn(prompt, argv)
        self.assertEqual(argv[0], "qwen")
        for flag in runtimes.QWEN_QUIET:
            self.assertIn(flag, argv)
        self.assertIn("-m", argv)
        self.assertIn(model, argv)
        self.assertIn("--resume", argv)
        self.assertIn(conv, argv)
        self.assertIn("-p", argv)
        self.assertIn(runtimes.QWEN_TRAILER, argv)
        assert stdin == prompt
        assert prompt not in argv
        assert argv[0] == "qwen"

    def test_qwen_command_omits_optional_flags(self):
        """command('qwen-code', ...) omits -m and --resume when model and conversation are None."""
        argv, stdin = runtimes.command("qwen-code", "simple task", None, conversation=None)
        self.assertEqual(stdin, "simple task")
        self.assertNotIn("-m", argv)
        self.assertNotIn("--resume", argv)
        assert "-m" not in argv
        assert "--resume" not in argv
        assert stdin == "simple task"

    def test_qwen_command_never_leaks_secrets(self):
        """command('qwen-code', ...) never includes API keys or secret references in argv."""
        argv, stdin = runtimes.command("qwen-code", "security check", "qwen-max", budget_usd=5.0)
        argv_str = " ".join(argv)
        self.assertNotIn("qwen.key", argv_str)
        self.assertNotIn("DASHSCOPE_API_KEY", argv_str)
        self.assertNotIn("sk-", argv_str)
        assert "qwen.key" not in argv_str
        assert "DASHSCOPE_API_KEY" not in argv_str
        assert "sk-" not in argv_str

    def test_qwen_command_end_to_end_execution(self):
        """command('qwen-code', ...) drives stub binary against fake server and scan() parses result."""
        prompt = "End-to-end integration test run"
        argv, stdin = runtimes.command("qwen-code", prompt, "qwen-max")
        proc = subprocess.run(argv, input=stdin, text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0)
        res = runtimes.scan("qwen-code", proc.stdout.splitlines())
        self.assertIsNotNone(res["result"])
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertEqual(res["result"]["response"], "Completed task successfully.")
        assert proc.returncode == 0
        assert res["result"]["status"] == "SUCCESS"
        assert res["result"]["response"] == "Completed task successfully."

    def test_qwen_command_resume_session_via_stub(self):
        """command('qwen-code', ...) resumes existing conversation session and scan() preserves conversation_id."""
        conv_id = "ses_qwen_resumed_77"
        argv, stdin = runtimes.command("qwen-code", "Follow-up question", "qwen-max", conversation=conv_id)
        proc = subprocess.run(argv, input=stdin, text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0)
        res = runtimes.scan("qwen-code", proc.stdout.splitlines())
        self.assertEqual(res["conversation_id"], conv_id)
        assert proc.returncode == 0
        assert res["conversation_id"] == conv_id

    def test_qwen_scan_empty_and_incomplete_streams(self):
        """scan('qwen-code', ...) handles empty stream and incomplete stream without result gracefully."""
        empty_res = runtimes.scan("qwen-code", [])
        self.assertEqual(empty_res["tokens"], 0)
        self.assertEqual(empty_res["tools"], 0)
        self.assertIsNone(empty_res["conversation_id"])
        self.assertIsNone(empty_res["result"])

        inc_res = runtimes.scan("qwen-code", [
            json.dumps({"type": "init", "session_id": "ses_incomplete"}),
            json.dumps({"type": "tool_use", "name": "shell"}),
        ])
        self.assertEqual(inc_res["tokens"], 0)
        self.assertEqual(inc_res["tools"], 1)
        self.assertEqual(inc_res["conversation_id"], "ses_incomplete")
        self.assertIsNone(inc_res["result"])

        assert empty_res["tokens"] == 0
        assert empty_res["result"] is None
        assert inc_res["tools"] == 1
        assert inc_res["result"] is None

    def test_qwen_scan_file_helper(self):
        """scan_file('qwen-code', path) reads events from temporary file and handles missing files safely."""
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as tf:
            tf.write(json.dumps({"type": "init", "session_id": "ses_file_01"}) + "\n")
            tf.write(json.dumps({"type": "tool_call", "name": "bash"}) + "\n")
            tf.write(json.dumps({"type": "result", "status": "success", "stats": {"total_tokens": 800, "cached": 100}}) + "\n")
            tf.flush()
            parsed = runtimes.scan_file("qwen-code", tf.name)
            self.assertEqual(parsed["conversation_id"], "ses_file_01")
            self.assertEqual(parsed["tools"], 1)
            self.assertEqual(parsed["tokens"], 700)
            self.assertEqual(parsed["result"]["status"], "SUCCESS")

        missing = runtimes.scan_file("qwen-code", "/nonexistent/path/qwen_events.jsonl")
        self.assertEqual(missing["tokens"], 0)
        self.assertIsNone(missing["result"])

        assert parsed["tokens"] == 700
        assert parsed["result"]["status"] == "SUCCESS"
        assert missing["result"] is None

    def test_qwen_scan_token_calculation_edge_cases(self):
        """scan('qwen-code', ...) correctly computes tokens when total_tokens missing or cached exceeds total."""
        stream_no_total = [
            json.dumps({"type": "result", "status": "success", "stats": {"input_tokens": 500, "output_tokens": 150, "cached": 50}})
        ]
        res1 = runtimes.scan("qwen-code", stream_no_total)
        self.assertEqual(res1["tokens"], 600)

        stream_cached_exceeds = [
            json.dumps({"type": "result", "status": "success", "stats": {"total_tokens": 100, "cached": 200}})
        ]
        res2 = runtimes.scan("qwen-code", stream_cached_exceeds)
        self.assertEqual(res2["tokens"], 0)

        assert res1["tokens"] == 600
        assert res2["tokens"] == 0

    def test_qwen_scan_session_id_field_variations(self):
        """scan('qwen-code', ...) extracts session ID from sessionId, thread_id, conversation_id and preserves first."""
        for field, sid in [("sessionId", "sid_alt_1"), ("thread_id", "sid_alt_2"), ("conversation_id", "sid_alt_3")]:
            r = runtimes.scan("qwen-code", [json.dumps({field: sid})])
            self.assertEqual(r["conversation_id"], sid)

        r_dup = runtimes.scan("qwen-code", [
            json.dumps({"type": "init", "session_id": "first_sid"}),
            json.dumps({"type": "message", "session_id": "second_sid"}),
        ])
        self.assertEqual(r_dup["conversation_id"], "first_sid")

        assert r_dup["conversation_id"] == "first_sid"
        assert r["conversation_id"] == "sid_alt_3"

    def test_qwen_scan_response_truncation_and_unicode(self):
        """scan('qwen-code', ...) truncates response to last 4000 characters and supports unicode text."""
        long_text = "X" * 5000
        r_long = runtimes.scan("qwen-code", [
            json.dumps({"type": "text", "text": long_text}),
            json.dumps({"type": "result", "status": "success"}),
        ])
        self.assertEqual(len(r_long["result"]["response"]), 4000)

        unicode_msg = "修复 DashScope 漏洞 🚀 完成"
        r_uni = runtimes.scan("qwen-code", [
            json.dumps({"type": "message", "role": "assistant", "content": unicode_msg}),
            json.dumps({"type": "result", "status": "success"}),
        ])
        self.assertIn("🚀", r_uni["result"]["response"])

        assert len(r_long["result"]["response"]) == 4000
        assert "🚀" in r_uni["result"]["response"]


if __name__ == "__main__":
    unittest.main()
