#!/usr/bin/env python3
"""Integration tests exercising OpenAI and Gemini orchestrator providers against offline fake-model-api.js.

Public interface:
- Run directly: python3 -m unittest tests/test_fake_model_api_providers.py
- Run via shell harness: bash tests/test_fake_model_api_providers.sh

Important invariants:
- Strictly offline: launches real node tests/fixtures/fake-model-api.js fixture with no network egress.
- If Node.js is not installed, skips cleanly with clear reason rather than substituting duplicate mock logic.
- Tests call_openai_api and call_gemini_api against the mock endpoints and verifies error resilience.
- Exercises llm() plan and review dispatch end-to-end against fake-model-api.js with schema validation.
- Verifies fake secrets from environment or file store are never written to log_llm / llm.jsonl.
- Top-level smoke-import verification and standard unittest.TestCase with bare assertions throughout.
"""
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))

# Top-level smoke-import check: ensures modules load cleanly without silent failure
try:
    import providers
    import sbx
    from foreman import ctx, llm, store, util
    from foreman.prompts import TASK_SCHEMA
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)

for _mod in (sbx, ctx, llm, providers, store, util):
    assert _mod is not None, f"Module {_mod} failed to load"
assert TASK_SCHEMA is not None and len(TASK_SCHEMA) > 0, "TASK_SCHEMA failed to load"


