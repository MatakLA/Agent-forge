#!/usr/bin/env python3
"""Security audit tests for secret handling in orchestrator LLM provider calls.
Public interface: run via python3 tests/test_llm_providers_security.py or pytest.
Invariants:
- Raw API keys/secrets never leak into log_llm() 'head' argument or llm.jsonl.
- Raw API keys/secrets never leak into store.event() or events.jsonl.
- Raw API keys/secrets never appear in exception messages raised out of llm().
- Sanitization holds under HTTP errors, timeouts, network exceptions, and injection payloads.
"""
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))

# Top-level smoke-import check: prints traceback and exits non-zero on failure
try:
    import providers
    import sbx
    from foreman import ctx, llm, store, util
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)

for _mod in (providers, sbx, ctx, llm, store, util):
    assert _mod is not None, f"Module {_mod} failed to load"


SENTINEL_OPENAI = "fake-openai-sentinel-token-xyz-9876"
SENTINEL_GEMINI = "fake-gemini-sentinel-secret-abc-5432"
SENTINEL_ANTHROPIC = "fake-anthropic-sentinel-secret-def-1357"
SENTINEL_INJECTION = "fake-injection-sentinel-';DROP-TABLE-$(id)-🚀"


class TestLLMProvidersSecurity(unittest.TestCase):
    """Adversarial penetration tests auditing secret leakage in orchestrator LLM providers."""

    def setUp(self):
        """Isolate context, temp directory, and captured logs."""
        self.tmp = tempfile.mkdtemp(prefix="af-sec-audit-")
        self.old_fd = ctx.FD
        self.old_c = dict(ctx.C)
        self.old_env = {
            k: os.environ.get(k)
            for k in (
                "ORCHESTRATOR_PROVIDER",
                "PLAN_PROVIDERS",
                "REVIEW_PROVIDERS",
                "OPENAI_MODEL",
                "OPENAI_API_BASE",
                "GEMINI_MODEL",
                "ANTHROPIC_MODEL",
                "OPENAI_API_KEY",
                "GEMINI_API_KEY",
                "ANTHROPIC_API_KEY",
            )
        }
        ctx.FD = os.path.join(self.tmp, "foreman")
        os.makedirs(ctx.FD, exist_ok=True)
        self.captured_heads = []
        self.captured_events = []

        self.orig_log_llm = llm.log_llm
        self.orig_event = store.event

        def _spy_log_llm(kind, who, rc, secs, cost, usage, head):
            self.captured_heads.append(str(head) if head is not None else "")
            return self.orig_log_llm(kind, who, rc, secs, cost, usage, head)

        def _spy_event(msg, **kw):
            self.captured_events.append(str(msg))
            return self.orig_event(msg, **kw)

        self.log_patcher = mock.patch.object(llm, "log_llm", side_effect=_spy_log_llm)
        self.event_patcher = mock.patch.object(store, "event", side_effect=_spy_event)
        self.log_patcher.start()
        self.event_patcher.start()

    def tearDown(self):
        """Restore environment, context and temporary files."""
        self.log_patcher.stop()
        self.event_patcher.stop()
        ctx.FD = self.old_fd
        ctx.C.clear()
        ctx.C.update(self.old_c)
        for k, v in self.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _read_llm_jsonl(self):
        p = os.path.join(ctx.FD, "llm.jsonl")
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        return ""

    def _read_events_jsonl(self):
        p = os.path.join(ctx.FD, "events.jsonl")
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        return ""

    def test_http_error_does_not_leak_secret(self):
        """Provider HTTP error with headers/body echoing credentials must not leak secrets."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"

        def _fake_get_secret(name, default=""):
            if name == "OPENAI_API_KEY":
                return SENTINEL_OPENAI
            return default

        error_body = json.dumps({
            "error": {
                "message": f"Unauthorized: invalid header Authorization: Bearer {SENTINEL_OPENAI}",
                "type": "invalid_request_error",
                "code": "invalid_api_key",
            }
        }).encode("utf-8")

        http_err = urllib.error.HTTPError(
            url="https://api.openai.com/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs={"Content-Type": "application/json"},
            fp=io.BytesIO(error_body),
        )

        with mock.patch("providers.get_secret", side_effect=_fake_get_secret):
            with mock.patch("urllib.request.urlopen", side_effect=http_err):
                with self.assertRaises(llm.Unavailable) as cm:
                    llm.llm("plan", "Generate project plan")

        exc_msg = str(cm.exception)
        llm_content = self._read_llm_jsonl()
        events_content = self._read_events_jsonl()

        for head in self.captured_heads:
            self.assertNotIn(SENTINEL_OPENAI, head)
            assert SENTINEL_OPENAI not in head

        for ev in self.captured_events:
            self.assertNotIn(SENTINEL_OPENAI, ev)
            assert SENTINEL_OPENAI not in ev

        self.assertNotIn(SENTINEL_OPENAI, llm_content)
        self.assertNotIn(SENTINEL_OPENAI, events_content)
        self.assertNotIn(SENTINEL_OPENAI, exc_msg)
        assert SENTINEL_OPENAI not in llm_content
        assert SENTINEL_OPENAI not in events_content
        assert SENTINEL_OPENAI not in exc_msg

    def test_timeout_and_network_error_do_not_leak_secret(self):
        """Network timeout or connection error mentioning secrets must not leak into logs or exceptions."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"

        def _fake_get_secret(name, default=""):
            if name == "OPENAI_API_KEY":
                return SENTINEL_OPENAI
            return default

        timeout_err = urllib.error.URLError(
            socket.timeout(f"timed out connecting to https://api.openai.com/v1?token={SENTINEL_OPENAI} Bearer {SENTINEL_OPENAI}")
        )

        with mock.patch("providers.get_secret", side_effect=_fake_get_secret):
            with mock.patch("urllib.request.urlopen", side_effect=timeout_err):
                with self.assertRaises(llm.Unavailable) as cm:
                    llm.llm("plan", "Generate roadmap")

        exc_msg = str(cm.exception)
        llm_content = self._read_llm_jsonl()
        events_content = self._read_events_jsonl()

        for head in self.captured_heads:
            self.assertNotIn(SENTINEL_OPENAI, head)
            assert SENTINEL_OPENAI not in head

        for ev in self.captured_events:
            self.assertNotIn(SENTINEL_OPENAI, ev)
            assert SENTINEL_OPENAI not in ev

        self.assertNotIn(SENTINEL_OPENAI, llm_content)
        self.assertNotIn(SENTINEL_OPENAI, events_content)
        self.assertNotIn(SENTINEL_OPENAI, exc_msg)
        assert SENTINEL_OPENAI not in llm_content
        assert SENTINEL_OPENAI not in events_content
        assert SENTINEL_OPENAI not in exc_msg

    def test_success_path_normal_logging_does_not_leak_secret(self):
        """Mocked 200 response on success path must never record secrets in logs or events."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api"
        ctx.C["REVIEW_PROVIDERS"] = "openai-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"

        def _fake_get_secret(name, default=""):
            if name == "OPENAI_API_KEY":
                return SENTINEL_OPENAI
            return default

        success_response = {
            "choices": [{"message": {"content": '{"verdict": "approve", "feedback": "looks good"}'}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        }

        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = json.dumps(success_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with mock.patch("providers.get_secret", side_effect=_fake_get_secret):
            with mock.patch("urllib.request.urlopen", return_value=mock_resp):
                text, who = llm.llm("review", "Review pull request")

        self.assertEqual(who, "openai-api:gpt-4o")
        self.assertIn("approve", text)

        llm_content = self._read_llm_jsonl()
        events_content = self._read_events_jsonl()

        for head in self.captured_heads:
            self.assertNotIn(SENTINEL_OPENAI, head)
            assert SENTINEL_OPENAI not in head

        for ev in self.captured_events:
            self.assertNotIn(SENTINEL_OPENAI, ev)
            assert SENTINEL_OPENAI not in ev

        self.assertNotIn(SENTINEL_OPENAI, llm_content)
        self.assertNotIn(SENTINEL_OPENAI, events_content)
        assert SENTINEL_OPENAI not in llm_content
        assert SENTINEL_OPENAI not in events_content
        assert who == "openai-api:gpt-4o"

    def test_gemini_and_anthropic_error_handling_does_not_leak_secret(self):
        """Gemini (x-goog-api-key) and Anthropic (x-api-key) error responses must not leak secrets."""
        ctx.C["PLAN_PROVIDERS"] = "gemini-api,anthropic-api"
        ctx.C["GEMINI_MODEL"] = "gemini-2.0-flash"
        ctx.C["ANTHROPIC_MODEL"] = "claude-3-5-sonnet"

        def _fake_get_secret(name, default=""):
            if name == "GEMINI_API_KEY":
                return SENTINEL_GEMINI
            if name == "ANTHROPIC_API_KEY":
                return SENTINEL_ANTHROPIC
            return default

        gemini_body = json.dumps({
            "error": {
                "message": f"API key not valid: x-goog-api-key {SENTINEL_GEMINI}",
                "status": "INVALID_ARGUMENT",
            }
        }).encode("utf-8")

        anthropic_body = json.dumps({
            "type": "error",
            "error": {
                "type": "authentication_error",
                "message": f"x-api-key: {SENTINEL_ANTHROPIC} was rejected",
            }
        }).encode("utf-8")

        err1 = urllib.error.HTTPError("https://generativelanguage.googleapis.com", 400, "Bad Request", {}, io.BytesIO(gemini_body))
        err2 = urllib.error.HTTPError("https://api.anthropic.com", 401, "Unauthorized", {}, io.BytesIO(anthropic_body))

        with mock.patch("providers.get_secret", side_effect=_fake_get_secret):
            with mock.patch("urllib.request.urlopen", side_effect=[err1, err2]):
                with self.assertRaises(llm.Unavailable) as cm:
                    llm.llm("plan", "Create architecture plan")

        exc_msg = str(cm.exception)
        llm_content = self._read_llm_jsonl()
        events_content = self._read_events_jsonl()

        for head in self.captured_heads:
            self.assertNotIn(SENTINEL_GEMINI, head)
            self.assertNotIn(SENTINEL_ANTHROPIC, head)
            assert SENTINEL_GEMINI not in head
            assert SENTINEL_ANTHROPIC not in head

        self.assertNotIn(SENTINEL_GEMINI, llm_content)
        self.assertNotIn(SENTINEL_ANTHROPIC, llm_content)
        self.assertNotIn(SENTINEL_GEMINI, events_content)
        self.assertNotIn(SENTINEL_ANTHROPIC, events_content)
        self.assertNotIn(SENTINEL_GEMINI, exc_msg)
        self.assertNotIn(SENTINEL_ANTHROPIC, exc_msg)

        assert SENTINEL_GEMINI not in llm_content
        assert SENTINEL_ANTHROPIC not in llm_content
        assert SENTINEL_GEMINI not in exc_msg
        assert SENTINEL_ANTHROPIC not in exc_msg

    def test_adversarial_injection_payloads_sanitization(self):
        """Adversarial sentinel keys containing injection tokens, shell syntax, and unicode are sanitized."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"

        def _fake_get_secret(name, default=""):
            if name == "OPENAI_API_KEY":
                return SENTINEL_INJECTION
            return default

        injection_body = (
            f"HTTP 500: Server crashed with Authorization: Bearer {SENTINEL_INJECTION}\n"
            f"Details: invalid token '{SENTINEL_INJECTION}'"
        ).encode("utf-8")

        http_err = urllib.error.HTTPError(
            url="https://api.openai.com/v1/chat/completions",
            code=500,
            msg="Internal Error",
            hdrs={},
            fp=io.BytesIO(injection_body),
        )

        with mock.patch("providers.get_secret", side_effect=_fake_get_secret):
            with mock.patch("urllib.request.urlopen", side_effect=http_err):
                with self.assertRaises(llm.Unavailable) as cm:
                    llm.llm("plan", "Inject attack")

        exc_msg = str(cm.exception)
        llm_content = self._read_llm_jsonl()

        for head in self.captured_heads:
            self.assertNotIn(SENTINEL_INJECTION, head)
            assert SENTINEL_INJECTION not in head

        self.assertNotIn(SENTINEL_INJECTION, llm_content)
        self.assertNotIn(SENTINEL_INJECTION, exc_msg)
        assert SENTINEL_INJECTION not in llm_content
        assert SENTINEL_INJECTION not in exc_msg

    def test_generic_provider_exception_does_not_leak_secret(self):
        """Unexpected provider exceptions during API calls must sanitize secrets in logs and events."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"

        def _fake_get_secret(name, default=""):
            if name == "OPENAI_API_KEY":
                return SENTINEL_OPENAI
            return default

        with mock.patch("providers.get_secret", side_effect=_fake_get_secret):
            with mock.patch("urllib.request.urlopen", side_effect=RuntimeError(f"SSL handshake error: token={SENTINEL_OPENAI} Bearer {SENTINEL_OPENAI}")):
                with self.assertRaises(llm.Unavailable) as cm:
                    llm.llm("plan", "Generate schema")

        exc_msg = str(cm.exception)
        llm_content = self._read_llm_jsonl()

        for head in self.captured_heads:
            self.assertNotIn(SENTINEL_OPENAI, head)
            assert SENTINEL_OPENAI not in head

        self.assertNotIn(SENTINEL_OPENAI, llm_content)
        self.assertNotIn(SENTINEL_OPENAI, exc_msg)
        assert SENTINEL_OPENAI not in llm_content
        assert SENTINEL_OPENAI not in exc_msg

    def test_log_llm_sanitizes_secrets_in_head(self):
        """log_llm() must sanitize secrets, bearer tokens, and headers before persisting to llm.jsonl."""
        def _fake_get_secret(name, default=""):
            if name == "OPENAI_API_KEY":
                return SENTINEL_OPENAI
            if name == "GEMINI_API_KEY":
                return SENTINEL_GEMINI
            return default

        with mock.patch("providers.get_secret", side_effect=_fake_get_secret):
            llm.log_llm(
                "plan",
                "openai-api:gpt-4o",
                1,
                0.2,
                None,
                None,
                f"Request failed: Authorization: Bearer {SENTINEL_OPENAI} x-goog-api-key: {SENTINEL_GEMINI}",
            )

        llm_content = self._read_llm_jsonl()
        self.assertNotIn(SENTINEL_OPENAI, llm_content)
        self.assertNotIn(SENTINEL_GEMINI, llm_content)
        self.assertIn("[REDACTED]", llm_content)
        assert SENTINEL_OPENAI not in llm_content
        assert SENTINEL_GEMINI not in llm_content

    def test_event_sanitizes_secrets(self):
        """llm.event() must sanitize secrets, tokens, and credentials before writing to events.jsonl."""
        def _fake_get_secret(name, default=""):
            if name == "OPENAI_API_KEY":
                return SENTINEL_OPENAI
            return default

        with mock.patch("providers.get_secret", side_effect=_fake_get_secret):
            llm.event(f"Provider failed with Authorization: Bearer {SENTINEL_OPENAI}")

        events_content = self._read_events_jsonl()
        self.assertNotIn(SENTINEL_OPENAI, events_content)
        self.assertIn("[REDACTED]", events_content)
        assert SENTINEL_OPENAI not in events_content


if __name__ == "__main__":
    unittest.main()
