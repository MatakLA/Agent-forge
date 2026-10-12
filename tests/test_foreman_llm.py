#!/usr/bin/env python3
"""Tests for Foreman LLM provider routing, JSON extraction, and fallback (bin/lib/foreman/llm.py).

Public interface: runs directly via python3 tests/test_foreman_llm.py or unittest discovery.
Invariants:
- providers_for: resolves configured provider lists for plan and review kinds, falling back to ORCHESTRATOR_PROVIDER.
- llm dispatch: routes to openai-api, gemini-api, anthropic-api, claude-cli, agy with caller identity tracking (who).
- robust JSON extraction: extracts and validates JSON across markdown fences and prose for REVIEW_SCHEMA and OBJECT_SCHEMA.
- fallback & exhaustion: provider errors, missing keys, or invalid schemas cascade to next provider, raising Unavailable on total exhaustion.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

# Set test environment before any sbx or foreman imports
import atexit
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = tempfile.mkdtemp(prefix="af-test-llm-")
atexit.register(shutil.rmtree, T, ignore_errors=True)
os.environ["SANDBOX_TEST"] = "1"
os.environ["SANDBOX_RUNS_DIR"] = T
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))

try:
    import providers
    import sbx
    from foreman import ctx, llm, store, util
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Top-level smoke-import check: ensures modules load cleanly without silent failure
for _mod in (sbx, ctx, llm, providers, store, util):
    assert _mod is not None, f"Module {_mod} failed to load"


class TestForemanLLM(unittest.TestCase):
    """Test suite for bin/lib/foreman/llm.py: providers_for, llm dispatch, extraction, and fallback."""

    def setUp(self):
        """Prepare throwaway directory and isolate context."""
        self.tmp = tempfile.mkdtemp(prefix="af-llm-test-")
        self.old_fd = ctx.FD
        self.old_c = dict(ctx.C)
        self.old_env_orch = os.environ.get("ORCHESTRATOR_PROVIDER")

        ctx.FD = os.path.join(self.tmp, "foreman")
        os.makedirs(ctx.FD, exist_ok=True)

        if "ORCHESTRATOR_PROVIDER" in os.environ:
            del os.environ["ORCHESTRATOR_PROVIDER"]

    def tearDown(self):
        """Restore global context and clean up temp files."""
        ctx.FD = self.old_fd
        ctx.C.clear()
        ctx.C.update(self.old_c)

        if self.old_env_orch is not None:
            os.environ["ORCHESTRATOR_PROVIDER"] = self.old_env_orch
        elif "ORCHESTRATOR_PROVIDER" in os.environ:
            del os.environ["ORCHESTRATOR_PROVIDER"]

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_providers_for_plan_resolution(self):
        """Test resolving configured providers for 'plan' decision kind."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api, gemini-api, claude-cli"
        ctx.C["REVIEW_PROVIDERS"] = "agy-claude"
        provs = llm.providers_for("plan")
        self.assertEqual(provs, ["openai-api", "gemini-api", "claude-cli"])
        assert provs == ["openai-api", "gemini-api", "claude-cli"]

    def test_providers_for_review_resolution(self):
        """Test resolving configured providers for 'review' decision kind."""
        ctx.C["PLAN_PROVIDERS"] = "claude-cli"
        ctx.C["REVIEW_PROVIDERS"] = "gemini-api, openai-api"
        provs = llm.providers_for("review")
        self.assertEqual(provs, ["gemini-api", "openai-api"])
        assert provs == ["gemini-api", "openai-api"]

    def test_providers_for_orchestrator_provider_fallback(self):
        """Test fallback to ORCHESTRATOR_PROVIDER when specific kind is missing or empty."""
        ctx.C.clear()
        ctx.C["ORCHESTRATOR_PROVIDER"] = "openai-api"
        plan_provs = llm.providers_for("plan")
        review_provs = llm.providers_for("review")
        self.assertEqual(plan_provs, ["openai-api"])
        self.assertEqual(review_provs, ["openai-api"])
        assert plan_provs == ["openai-api"]
        assert review_provs == ["openai-api"]

    def test_providers_for_environment_fallback(self):
        """Test fallback to os.environ['ORCHESTRATOR_PROVIDER'] when ctx.C has neither."""
        ctx.C.clear()
        os.environ["ORCHESTRATOR_PROVIDER"] = "gemini-api"
        plan_provs = llm.providers_for("plan")
        review_provs = llm.providers_for("review")
        self.assertEqual(plan_provs, ["gemini-api"])
        self.assertEqual(review_provs, ["gemini-api"])
        assert plan_provs == ["gemini-api"]
        assert review_provs == ["gemini-api"]

    def test_providers_for_whitespace_and_list_support(self):
        """Test providers_for cleans whitespace, filters empty strings, and handles list inputs."""
        ctx.C["PLAN_PROVIDERS"] = "  openai-api , , gemini-api ,   "
        provs_str = llm.providers_for("plan")
        self.assertEqual(provs_str, ["openai-api", "gemini-api"])

        ctx.C["PLAN_PROVIDERS"] = [" openai-api ", "gemini-api"]
        provs_list = llm.providers_for("plan")
        self.assertEqual(provs_list, ["openai-api", "gemini-api"])
        assert provs_str == ["openai-api", "gemini-api"]
        assert provs_list == ["openai-api", "gemini-api"]

    def test_dispatch_openai_api_success_with_model_identity(self):
        """Test llm dispatch to openai-api with correct model identity and parameters."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"
        ctx.C["OPENAI_API_BASE"] = "https://mock.openai.test/v1"

        mock_result = {
            "text": '{"tasks": [{"id": "0001", "title": "Setup"}], "done": false}',
            "provider": "openai-api",
            "model": "gpt-4o",
            "duration_s": 0.42,
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            "is_error": False,
            "error": None,
        }

        with mock.patch("providers.call_openai_api", return_value=mock_result) as mock_call:
            text, who = llm.llm("plan", "Generate tasks for goal", subject="G20261011-0001")

        self.assertEqual(who, "openai-api:gpt-4o")
        self.assertTrue(mock_call.called)
        call_args = mock_call.call_args
        self.assertEqual(call_args.kwargs.get("model") or call_args.args[1], "gpt-4o")
        self.assertEqual(call_args.kwargs.get("api_base"), "https://mock.openai.test/v1")
        assert who == "openai-api:gpt-4o"

    def test_dispatch_gemini_api_success_with_model_identity(self):
        """Test llm dispatch to gemini-api with correct model identity and review schema."""
        ctx.C["REVIEW_PROVIDERS"] = "gemini-api"
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"

        mock_result = {
            "text": '{"verdict": "approve", "feedback": "Code changes look solid"}',
            "provider": "gemini-api",
            "model": "gemini-1.5-pro",
            "duration_s": 0.35,
            "usage": {"promptTokenCount": 200, "candidatesTokenCount": 60},
            "is_error": False,
            "error": None,
        }

        with mock.patch("providers.call_gemini_api", return_value=mock_result) as mock_call:
            text, who = llm.llm("review", "Review pull request diff", subject="0002-test")

        self.assertEqual(who, "gemini-api:gemini-1.5-pro")
        self.assertTrue(mock_call.called)
        call_args = mock_call.call_args
        self.assertEqual(call_args.kwargs.get("model") or call_args.args[1], "gemini-1.5-pro")
        assert who == "gemini-api:gemini-1.5-pro"

    def test_dispatch_anthropic_api_success_with_model_identity(self):
        """Test llm dispatch to anthropic-api with correct model identity."""
        ctx.C["PLAN_PROVIDERS"] = "anthropic-api"
        ctx.C["ANTHROPIC_MODEL"] = "claude-3-5-sonnet"

        mock_result = {
            "text": '{"tasks": [], "done": true}',
            "provider": "anthropic-api",
            "model": "claude-3-5-sonnet",
            "duration_s": 0.28,
            "usage": {"input_tokens": 80, "output_tokens": 20},
            "is_error": False,
            "error": None,
        }

        with mock.patch("providers.call_anthropic_api", return_value=mock_result):
            text, who = llm.llm("plan", "Review plan")

        self.assertEqual(who, "anthropic-api:claude-3-5-sonnet")
        assert who == "anthropic-api:claude-3-5-sonnet"

    def test_json_extraction_markdown_fence_review_schema(self):
        """Test robust JSON extraction when review response is enclosed in markdown code fences."""
        ctx.C["REVIEW_PROVIDERS"] = "openai-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"

        fenced_payload = (
            "Here is my review verdict:\n\n"
            "```json\n"
            "{\n"
            '  "verdict": "rework",\n'
            '  "feedback": "Needs additional test coverage for boundary cases.",\n'
            '  "lesson": "Always check empty inputs"\n'
            "}\n"
            "```\n\n"
            "Please address the feedback."
        )

        mock_result = {
            "text": fenced_payload,
            "provider": "openai-api",
            "model": "gpt-4o",
            "duration_s": 0.5,
            "usage": {},
            "is_error": False,
            "error": None,
        }

        with mock.patch("providers.call_openai_api", return_value=mock_result):
            text, who = llm.llm("review", "Review diff")

        parsed = util.extract_json(text)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.get("verdict"), "rework")
        self.assertEqual(parsed.get("lesson"), "Always check empty inputs")
        assert parsed["verdict"] == "rework"

    def test_json_extraction_prose_surrounded_plan_schema(self):
        """Test robust JSON extraction when plan response is surrounded by conversational prose."""
        ctx.C["PLAN_PROVIDERS"] = "gemini-api"
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"

        prose_payload = (
            "Certainly! Based on the requirements, I have generated the following task breakdown:\n"
            '{"tasks": [{"id": "0010-auth", "title": "Implement auth route", "role": "backend-developer"}], "done": false}\n'
            "Let me know if further adjustments are needed."
        )

        mock_result = {
            "text": prose_payload,
            "provider": "gemini-api",
            "model": "gemini-1.5-pro",
            "duration_s": 0.4,
            "usage": {},
            "is_error": False,
            "error": None,
        }

        with mock.patch("providers.call_gemini_api", return_value=mock_result):
            text, who = llm.llm("plan", "Plan goal")

        parsed = util.extract_json(text)
        self.assertIsNotNone(parsed)
        self.assertIsInstance(parsed.get("tasks"), list)
        self.assertEqual(parsed["tasks"][0]["id"], "0010-auth")
        assert len(parsed["tasks"]) == 1

    def test_provider_fallback_on_missing_api_key(self):
        """Test fallback to subsequent provider when primary provider raises ValueError for missing key."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api, gemini-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"

        gemini_result = {
            "text": '{"tasks": [], "done": true}',
            "provider": "gemini-api",
            "model": "gemini-1.5-pro",
            "duration_s": 0.3,
            "usage": {},
            "is_error": False,
            "error": None,
        }

        with mock.patch("providers.call_openai_api", side_effect=ValueError("OPENAI_API_KEY not configured")):
            with mock.patch("providers.call_gemini_api", return_value=gemini_result) as mock_gemini:
                text, who = llm.llm("plan", "Plan prompt")

        self.assertEqual(who, "gemini-api:gemini-1.5-pro")
        self.assertTrue(mock_gemini.called)
        assert who == "gemini-api:gemini-1.5-pro"

    def test_provider_fallback_on_api_error_response(self):
        """Test fallback to subsequent provider when primary returns an API error result."""
        ctx.C["REVIEW_PROVIDERS"] = "openai-api, gemini-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"

        openai_err = {
            "text": "",
            "provider": "openai-api",
            "model": "gpt-4o",
            "duration_s": 0.2,
            "usage": {},
            "is_error": True,
            "error": "HTTP 429: Rate limit exceeded",
        }
        gemini_ok = {
            "text": '{"verdict": "approve", "feedback": "All clear"}',
            "provider": "gemini-api",
            "model": "gemini-1.5-pro",
            "duration_s": 0.3,
            "usage": {},
            "is_error": False,
            "error": None,
        }

        with mock.patch("providers.call_openai_api", return_value=openai_err):
            with mock.patch("providers.call_gemini_api", return_value=gemini_ok):
                text, who = llm.llm("review", "Review prompt")

        self.assertEqual(who, "gemini-api:gemini-1.5-pro")
        assert who == "gemini-api:gemini-1.5-pro"

    def test_provider_fallback_on_network_exception(self):
        """Test fallback when primary provider raises an unhandled network exception."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api, gemini-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"

        gemini_ok = {
            "text": '{"tasks": [], "done": true}',
            "provider": "gemini-api",
            "model": "gemini-1.5-pro",
            "duration_s": 0.2,
            "usage": {},
            "is_error": False,
            "error": None,
        }

        with mock.patch("providers.call_openai_api", side_effect=OSError("Network connection refused")):
            with mock.patch("providers.call_gemini_api", return_value=gemini_ok):
                text, who = llm.llm("plan", "Plan prompt")

        self.assertEqual(who, "gemini-api:gemini-1.5-pro")
        assert who == "gemini-api:gemini-1.5-pro"

    def test_provider_fallback_on_invalid_schema_output(self):
        """Test fallback when primary provider returns non-JSON or invalid review schema (missing verdict)."""
        ctx.C["REVIEW_PROVIDERS"] = "openai-api, gemini-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"

        openai_invalid = {
            "text": "I am a helpful assistant, but I will not output JSON today.",
            "provider": "openai-api",
            "model": "gpt-4o",
            "duration_s": 0.2,
            "usage": {},
            "is_error": False,
            "error": None,
        }
        gemini_ok = {
            "text": '{"verdict": "reject", "feedback": "Criteria unmet"}',
            "provider": "gemini-api",
            "model": "gemini-1.5-pro",
            "duration_s": 0.25,
            "usage": {},
            "is_error": False,
            "error": None,
        }

        with mock.patch("providers.call_openai_api", return_value=openai_invalid):
            with mock.patch("providers.call_gemini_api", return_value=gemini_ok):
                text, who = llm.llm("review", "Review prompt")

        self.assertEqual(who, "gemini-api:gemini-1.5-pro")
        parsed = util.extract_json(text)
        self.assertEqual(parsed.get("verdict"), "reject")
        assert who == "gemini-api:gemini-1.5-pro"

    def test_unavailable_when_all_providers_fail(self):
        """Test Unavailable is raised only when all configured providers are exhausted."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api, gemini-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"

        openai_err = {"text": "", "provider": "openai-api", "model": "gpt-4o", "duration_s": 0.1, "usage": {}, "is_error": True, "error": "timeout"}
        gemini_err = {"text": "", "provider": "gemini-api", "model": "gemini-1.5-pro", "duration_s": 0.1, "usage": {}, "is_error": True, "error": "rate limit"}

        with mock.patch("providers.call_openai_api", return_value=openai_err):
            with mock.patch("providers.call_gemini_api", return_value=gemini_err):
                with self.assertRaises(llm.Unavailable):
                    llm.llm("plan", "Plan prompt")

        assert True

    def test_unavailable_when_no_providers_configured(self):
        """Test Unavailable is raised immediately when no providers are configured."""
        ctx.C.clear()
        ctx.C["PLAN_PROVIDERS"] = ""
        with self.assertRaises(llm.Unavailable):
            llm.llm("plan", "Plan prompt")
        assert True

    def test_log_llm_records_who_timing_and_usage(self):
        """Test log_llm records provider caller identity, timing, cost, and token usage to llm.jsonl."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"

        mock_result = {
            "text": '{"tasks": []}',
            "provider": "openai-api",
            "model": "gpt-4o",
            "duration_s": 1.25,
            "usage": {"total_tokens": 120},
            "is_error": False,
            "error": None,
        }

        with mock.patch("providers.call_openai_api", return_value=mock_result):
            llm.llm("plan", "Plan prompt", subject="G20261011-9999")

        log_path = os.path.join(ctx.FD, "llm.jsonl")
        self.assertTrue(os.path.exists(log_path))
        with open(log_path, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]

        self.assertGreater(len(lines), 0)
        entry = lines[-1]
        self.assertEqual(entry["who"], "openai-api:gpt-4o")
        self.assertEqual(entry["kind"], "plan")
        self.assertEqual(entry["rc"], 0)
        self.assertEqual(entry["subject"], "G20261011-9999")
        self.assertEqual(entry["usage"], {"total_tokens": 120})
        self.assertAlmostEqual(entry["secs"], 1.2, places=1)
        assert entry["who"] == "openai-api:gpt-4o"

    def test_log_llm_records_error_entry(self):
        """Test _via_paid_api logs error entries with rc=1 when provider call fails."""
        ctx.C["OPENAI_MODEL"] = "gpt-4o"
        err_res = {
            "text": "",
            "provider": "openai-api",
            "model": "gpt-4o",
            "duration_s": 0.5,
            "usage": {},
            "is_error": True,
            "error": "HTTP 502: Bad Gateway",
        }

        with mock.patch("providers.call_openai_api", return_value=err_res):
            text, who = llm._via_paid_api("openai-api", "Prompt", "System")

        self.assertIsNone(text)
        self.assertIsNone(who)

        log_path = os.path.join(ctx.FD, "llm.jsonl")
        self.assertTrue(os.path.exists(log_path))
        with open(log_path, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]

        self.assertGreater(len(lines), 0)
        entry = lines[-1]
        self.assertEqual(entry["rc"], 1)
        self.assertEqual(entry["who"], "openai-api:gpt-4o")
        assert entry["rc"] == 1

    def test_schemaless_ask_kind(self):
        """Test decision kind 'ask' succeeds with non-JSON plain text responses."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"

        mock_result = {
            "text": "Yes, you should definitely proceed with database migrations.",
            "provider": "openai-api",
            "model": "gpt-4o",
            "duration_s": 0.3,
            "usage": {},
            "is_error": False,
            "error": None,
        }

        with mock.patch("providers.call_openai_api", return_value=mock_result):
            text, who = llm.llm("ask", "Should we run migrations?")

        self.assertEqual(who, "openai-api:gpt-4o")
        self.assertIn("database migrations", text)
        assert who == "openai-api:gpt-4o"

    def test_schema_validation_direct(self):
        """Unit test for validate_schema and parse_and_validate against REVIEW_SCHEMA and OBJECT_SCHEMA."""
        valid_review = '{"verdict": "approve", "feedback": "Good job", "lesson": "test"}'
        self.assertIsNotNone(llm.parse_and_validate(valid_review, llm.REVIEW_SCHEMA))

        invalid_verdict = '{"verdict": "unknown_status", "feedback": "Good job"}'
        self.assertIsNone(llm.parse_and_validate(invalid_verdict, llm.REVIEW_SCHEMA))

        missing_feedback = '{"verdict": "approve"}'
        self.assertIsNone(llm.parse_and_validate(missing_feedback, llm.REVIEW_SCHEMA))

        valid_object = '{"foo": "bar", "count": 42}'
        self.assertIsNotNone(llm.parse_and_validate(valid_object, llm.OBJECT_SCHEMA))

        non_object = '["item1", "item2"]'
        self.assertIsNone(llm.parse_and_validate(non_object, llm.OBJECT_SCHEMA))

        non_json = "plain text with no brackets"
        self.assertIsNone(llm.parse_and_validate(non_json, llm.OBJECT_SCHEMA))

        assert llm.parse_and_validate(valid_review, llm.REVIEW_SCHEMA)["verdict"] == "approve"

    def test_providers_for_whitespace_fallback_to_orchestrator(self):
        """Test whitespace-only PLAN_PROVIDERS correctly falls back to ORCHESTRATOR_PROVIDER."""
        ctx.C.clear()
        ctx.C["PLAN_PROVIDERS"] = "   "
        ctx.C["ORCHESTRATOR_PROVIDER"] = "openai-api"
        provs = llm.providers_for("plan")
        self.assertEqual(provs, ["openai-api"])
        assert provs == ["openai-api"]

    def test_provider_fallback_when_model_unconfigured(self):
        """Test fallback to subsequent provider when primary provider model is unconfigured."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api, gemini-api"
        ctx.C["OPENAI_MODEL"] = ""
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"

        gemini_result = {
            "text": '{"tasks": [], "done": true}',
            "provider": "gemini-api",
            "model": "gemini-1.5-pro",
            "duration_s": 0.2,
            "usage": {},
            "is_error": False,
            "error": None,
        }

        with mock.patch("providers.call_gemini_api", return_value=gemini_result) as mock_gemini:
            text, who = llm.llm("plan", "Plan prompt")

        self.assertEqual(who, "gemini-api:gemini-1.5-pro")
        self.assertTrue(mock_gemini.called)
        assert who == "gemini-api:gemini-1.5-pro"

    def test_review_schema_validation_with_mutations(self):
        """Test REVIEW_SCHEMA validates correctly with structured mutation diff proposals."""
        review_with_mutations = {
            "verdict": "approve",
            "feedback": "LGTM with minor cleanup",
            "lesson": "keep tests offline",
            "mutations": [
                {
                    "name": "cleanup",
                    "file": "bin/lib/foreman/llm.py",
                    "old": "pass",
                    "new": "return True",
                }
            ],
        }
        raw_text = json.dumps(review_with_mutations)
        parsed = llm.parse_and_validate(raw_text, llm.REVIEW_SCHEMA)
        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed.get("mutations", [])), 1)
        assert parsed["verdict"] == "approve"

    def test_llm_available_openai_and_gemini(self):
        """Test llm_available recognizes openai-api and gemini-api as valid providers."""
        ctx.C.clear()
        ctx.C["PLAN_PROVIDERS"] = "openai-api"
        self.assertTrue(llm.llm_available())
        assert llm.llm_available() is True

        ctx.C.clear()
        ctx.C["REVIEW_PROVIDERS"] = "gemini-api"
        self.assertTrue(llm.llm_available())
        assert llm.llm_available() is True

    def test_providers_for_whitespace_orchestrator_falls_back_to_env(self):
        """Test whitespace ORCHESTRATOR_PROVIDER in ctx.C falls back to os.environ."""
        ctx.C.clear()
        ctx.C["PLAN_PROVIDERS"] = ""
        ctx.C["ORCHESTRATOR_PROVIDER"] = "   "
        os.environ["ORCHESTRATOR_PROVIDER"] = "gemini-api"
        provs = llm.providers_for("plan")
        self.assertEqual(provs, ["gemini-api"])
        assert provs == ["gemini-api"]

    def test_provider_fallback_on_empty_text_response(self):
        """Test provider returning empty text or whitespace triggers fallback to next provider."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api, gemini-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"

        openai_empty = {
            "text": "   ",
            "provider": "openai-api",
            "model": "gpt-4o",
            "duration_s": 0.1,
            "usage": {},
            "is_error": False,
            "error": None,
        }
        gemini_ok = {
            "text": '{"tasks": [], "done": true}',
            "provider": "gemini-api",
            "model": "gemini-1.5-pro",
            "duration_s": 0.2,
            "usage": {},
            "is_error": False,
            "error": None,
        }

        with mock.patch("providers.call_openai_api", return_value=openai_empty):
            with mock.patch("providers.call_gemini_api", return_value=gemini_ok):
                text, who = llm.llm("plan", "Plan prompt")

        self.assertEqual(who, "gemini-api:gemini-1.5-pro")
        assert who == "gemini-api:gemini-1.5-pro"

    def test_validate_schema_malformed_schema_and_types(self):
        """Test validate_schema safely handles malformed schema JSON strings and non-dict inputs."""
        self.assertFalse(llm.validate_schema({"a": 1}, "{invalid json"))
        self.assertFalse(llm.validate_schema("not a dict", "{}"))
        self.assertFalse(llm.validate_schema({"a": 1}, 12345))
        assert llm.validate_schema({"a": 1}, "{invalid json") is False

    def test_json_extraction_markdown_fence_plan_schema(self):
        """Test markdown code fenced JSON extraction for plan schema (OBJECT_SCHEMA)."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"

        fenced_plan = (
            "Here is the planned task list:\n```json\n"
            '{"tasks": [{"id": "0001-setup", "title": "Initial setup"}], "done": false}\n'
            "```\nAll done."
        )
        mock_result = {
            "text": fenced_plan,
            "provider": "openai-api",
            "model": "gpt-4o",
            "duration_s": 0.3,
            "usage": {},
            "is_error": False,
            "error": None,
        }
        with mock.patch("providers.call_openai_api", return_value=mock_result):
            text, who = llm.llm("plan", "Plan goal")

        self.assertEqual(who, "openai-api:gpt-4o")
        parsed = util.extract_json(text)
        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed.get("tasks", [])), 1)
        assert len(parsed["tasks"]) == 1

    def test_json_extraction_prose_surrounded_review_schema(self):
        """Test conversational prose surrounding review schema (REVIEW_SCHEMA)."""
        ctx.C["REVIEW_PROVIDERS"] = "gemini-api"
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"

        prose_review = (
            "I reviewed the pull request changes in detail.\n"
            '{"verdict": "approve", "feedback": "All acceptance criteria verified", "lesson": "Keep tests clean"}\n'
            "Looks ready to merge."
        )
        mock_result = {
            "text": prose_review,
            "provider": "gemini-api",
            "model": "gemini-1.5-pro",
            "duration_s": 0.25,
            "usage": {},
            "is_error": False,
            "error": None,
        }
        with mock.patch("providers.call_gemini_api", return_value=mock_result):
            text, who = llm.llm("review", "Review diff")

        self.assertEqual(who, "gemini-api:gemini-1.5-pro")
        parsed = util.extract_json(text)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.get("verdict"), "approve")
        assert parsed["verdict"] == "approve"

    def test_dispatch_openai_default_api_base(self):
        """Test openai-api passes default api_base when OPENAI_API_BASE is not configured."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"
        ctx.C.pop("OPENAI_API_BASE", None)

        mock_result = {
            "text": '{"tasks": []}',
            "provider": "openai-api",
            "model": "gpt-4o",
            "duration_s": 0.2,
            "usage": {},
            "is_error": False,
            "error": None,
        }
        with mock.patch("providers.call_openai_api", return_value=mock_result) as mock_call:
            text, who = llm.llm("plan", "Plan goal")

        self.assertEqual(who, "openai-api:gpt-4o")
        self.assertTrue(mock_call.called)
        call_kwargs = mock_call.call_args.kwargs
        self.assertEqual(call_kwargs.get("api_base"), "https://api.openai.com/v1")
        assert who == "openai-api:gpt-4o"

    def test_provider_multi_step_fallback_chain(self):
        """Test multi-step fallback through 3 providers: openai error -> gemini error -> anthropic success."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api, gemini-api, anthropic-api"
        ctx.C["OPENAI_MODEL"] = "gpt-4o"
        ctx.C["GEMINI_MODEL"] = "gemini-1.5-pro"
        ctx.C["ANTHROPIC_MODEL"] = "claude-3-5-sonnet"

        anthropic_ok = {
            "text": '{"tasks": [], "done": true}',
            "provider": "anthropic-api",
            "model": "claude-3-5-sonnet",
            "duration_s": 0.3,
            "usage": {},
            "is_error": False,
            "error": None,
        }
        with mock.patch("providers.call_openai_api", side_effect=OSError("Network down")):
            with mock.patch("providers.call_gemini_api", return_value={"text": "", "is_error": True, "error": "rate limit"}):
                with mock.patch("providers.call_anthropic_api", return_value=anthropic_ok) as mock_anthropic:
                    text, who = llm.llm("plan", "Plan goal")

        self.assertEqual(who, "anthropic-api:claude-3-5-sonnet")
        self.assertTrue(mock_anthropic.called)
        assert who == "anthropic-api:claude-3-5-sonnet"

    def test_providers_for_other_decision_kinds(self):
        """Test non-review decision kinds (ask, check, roadmap) resolve from PLAN_PROVIDERS."""
        ctx.C["PLAN_PROVIDERS"] = "openai-api"
        ctx.C["REVIEW_PROVIDERS"] = "gemini-api"

        provs_ask = llm.providers_for("ask")
        provs_check = llm.providers_for("check")
        provs_roadmap = llm.providers_for("roadmap")

        self.assertEqual(provs_ask, ["openai-api"])
        self.assertEqual(provs_check, ["openai-api"])
        self.assertEqual(provs_roadmap, ["openai-api"])
        assert provs_ask == ["openai-api"]

    def test_parse_and_validate_edge_cases(self):
        """Test parse_and_validate with None, empty strings, and whitespace."""
        self.assertIsNone(llm.parse_and_validate(None, llm.OBJECT_SCHEMA))
        self.assertIsNone(llm.parse_and_validate("", llm.OBJECT_SCHEMA))
        self.assertIsNone(llm.parse_and_validate("   ", llm.OBJECT_SCHEMA))
        self.assertIsNone(llm.parse_and_validate(None, None))
        self.assertIsNone(llm.parse_and_validate("   ", None))
        self.assertEqual(llm.parse_and_validate("plain text response", None), "plain text response")
        assert llm.parse_and_validate(None, llm.OBJECT_SCHEMA) is None


if __name__ == "__main__":
    unittest.main()