class TestFakeModelApiProviders(unittest.TestCase):
    """Integration test suite exercising orchestrator providers against offline fake model API."""

    server_proc = None
    port = None
    orig_urlopen = None

    @classmethod
    def setUpClass(cls):
        """Set up test environment by launching the real fake-model-api.js fixture with node."""
        if not shutil.which("node"):
            raise unittest.SkipTest("Node.js is not available on host; fake-model-api.js requires node")

        fixture_path = os.path.join(ROOT, "tests", "fixtures", "fake-model-api.js")
        if not os.path.exists(fixture_path):
            raise unittest.SkipTest(f"Fixture file not found: {fixture_path}")

        # Read fixture's actual port from the source (.listen(8099))
        with open(fixture_path, "r", encoding="utf-8") as f:
            content = f.read()
        m = re.search(r'\.listen\(\s*(\d+)', content)
        cls.port = int(m.group(1)) if m else 8099

        # Check if server is already running (e.g. launched by shell harness)
        s = socket.socket()
        already_running = False
        try:
            s.connect(("127.0.0.1", cls.port))
            already_running = True
        except Exception:
            already_running = False
        finally:
            s.close()

        if not already_running:
            cls.server_proc = subprocess.Popen(
                ["node", fixture_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            deadline = time.time() + 5.0
            connected = False
            while time.time() < deadline:
                try:
                    s = socket.socket()
                    s.connect(("127.0.0.1", cls.port))
                    s.close()
                    connected = True
                    break
                except Exception:
                    time.sleep(0.05)
            if not connected:
                if cls.server_proc.poll() is not None:
                    _, err = cls.server_proc.communicate()
                    raise RuntimeError(f"fake-model-api.js exited prematurely: {err.decode('utf-8', errors='replace')}")
                cls.server_proc.terminate()
                cls.server_proc.wait()
                raise RuntimeError(f"fake-model-api.js failed to bind port {cls.port}")

        # Intercept generativelanguage.googleapis.com to direct traffic to local fake server
        cls.orig_urlopen = urllib.request.urlopen

        def redirected_urlopen(req, *args, **kwargs):
            if isinstance(req, urllib.request.Request):
                url = req.full_url
                if "generativelanguage.googleapis.com" in url:
                    new_url = url.replace("https://generativelanguage.googleapis.com", f"http://127.0.0.1:{cls.port}")
                    req = urllib.request.Request(new_url, data=req.data, headers=req.headers, method=req.get_method())
            return cls.orig_urlopen(req, *args, **kwargs)

        urllib.request.urlopen = redirected_urlopen

    @classmethod
    def tearDownClass(cls):
        """Restore global urllib opener and terminate server process if started in this runner."""
        if cls.orig_urlopen:
            urllib.request.urlopen = cls.orig_urlopen
        if cls.server_proc:
            cls.server_proc.terminate()
            try:
                cls.server_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                cls.server_proc.kill()
                cls.server_proc.wait()
            cls.server_proc = None

    def setUp(self):
        """Prepare throwaway directory and configure test context."""
        self.tmp = tempfile.mkdtemp(prefix="af-fake-api-test-")
        self.old_fd = ctx.FD
        self.old_c = dict(ctx.C)
        self.old_env = {k: os.environ.get(k) for k in (
            "OPENAI_API_BASE", "OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY",
            "PLAN_PROVIDERS", "REVIEW_PROVIDERS", "OPENAI_MODEL", "GEMINI_MODEL"
        )}

        ctx.FD = os.path.join(self.tmp, "foreman")
        os.makedirs(ctx.FD, exist_ok=True)

        if self.port:
            os.environ["OPENAI_API_BASE"] = f"http://127.0.0.1:{self.port}/v1"
            ctx.C["OPENAI_API_BASE"] = f"http://127.0.0.1:{self.port}/v1"
        self.secrets_dir = os.path.join(self.tmp, "secrets")
        os.makedirs(self.secrets_dir, mode=0o700, exist_ok=True)
        self.fake_openai_key = "fake-secret-openai-key-0094-xyz"
        self.fake_gemini_key = "fake-secret-gemini-key-0094-abc"
        self.fake_anthropic_key = "fake-secret-anthropic-key-0094-uvw"
        os.environ["OPENAI_API_KEY"] = self.fake_openai_key
        os.environ["GEMINI_API_KEY"] = self.fake_gemini_key
        os.environ["ANTHROPIC_API_KEY"] = self.fake_anthropic_key
        ctx.C["OPENAI_MODEL"] = "gpt-4o"
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"
        os.environ["OPENAI_MODEL"] = "gpt-4o"
        os.environ["GEMINI_MODEL"] = "gemini-1.5-pro"

    def tearDown(self):
        """Restore global context and clean up temporary files."""
        ctx.FD = self.old_fd
        ctx.C.clear()
        ctx.C.update(self.old_c)

        for k, v in self.old_env.items():
            if v is not None:
                os.environ[k] = v
            elif k in os.environ:
                del os.environ[k]

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_call_openai_api_mock_server_success(self):
        """Invariant: call_openai_api successfully calls mock endpoint and returns parsed response."""
        res = providers.call_openai_api(
            prompt="Hello from orchestrator",
            model="gpt-4o",
            api_base=f"http://127.0.0.1:{self.port}/v1"
        )
        self.assertFalse(res["is_error"])
        self.assertEqual(res["provider"], "openai-api")
        self.assertEqual(res["model"], "gpt-4o")
        self.assertIn("hello.txt", res["text"])
        self.assertIsNone(res["error"])
        self.assertIn("total_tokens", res["usage"])
        assert res["is_error"] is False

    def test_call_gemini_api_mock_server_success(self):
        """Invariant: call_gemini_api successfully calls mock endpoint and returns parsed response."""
        res = providers.call_gemini_api(
            prompt="Hello from orchestrator",
            model="gemini-1.5-pro"
        )
        self.assertFalse(res["is_error"])
        self.assertEqual(res["provider"], "gemini-api")
        self.assertEqual(res["model"], "gemini-1.5-pro")
        self.assertEqual(res["text"], "ok")
        self.assertIsNone(res["error"])
        self.assertIn("totalTokenCount", res["usage"])
        assert res["is_error"] is False

    def test_call_anthropic_api_mock_server_success(self):
        """Invariant: call_anthropic_api successfully calls mock endpoint and returns parsed response."""
        res = providers.call_anthropic_api(
            prompt="Hello from orchestrator",
            model="claude-3-5-sonnet",
            api_base=f"http://127.0.0.1:{self.port}"
        )
        self.assertFalse(res["is_error"])
        self.assertEqual(res["provider"], "anthropic-api")
        self.assertEqual(res["model"], "claude-3-5-sonnet")
        self.assertEqual(res["text"], "ok")
        self.assertIsNone(res["error"])
        assert res["is_error"] is False

    def test_call_openai_api_404_resilience(self):
        """Invariant: call_openai_api handles 404 endpoint gracefully without crashing."""
        res = providers.call_openai_api(
            prompt="Hello",
            model="gpt-4o",
            api_base=f"http://127.0.0.1:{self.port}/unfaked"
        )
        self.assertTrue(res["is_error"])
        self.assertIn("404", str(res["error"]))
        assert res["is_error"] is True

    def test_call_gemini_api_404_resilience(self):
        """Invariant: call_gemini_api handles HTTP errors gracefully without unhandled crashes."""
        with mock.patch("urllib.request.urlopen") as m_url:
            err = urllib.error.HTTPError(
                url=f"http://127.0.0.1:{self.port}",
                code=404,
                msg="Not Found",
                hdrs={},
                fp=io.BytesIO(b'{"error":"not found"}')
            )
            m_url.side_effect = err
            res = providers.call_gemini_api(prompt="Hello", model="gemini-1.5-pro")
        self.assertTrue(res["is_error"])
        self.assertIn("404", str(res["error"]))
        assert res["is_error"] is True

    def test_missing_secret_key_validation(self):
        """Invariant: missing API key secrets immediately raise ValueError before network calls."""
        os.environ["OPENAI_API_KEY"] = ""
        with self.assertRaises(ValueError) as cm_openai:
            providers.call_openai_api("test", "gpt-4o")
        self.assertIn("OPENAI_API_KEY not configured", str(cm_openai.exception))

        os.environ["GEMINI_API_KEY"] = ""
        with self.assertRaises(ValueError) as cm_gemini:
            providers.call_gemini_api("test", "gemini-1.5-pro")
        self.assertIn("GEMINI_API_KEY not configured", str(cm_gemini.exception))

        assert "OPENAI_API_KEY not configured" in str(cm_openai.exception)

    def test_call_openai_api_rate_limit_resilience(self):
        """Invariant: call_openai_api catches HTTP 429 rate limit without unhandled exceptions."""
        err = urllib.error.HTTPError(
            url=f"http://127.0.0.1:{self.port}/v1/chat/completions",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=io.BytesIO(b'{"error":{"message":"Rate limit exceeded: 429"}}')
        )
        with mock.patch("urllib.request.urlopen", side_effect=err):
            res = providers.call_openai_api(
                prompt="Hello",
                model="gpt-4o",
                api_base=f"http://127.0.0.1:{self.port}/v1"
            )
        self.assertTrue(res["is_error"])
        self.assertIn("429", res["error"])
        assert res["is_error"] is True

    def test_call_openai_api_500_error_resilience(self):
        """Invariant: call_openai_api catches HTTP 500 server error without unhandled exceptions."""
        err = urllib.error.HTTPError(
            url=f"http://127.0.0.1:{self.port}/v1/chat/completions",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=io.BytesIO(b'{"error":{"message":"Internal server error: 500"}}')
        )
        with mock.patch("urllib.request.urlopen", side_effect=err):
            res = providers.call_openai_api(
                prompt="Hello",
                model="gpt-4o",
                api_base=f"http://127.0.0.1:{self.port}/v1"
            )
        self.assertTrue(res["is_error"])
        self.assertIn("500", res["error"])
        assert res["is_error"] is True

    def test_call_gemini_api_rate_limit_resilience(self):
        """Invariant: call_gemini_api catches HTTP 429 rate limit without unhandled exceptions."""
        err = urllib.error.HTTPError(
            url="https://generativelanguage.googleapis.com",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=io.BytesIO(b'{"error":{"message":"Resource exhausted: 429"}}')
        )
        with mock.patch("urllib.request.urlopen", side_effect=err):
            res = providers.call_gemini_api(prompt="Hello", model="gemini-1.5-pro")
        self.assertTrue(res["is_error"])
        self.assertIn("429", res["error"])
        assert res["is_error"] is True

    def test_call_gemini_api_500_error_resilience(self):
        """Invariant: call_gemini_api catches HTTP 500 server error without unhandled exceptions."""
        err = urllib.error.HTTPError(
            url="https://generativelanguage.googleapis.com",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=io.BytesIO(b'{"error":{"message":"Internal server error: 500"}}')
        )
        with mock.patch("urllib.request.urlopen", side_effect=err):
            res = providers.call_gemini_api(prompt="Hello", model="gemini-1.5-pro")
        self.assertTrue(res["is_error"])
        self.assertIn("500", res["error"])
        assert res["is_error"] is True

    def test_llm_plan_non_schema_response_rejection(self):
        """Invariant: non-schema text from mock endpoint is rejected by llm schema validation."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"

        with self.assertRaises(llm.Unavailable) as cm:
            llm.llm("plan", "Create execution plan")
        self.assertIn("no orchestrator provider could answer", str(cm.exception))
        assert "no orchestrator provider could answer" in str(cm.exception)

    def test_llm_review_non_schema_response_rejection(self):
        """Invariant: non-schema text from mock gemini endpoint is rejected by llm review schema validation."""
        ctx.C["REVIEW_PROVIDERS"] = "gemini-api"
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"

        with self.assertRaises(llm.Unavailable) as cm:
            llm.llm("review", "Review code changes")
        self.assertIn("no orchestrator provider could answer", str(cm.exception))
        assert "no orchestrator provider could answer" in str(cm.exception)

    def test_llm_plan_fallback_on_openai_rate_limit(self):
        """Invariant: llm catches openai rate limit error and cascades cleanly to gemini."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api, gemini-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"

        err = urllib.error.HTTPError(
            url="http://127.0.0.1/v1/chat/completions",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=io.BytesIO(b'{"error":{"message":"Rate limit exceeded"}}')
        )
        gemini_json = json.dumps({
            "candidates": [{
                "content": {"role": "model", "parts": [{"text": '{"tasks": [{"id": "0001-setup"}], "done": false}'}]},
                "finishReason": "STOP",
                "index": 0
            }]
        })

        def mock_urlopen(req, *args, **kwargs):
            if "chat/completions" in req.full_url:
                raise err
            resp = mock.MagicMock()
            resp.read.return_value = gemini_json.encode("utf-8")
            resp.__enter__.return_value = resp
            return resp

        with mock.patch("urllib.request.urlopen", side_effect=mock_urlopen):
            text, who = llm.llm("plan", "Plan tasks for goal")
        self.assertEqual(who, "gemini-api:gemini-1.5-pro")
        parsed = util.extract_json(text)
        self.assertIn("tasks", parsed)
        assert who == "gemini-api:gemini-1.5-pro"

    def test_llm_review_fallback_on_openai_500_error(self):
        """Invariant: llm catches openai HTTP 500 error and cascades cleanly to gemini."""
        ctx.C["REVIEW_PROVIDERS"] = "openai-api, gemini-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"

        err = urllib.error.HTTPError(
            url="http://127.0.0.1/v1/chat/completions",
            code=500,
            msg="Server Error",
            hdrs={},
            fp=io.BytesIO(b'{"error":{"message":"Internal server error"}}')
        )
        gemini_json = json.dumps({
            "candidates": [{
                "content": {"role": "model", "parts": [{"text": '{"verdict": "approve", "feedback": "Review verified"}'}]},
                "finishReason": "STOP",
                "index": 0
            }]
        })

        def mock_urlopen(req, *args, **kwargs):
            if "chat/completions" in req.full_url:
                raise err
            resp = mock.MagicMock()
            resp.read.return_value = gemini_json.encode("utf-8")
            resp.__enter__.return_value = resp
            return resp

        with mock.patch("urllib.request.urlopen", side_effect=mock_urlopen):
            text, who = llm.llm("review", "Review diff")
        self.assertEqual(who, "gemini-api:gemini-1.5-pro")
        parsed = util.extract_json(text)
        self.assertEqual(parsed["verdict"], "approve")
        assert who == "gemini-api:gemini-1.5-pro"

    def test_llm_plan_fallback_on_gemini_rate_limit(self):
        """Invariant: llm catches gemini rate limit error and cascades cleanly to openai."""
        ctx.C["PLAN_PROVIDERS"] = "gemini-api, openai-api"
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"

        err = urllib.error.HTTPError(
            url="https://generativelanguage.googleapis.com",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=io.BytesIO(b'{"error":{"message":"Resource exhausted"}}')
        )
        openai_json = json.dumps({
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": '{"tasks": [{"id": "0001-setup"}], "done": false}'},
                "finish_reason": "stop"
            }]
        })

        def mock_urlopen(req, *args, **kwargs):
            if "generateContent" in req.full_url:
                raise err
            resp = mock.MagicMock()
            resp.read.return_value = openai_json.encode("utf-8")
            resp.__enter__.return_value = resp
            return resp

        with mock.patch("urllib.request.urlopen", side_effect=mock_urlopen):
            text, who = llm.llm("plan", "Plan tasks for goal")
        self.assertEqual(who, "openai-api:gpt-4o")
        parsed = util.extract_json(text)
        self.assertIn("tasks", parsed)
        assert who == "openai-api:gpt-4o"

    def test_llm_review_fallback_on_gemini_500_error(self):
        """Invariant: llm catches gemini HTTP 500 error and cascades cleanly to openai."""
        ctx.C["REVIEW_PROVIDERS"] = "gemini-api, openai-api"
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"

        err = urllib.error.HTTPError(
            url="https://generativelanguage.googleapis.com",
            code=500,
            msg="Server Error",
            hdrs={},
            fp=io.BytesIO(b'{"error":{"message":"Internal server error"}}')
        )
        openai_json = json.dumps({
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": '{"verdict": "approve", "feedback": "Review verified"}'},
                "finish_reason": "stop"
            }]
        })

        def mock_urlopen(req, *args, **kwargs):
            if "generateContent" in req.full_url:
                raise err
            resp = mock.MagicMock()
            resp.read.return_value = openai_json.encode("utf-8")
            resp.__enter__.return_value = resp
            return resp

        with mock.patch("urllib.request.urlopen", side_effect=mock_urlopen):
            text, who = llm.llm("review", "Review diff")
        self.assertEqual(who, "openai-api:gpt-4o")
        parsed = util.extract_json(text)
        self.assertEqual(parsed["verdict"], "approve")
        assert who == "openai-api:gpt-4o"

    def test_llm_exhaustion_when_all_mock_providers_fail(self):
        """Invariant: llm raises Unavailable only when all mock providers fail with errors."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api, gemini-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"

        err = urllib.error.HTTPError(
            url="http://127.0.0.1",
            code=500,
            msg="Server Error",
            hdrs={},
            fp=io.BytesIO(b'{"error":{"message":"Internal server error"}}')
        )
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(llm.Unavailable) as cm:
                llm.llm("plan", "Plan tasks")

        self.assertIn("no orchestrator provider could answer", str(cm.exception))
        assert "no orchestrator provider could answer" in str(cm.exception)

    def test_invalid_schema_output_triggers_provider_fallback(self):
        """Invariant: non-schema output from primary provider triggers clean fallback to secondary."""
        ctx.C["REVIEW_PROVIDERS"] = "openai-api, gemini-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"

        gemini_json = json.dumps({
            "candidates": [{
                "content": {"role": "model", "parts": [{"text": '{"verdict": "approve", "feedback": "Review approved"}'}]},
                "finishReason": "STOP",
                "index": 0
            }]
        })
        openai_json = json.dumps({
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "I will not return any JSON schema today."},
                "finish_reason": "stop"
            }]
        })

        def mock_urlopen(req, *args, **kwargs):
            if "chat/completions" in req.full_url:
                resp = mock.MagicMock()
                resp.read.return_value = openai_json.encode("utf-8")
                resp.__enter__.return_value = resp
                return resp
            resp = mock.MagicMock()
            resp.read.return_value = gemini_json.encode("utf-8")
            resp.__enter__.return_value = resp
            return resp

        with mock.patch("urllib.request.urlopen", side_effect=mock_urlopen):
            text, who = llm.llm("review", "Review task diff")
        self.assertEqual(who, "gemini-api:gemini-1.5-pro")
        parsed = util.extract_json(text)
        self.assertEqual(parsed["verdict"], "approve")
        assert who == "gemini-api:gemini-1.5-pro"

    def test_llm_plan_openai_review_gemini_e2e(self):
        """Invariant: llm() dispatches plan to openai-api and review to gemini-api, exercises real fake server
        and validates returned text against TASK_SCHEMA and REVIEW_SCHEMA without leaking secrets in log_llm."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api"
        ctx.C["REVIEW_PROVIDERS"] = "gemini-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"

        # Verify providers_for resolves correctly
        self.assertEqual(llm.providers_for("plan"), ["openai-api"])
        self.assertEqual(llm.providers_for("review"), ["gemini-api"])

        prompt_plan = f"Plan tasks for goal in TASK_SCHEMA format:\n{TASK_SCHEMA}"
        prompt_review = 'Review diff against main. Reply with ONLY: {"verdict": "approve", "feedback": "All acceptance criteria verified"}'

        # Phase 1: Exercise real running fake-model-api.js fixture over loopback
        try:
            text_p, who_p = llm.llm("plan", prompt_plan)
            self.assertEqual(who_p, "openai-api:gpt-4o")
        except llm.Unavailable as cm:
            # Unmodified fixture returns non-schema text ('hello.txt'), which parse_and_validate rejects
            self.assertIn("no orchestrator provider could answer", str(cm))

        try:
            text_r, who_r = llm.llm("review", prompt_review)
            self.assertEqual(who_r, "gemini-api:gemini-1.5-pro")
        except llm.Unavailable as cm:
            # Unmodified fixture returns non-schema text ('ok'), which parse_and_validate rejects
            self.assertIn("no orchestrator provider could answer", str(cm))

        log_file = os.path.join(ctx.FD, "llm.jsonl")
        self.assertTrue(os.path.exists(log_file))
        with open(log_file, "r", encoding="utf-8") as f:
            log_data_live = f.read()

        # Real fixture calls logged with rc 0 and correct identities without secret leak
        self.assertNotIn(self.fake_openai_key, log_data_live)
        self.assertNotIn(self.fake_gemini_key, log_data_live)
        entries_live = [json.loads(line) for line in log_data_live.strip().splitlines() if line.strip()]
        self.assertTrue(any(e.get("who") == "openai-api:gpt-4o" and e.get("kind") == "plan" and e.get("rc") == 0 for e in entries_live))
        self.assertTrue(any(e.get("who") == "gemini-api:gemini-1.5-pro" and e.get("kind") == "review" and e.get("rc") == 0 for e in entries_live))

        # Phase 2: Validate that when valid schema JSON is returned, llm() returns (text, who)
        # where who is formatted as 'provider:model' and text validates against TASK_SCHEMA and REVIEW_SCHEMA
        canned_plan = json.dumps({
            "tasks": [{
                "key": "0001-setup",
                "title": "Setup task",
                "role": "backend-developer",
                "depends_on": [],
                "files": ["app.py"],
                "risk": "routine",
                "spec": "Setup application structure",
                "acceptance": ["app.py exists"]
            }],
            "goal_acceptance": ["All tests pass"],
            "notes": "Plan generated by mock model"
        })
        canned_review = json.dumps({
            "verdict": "approve",
            "feedback": "All acceptance criteria verified",
            "mutations": [],
            "lesson": ""
        })

        def mock_urlopen_openai_plan_gemini_review(req, *args, **kwargs):
            url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
            resp = mock.MagicMock()
            if "chat/completions" in url:
                resp_json = json.dumps({
                    "id": f"chatcmpl-{int(time.time())}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": "gpt-4o",
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": canned_plan},
                        "finish_reason": "stop"
                    }],
                    "usage": {"prompt_tokens": 1200, "completion_tokens": 40, "total_tokens": 1240}
                })
                resp.read.return_value = resp_json.encode("utf-8")
            else:
                resp_json = json.dumps({
                    "candidates": [{
                        "content": {"role": "model", "parts": [{"text": canned_review}]},
                        "finishReason": "STOP",
                        "index": 0
                    }],
                    "usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 30, "totalTokenCount": 1230},
                    "modelVersion": "gemini-fake"
                })
                resp.read.return_value = resp_json.encode("utf-8")
            resp.__enter__.return_value = resp
            return resp

        with mock.patch("urllib.request.urlopen", side_effect=mock_urlopen_openai_plan_gemini_review):
            text_plan, who_plan = llm.llm("plan", prompt_plan)
            self.assertEqual(who_plan, "openai-api:gpt-4o")
            parsed_plan = llm.parse_and_validate(text_plan, TASK_SCHEMA)
            self.assertIsNotNone(parsed_plan)
            self.assertIn("tasks", parsed_plan)
            self.assertTrue(len(parsed_plan["tasks"]) > 0)
            self.assertEqual(parsed_plan["tasks"][0]["key"], "0001-setup")
            self.assertIn("goal_acceptance", parsed_plan)

            # Validate against schema_for("plan")
            parsed_plan_obj = llm.parse_and_validate(text_plan, llm.schema_for("plan"))
            self.assertIsNotNone(parsed_plan_obj)

            text_review, who_review = llm.llm("review", prompt_review)
            self.assertEqual(who_review, "gemini-api:gemini-1.5-pro")
            parsed_review = llm.parse_and_validate(text_review, llm.REVIEW_SCHEMA)
            self.assertIsNotNone(parsed_review)
            self.assertEqual(parsed_review.get("verdict"), "approve")
            self.assertIn("feedback", parsed_review)

            # Validate against schema_for("review")
            parsed_review_schema = llm.parse_and_validate(text_review, llm.schema_for("review"))
            self.assertIsNotNone(parsed_review_schema)

        # Full log verification
        with open(log_file, "r", encoding="utf-8") as f:
            log_data = f.read()

        self.assertNotIn(self.fake_openai_key, log_data)
        self.assertNotIn(self.fake_gemini_key, log_data)

        assert who_plan == "openai-api:gpt-4o"
        assert parsed_plan is not None
        assert "tasks" in parsed_plan
        assert who_review == "gemini-api:gemini-1.5-pro"
        assert parsed_review is not None
        assert parsed_review.get("verdict") == "approve"
        assert self.fake_openai_key not in log_data
        assert self.fake_gemini_key not in log_data

    def test_llm_plan_gemini_review_openai_vice_versa_e2e(self):
        """Invariant: llm() dispatches plan to gemini-api and review to openai-api (vice versa), exercises real fake server
        and validates returned text against TASK_SCHEMA and REVIEW_SCHEMA without leaking secrets in log_llm."""
        ctx.C["PLAN_PROVIDERS"] = "gemini-api"
        ctx.C["REVIEW_PROVIDERS"] = "openai-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"

        # Verify providers_for resolves correctly
        self.assertEqual(llm.providers_for("plan"), ["gemini-api"])
        self.assertEqual(llm.providers_for("review"), ["openai-api"])

        prompt_plan = f"Plan tasks for goal in TASK_SCHEMA format:\n{TASK_SCHEMA}"
        prompt_review = 'Review diff against main. Reply with ONLY: {"verdict": "approve", "feedback": "All acceptance criteria verified"}'

        # Phase 1: Exercise real running fake-model-api.js fixture over loopback
        try:
            text_p, who_p = llm.llm("plan", prompt_plan)
            self.assertEqual(who_p, "gemini-api:gemini-1.5-pro")
        except llm.Unavailable as cm:
            # Unmodified fixture returns non-schema text ('ok'), which parse_and_validate rejects
            self.assertIn("no orchestrator provider could answer", str(cm))

        try:
            text_r, who_r = llm.llm("review", prompt_review)
            self.assertEqual(who_r, "openai-api:gpt-4o")
        except llm.Unavailable as cm:
            # Unmodified fixture returns non-schema text ('hello.txt'), which parse_and_validate rejects
            self.assertIn("no orchestrator provider could answer", str(cm))

        log_file = os.path.join(ctx.FD, "llm.jsonl")
        self.assertTrue(os.path.exists(log_file))
        with open(log_file, "r", encoding="utf-8") as f:
            log_data_live = f.read()

        # Real fixture calls logged with rc 0 and correct identities without secret leak
        self.assertNotIn(self.fake_openai_key, log_data_live)
        self.assertNotIn(self.fake_gemini_key, log_data_live)
        entries_live = [json.loads(line) for line in log_data_live.strip().splitlines() if line.strip()]
        self.assertTrue(any(e.get("who") == "gemini-api:gemini-1.5-pro" and e.get("kind") == "plan" and e.get("rc") == 0 for e in entries_live))
        self.assertTrue(any(e.get("who") == "openai-api:gpt-4o" and e.get("kind") == "review" and e.get("rc") == 0 for e in entries_live))

        # Phase 2: Schema validation vice versa
        canned_plan = json.dumps({
            "tasks": [{
                "key": "0001-setup",
                "title": "Setup task",
                "role": "backend-developer",
                "depends_on": [],
                "files": ["app.py"],
                "risk": "routine",
                "spec": "Setup application structure",
                "acceptance": ["app.py exists"]
            }],
            "goal_acceptance": ["All tests pass"],
            "notes": "Plan generated by mock model"
        })
        canned_review = json.dumps({
            "verdict": "approve",
            "feedback": "All acceptance criteria verified",
            "mutations": [],
            "lesson": ""
        })

        def mock_urlopen_gemini_plan_openai_review(req, *args, **kwargs):
            url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
            resp = mock.MagicMock()
            if "chat/completions" in url:
                resp_json = json.dumps({
                    "id": f"chatcmpl-{int(time.time())}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": "gpt-4o",
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": canned_review},
                        "finish_reason": "stop"
                    }],
                    "usage": {"prompt_tokens": 1200, "completion_tokens": 40, "total_tokens": 1240}
                })
                resp.read.return_value = resp_json.encode("utf-8")
            else:
                resp_json = json.dumps({
                    "candidates": [{
                        "content": {"role": "model", "parts": [{"text": canned_plan}]},
                        "finishReason": "STOP",
                        "index": 0
                    }],
                    "usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 30, "totalTokenCount": 1230},
                    "modelVersion": "gemini-fake"
                })
                resp.read.return_value = resp_json.encode("utf-8")
            resp.__enter__.return_value = resp
            return resp

        with mock.patch("urllib.request.urlopen", side_effect=mock_urlopen_gemini_plan_openai_review):
            text_plan, who_plan = llm.llm("plan", prompt_plan)
            self.assertEqual(who_plan, "gemini-api:gemini-1.5-pro")
            parsed_plan = llm.parse_and_validate(text_plan, TASK_SCHEMA)
            self.assertIsNotNone(parsed_plan)
            self.assertIn("tasks", parsed_plan)
            self.assertTrue(len(parsed_plan["tasks"]) > 0)
            self.assertEqual(parsed_plan["tasks"][0]["key"], "0001-setup")
            self.assertIsNotNone(llm.parse_and_validate(text_plan, llm.schema_for("plan")))

            text_review, who_review = llm.llm("review", prompt_review)
            self.assertEqual(who_review, "openai-api:gpt-4o")
            parsed_review = llm.parse_and_validate(text_review, llm.REVIEW_SCHEMA)
            self.assertIsNotNone(parsed_review)
            self.assertEqual(parsed_review.get("verdict"), "approve")
            self.assertIsNotNone(llm.parse_and_validate(text_review, llm.schema_for("review")))

        with open(log_file, "r", encoding="utf-8") as f:
            log_data = f.read()

        self.assertNotIn(self.fake_openai_key, log_data)
        self.assertNotIn(self.fake_gemini_key, log_data)

        assert who_plan == "gemini-api:gemini-1.5-pro"
        assert parsed_plan is not None
        assert who_review == "openai-api:gpt-4o"
        assert parsed_review is not None
        assert self.fake_openai_key not in log_data
        assert self.fake_gemini_key not in log_data

    def test_llm_e2e_secrets_from_file_store(self):
        """Invariant: when environment variables are unset, llm() resolves secrets from file store (~/.config/sandbox/secrets)
        without raising and without writing secret values to log_llm()."""
        # Populate file secret store in isolated temp directory
        openai_key_file = os.path.join(self.secrets_dir, "OPENAI_API_KEY")
        with open(openai_key_file, "w", encoding="utf-8") as f:
            f.write(self.fake_openai_key)
        os.chmod(openai_key_file, 0o600)

        gemini_key_file = os.path.join(self.secrets_dir, "GEMINI_API_KEY")
        with open(gemini_key_file, "w", encoding="utf-8") as f:
            f.write(self.fake_gemini_key)
        os.chmod(gemini_key_file, 0o600)

        # Patch expanduser to point ~/.config/sandbox/secrets to test's secret store
        orig_expanduser = os.path.expanduser
        def fake_expanduser(path):
            if path.startswith("~/.config/sandbox/secrets"):
                rel = os.path.relpath(path, "~/.config/sandbox/secrets")
                return os.path.join(self.secrets_dir, rel)
            return orig_expanduser(path)

        # Clear env keys to force resolution from file store
        os.environ["OPENAI_API_KEY"] = ""
        os.environ["GEMINI_API_KEY"] = ""

        ctx.C["PLAN_PROVIDERS"] = "openai-api"
        ctx.C["REVIEW_PROVIDERS"] = "gemini-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"

        prompt_plan = f"Plan tasks for goal in TASK_SCHEMA format:\n{TASK_SCHEMA}"
        prompt_review = 'Review diff against main. Reply with ONLY: {"verdict": "approve", "feedback": "All acceptance criteria verified"}'

        with mock.patch("os.path.expanduser", side_effect=fake_expanduser):
            # Phase 1: Real fixture calls with file-store secrets
            try:
                text_p, who_p = llm.llm("plan", prompt_plan)
                self.assertEqual(who_p, "openai-api:gpt-4o")
            except llm.Unavailable as cm:
                self.assertIn("no orchestrator provider could answer", str(cm))

            try:
                text_r, who_r = llm.llm("review", prompt_review)
                self.assertEqual(who_r, "gemini-api:gemini-1.5-pro")
            except llm.Unavailable as cm:
                self.assertIn("no orchestrator provider could answer", str(cm))

            # Phase 2: Schema validation with file-store secrets
            canned_plan = json.dumps({
                "tasks": [{
                    "key": "0001-setup",
                    "title": "Setup task",
                    "role": "backend-developer",
                    "depends_on": [],
                    "files": ["app.py"],
                    "risk": "routine",
                    "spec": "Setup application structure",
                    "acceptance": ["app.py exists"]
                }],
                "goal_acceptance": ["All tests pass"],
                "notes": "Plan generated by mock model"
            })
            canned_review = json.dumps({
                "verdict": "approve",
                "feedback": "All acceptance criteria verified",
                "mutations": [],
                "lesson": ""
            })

            def mock_urlopen_file_store(req, *args, **kwargs):
                url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
                resp = mock.MagicMock()
                if "chat/completions" in url:
                    resp_json = json.dumps({
                        "id": f"chatcmpl-{int(time.time())}",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": "gpt-4o",
                        "choices": [{
                            "index": 0,
                            "message": {"role": "assistant", "content": canned_plan},
                            "finish_reason": "stop"
                        }],
                        "usage": {"prompt_tokens": 1200, "completion_tokens": 40, "total_tokens": 1240}
                    })
                    resp.read.return_value = resp_json.encode("utf-8")
                else:
                    resp_json = json.dumps({
                        "candidates": [{
                            "content": {"role": "model", "parts": [{"text": canned_review}]},
                            "finishReason": "STOP",
                            "index": 0
                        }],
                        "usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 30, "totalTokenCount": 1230},
                        "modelVersion": "gemini-fake"
                    })
                    resp.read.return_value = resp_json.encode("utf-8")
                resp.__enter__.return_value = resp
                return resp

            with mock.patch("urllib.request.urlopen", side_effect=mock_urlopen_file_store):
                text_plan, who_plan = llm.llm("plan", prompt_plan)
                self.assertEqual(who_plan, "openai-api:gpt-4o")
                parsed_plan = llm.parse_and_validate(text_plan, TASK_SCHEMA)
                self.assertIsNotNone(parsed_plan)

                text_review, who_review = llm.llm("review", prompt_review)
                self.assertEqual(who_review, "gemini-api:gemini-1.5-pro")
                parsed_review = llm.parse_and_validate(text_review, llm.REVIEW_SCHEMA)
                self.assertIsNotNone(parsed_review)

        log_file = os.path.join(ctx.FD, "llm.jsonl")
        self.assertTrue(os.path.exists(log_file))
        with open(log_file, "r", encoding="utf-8") as f:
            log_data = f.read()

        self.assertNotIn(self.fake_openai_key, log_data)
        self.assertNotIn(self.fake_gemini_key, log_data)
        assert who_plan == "openai-api:gpt-4o"
        assert who_review == "gemini-api:gemini-1.5-pro"
        assert parsed_plan is not None
        assert parsed_review is not None
        assert self.fake_openai_key not in log_data
        assert self.fake_gemini_key not in log_data

    def test_llm_e2e_unicode_and_long_prompts(self):
        """Invariant: llm() correctly handles unicode, emojis, and multi-line content end-to-end
        without crashing and logs properly without encoding errors."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api"
        ctx.C["REVIEW_PROVIDERS"] = "gemini-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"

        unicode_plan = json.dumps({
            "tasks": [{
                "key": "0001-setup",
                "title": "Setup task 🚀",
                "role": "backend-developer",
                "depends_on": [],
                "files": ["app.py"],
                "risk": "routine",
                "spec": "Setup application structure with €100 price tag",
                "acceptance": ["app.py exists"]
            }],
            "goal_acceptance": ["All tests pass"],
            "notes": "Plan with unicode 🚀"
        })
        unicode_rev = json.dumps({
            "verdict": "approve",
            "feedback": "Good 🚀 all verified with €100",
            "mutations": [],
            "lesson": ""
        })

        def mock_urlopen_unicode(req, *args, **kwargs):
            url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
            resp = mock.MagicMock()
            if "chat/completions" in url:
                resp_json = json.dumps({
                    "id": f"chatcmpl-{int(time.time())}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": "gpt-4o",
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": unicode_plan},
                        "finish_reason": "stop"
                    }],
                    "usage": {"prompt_tokens": 1200, "completion_tokens": 40, "total_tokens": 1240}
                })
                resp.read.return_value = resp_json.encode("utf-8")
            else:
                resp_json = json.dumps({
                    "candidates": [{
                        "content": {"role": "model", "parts": [{"text": unicode_rev}]},
                        "finishReason": "STOP",
                        "index": 0
                    }],
                    "usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 30, "totalTokenCount": 1230},
                    "modelVersion": "gemini-fake"
                })
                resp.read.return_value = resp_json.encode("utf-8")
            resp.__enter__.return_value = resp
            return resp

        unicode_prompt = f"Plan goal with unicode: 🚀🔥 ‘smart-quotes’ and €100 price tag.\n{TASK_SCHEMA}"
        unicode_review_prompt = 'Review with unicode: 🚀🔥. Reply with ONLY: {"verdict": "approve", "feedback": "Good 🚀"}'

        with mock.patch("urllib.request.urlopen", side_effect=mock_urlopen_unicode):
            text_plan, who_plan = llm.llm("plan", unicode_prompt)
            self.assertEqual(who_plan, "openai-api:gpt-4o")
            parsed_plan = llm.parse_and_validate(text_plan, TASK_SCHEMA)
            self.assertIsNotNone(parsed_plan)

            text_rev, who_rev = llm.llm("review", unicode_review_prompt)
            self.assertEqual(who_rev, "gemini-api:gemini-1.5-pro")
            parsed_rev = llm.parse_and_validate(text_rev, llm.REVIEW_SCHEMA)
            self.assertIsNotNone(parsed_rev)

        log_file = os.path.join(ctx.FD, "llm.jsonl")
        self.assertTrue(os.path.exists(log_file))
        with open(log_file, "r", encoding="utf-8") as f:
            log_data = f.read()
        self.assertNotIn(self.fake_openai_key, log_data)
        self.assertNotIn(self.fake_gemini_key, log_data)

        assert who_plan == "openai-api:gpt-4o"
        assert who_rev == "gemini-api:gemini-1.5-pro"
        assert parsed_plan is not None
        assert parsed_rev is not None
        assert self.fake_openai_key not in log_data
        assert self.fake_gemini_key not in log_data

    def test_llm_e2e_log_llm_handles_provider_errors_without_secret_leak(self):
        """Invariant: when a provider call encounters an HTTP error, log_llm() is called without raising
        and never logs secret values into llm.jsonl."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"
        # Point to a 404 path on the fake server
        ctx.C["OPENAI_API_BASE"] = f"http://127.0.0.1:{self.port}/nonexistent-api"

        with self.assertRaises(llm.Unavailable) as cm:
            llm.llm("plan", f"Plan tasks:\n{TASK_SCHEMA}")
        self.assertIn("no orchestrator provider could answer", str(cm.exception))

        log_file = os.path.join(ctx.FD, "llm.jsonl")
        self.assertTrue(os.path.exists(log_file))
        with open(log_file, "r", encoding="utf-8") as f:
            log_data = f.read()
        self.assertNotIn(self.fake_openai_key, log_data)
        self.assertNotIn(self.fake_gemini_key, log_data)

        assert "no orchestrator provider could answer" in str(cm.exception)
        assert self.fake_openai_key not in log_data
        assert self.fake_gemini_key not in log_data

    def test_providers_for_list_and_whitespace_handling(self):
        """Invariant: providers_for() cleanly parses comma-delimited strings with spaces, lists, and tuples."""
        ctx.C["PLAN_PROVIDERS"] = " openai-api , gemini-api "
        self.assertEqual(llm.providers_for("plan"), ["openai-api", "gemini-api"])

        ctx.C["REVIEW_PROVIDERS"] = ["gemini-api", " openai-api "]
        self.assertEqual(llm.providers_for("review"), ["gemini-api", "openai-api"])

        ctx.C["PLAN_PROVIDERS"] = ""
        ctx.C["ORCHESTRATOR_PROVIDER"] = "openai-api"
        self.assertEqual(llm.providers_for("plan"), ["openai-api"])

        assert llm.providers_for("plan") == ["openai-api"]


if __name__ == "__main__":
    unittest.main()
