#!/usr/bin/env python3
"""Tests for per-runtime cost/quota classifier cost_model() in bin/lib/runtimes.py.

Public interface:
- Run directly: python3 tests/test_runtime_cost_model.py
- Run via unittest: python3 -m unittest tests/test_runtime_cost_model.py

Important invariants:
- cost_model(rt) returns a dict with exactly two keys: 'kind' and 'detail'.
- 'kind' is one of {"metered", "quota", "unknown"}.
- 'detail' is a non-empty human-readable explanation.
- opencode, qwen-code, goose, anthropic-compat, copilot, mistral-vibe, ollama,
  vllm, lmstudio, and llamacpp return kind == 'unknown', each naming the runtime.
- agy returns kind == 'quota' (subscription tracked via agy /usage).
- claude, codex, gemini, compat, aider return kind == 'metered' (API token billing).
- The six new runtimes return honest 'unknown' classifications with no fabricated USD.
- Unknown runtimes not in RUNTIMES raise ValueError.
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))

import runtimes


class TestRuntimeCostModel(unittest.TestCase):
    """Unit tests for cost_model() classifier."""

    def test_all_runtimes_covered_and_dict_shape(self):
        """Every key in RUNTIMES must return a dict with exactly {'kind', 'detail'}."""
        self.assertGreater(len(runtimes.RUNTIMES), 0)
        valid_kinds = {"metered", "quota", "unknown"}
        for rt in runtimes.RUNTIMES:
            res = runtimes.cost_model(rt)
            self.assertIsInstance(res, dict, f"cost_model({rt!r}) must return a dict")
            self.assertEqual(
                set(res.keys()),
                {"kind", "detail"},
                f"cost_model({rt!r}) must return exactly keys ('kind', 'detail'), got {set(res.keys())}",
            )
            self.assertIn(
                res["kind"],
                valid_kinds,
                f"cost_model({rt!r}) kind must be in {valid_kinds}, got {res['kind']!r}",
            )
            self.assertIsInstance(res["detail"], str, f"cost_model({rt!r}) detail must be a string")
            self.assertTrue(len(res["detail"].strip()) > 0, f"cost_model({rt!r}) detail must be non-empty")

    def test_exact_kind_for_every_runtime(self):
        """Every RUNTIMES key must assert its exact kind in aggregate."""
        expected_kinds = {
            "agy": "quota",
            "claude": "metered",
            "codex": "metered",
            "gemini": "metered",
            "compat": "metered",
            "aider": "metered",
            "opencode": "unknown",
            "goose": "unknown",
            "qwen-code": "unknown",
            "anthropic-compat": "unknown",
            "copilot": "unknown",
            "mistral-vibe": "unknown",
            "ollama": "unknown",
            "vllm": "unknown",
            "lmstudio": "unknown",
            "llamacpp": "unknown",
        }
        self.assertEqual(set(expected_kinds.keys()), set(runtimes.RUNTIMES.keys()))
        for rt, expected_kind in expected_kinds.items():
            res = runtimes.cost_model(rt)
            self.assertEqual(
                res["kind"],
                expected_kind,
                f"cost_model({rt!r}) expected kind {expected_kind!r}, got {res['kind']!r}",
            )

    # Individual unit tests for each RUNTIMES key asserting its exact kind
    def test_kind_agy(self):
        res = runtimes.cost_model("agy")
        self.assertEqual(res["kind"], "quota")

    def test_kind_claude(self):
        res = runtimes.cost_model("claude")
        self.assertEqual(res["kind"], "metered")

    def test_kind_codex(self):
        res = runtimes.cost_model("codex")
        self.assertEqual(res["kind"], "metered")

    def test_kind_gemini(self):
        res = runtimes.cost_model("gemini")
        self.assertEqual(res["kind"], "metered")

    def test_kind_compat(self):
        res = runtimes.cost_model("compat")
        self.assertEqual(res["kind"], "metered")

    def test_kind_aider(self):
        res = runtimes.cost_model("aider")
        self.assertEqual(res["kind"], "metered")

    def test_kind_opencode(self):
        res = runtimes.cost_model("opencode")
        self.assertEqual(res["kind"], "unknown")

    def test_kind_goose(self):
        res = runtimes.cost_model("goose")
        self.assertEqual(res["kind"], "unknown")

    def test_kind_qwen_code(self):
        res = runtimes.cost_model("qwen-code")
        self.assertEqual(res["kind"], "unknown")

    def test_kind_anthropic_compat(self):
        res = runtimes.cost_model("anthropic-compat")
        self.assertEqual(res["kind"], "unknown")

    def test_kind_copilot(self):
        res = runtimes.cost_model("copilot")
        self.assertEqual(res["kind"], "unknown")

    def test_kind_mistral_vibe(self):
        res = runtimes.cost_model("mistral-vibe")
        self.assertEqual(res["kind"], "unknown")

    def test_kind_ollama(self):
        res = runtimes.cost_model("ollama")
        self.assertEqual(res["kind"], "unknown")

    def test_kind_vllm(self):
        res = runtimes.cost_model("vllm")
        self.assertEqual(res["kind"], "unknown")

    def test_kind_lmstudio(self):
        res = runtimes.cost_model("lmstudio")
        self.assertEqual(res["kind"], "unknown")

    def test_kind_llamacpp(self):
        res = runtimes.cost_model("llamacpp")
        self.assertEqual(res["kind"], "unknown")

    def test_six_new_runtimes_honest_cost_model_classification(self):
        """Assert cost_model() returns honest classification for all six new runtimes.

        Acceptance criteria:
        - cost_model(rt) returns a dict without raising for rt in
          {copilot, mistral-vibe, ollama, vllm, lmstudio, llamacpp}
        - the four self-hosted runtimes' classification contains no fabricated dollar
          price and matches the existing convention used for other local/endpoint
          runtimes in the same function
        - a new test in tests/test_runtime_cost_model.py asserts the above for all six keys
          and fails if any key is missing from the classification logic
        """
        six_keys = {"copilot", "mistral-vibe", "ollama", "vllm", "lmstudio", "llamacpp"}
        self_hosted_keys = {"ollama", "vllm", "lmstudio", "llamacpp"}
        cli_metered_keys = {"copilot", "mistral-vibe"}

        # Must assert against exactly the six specified keys
        self.assertEqual(len(six_keys), 6)
        self.assertTrue(
            six_keys.issubset(set(runtimes.RUNTIMES.keys())),
            f"All six keys must be in RUNTIMES, missing: {six_keys - set(runtimes.RUNTIMES.keys())}",
        )

        for rt in six_keys:
            # 1. cost_model(rt) returns a dict without raising
            res = runtimes.cost_model(rt)
            self.assertIsInstance(res, dict, f"cost_model({rt!r}) must return a dict")
            self.assertEqual(
                set(res.keys()),
                {"kind", "detail"},
                f"cost_model({rt!r}) must return exactly keys ('kind', 'detail')",
            )
            self.assertEqual(res["kind"], "unknown", f"cost_model({rt!r}) kind must be 'unknown'")
            self.assertIsInstance(res["detail"], str)
            self.assertTrue(len(res["detail"].strip()) > 0, f"cost_model({rt!r}) detail must be non-empty")

            # 2. Contains no fabricated dollar price
            self.assertNotIn("$", res["detail"], f"cost_model({rt!r}) detail must not contain '$'")
            self.assertNotRegex(
                res["detail"],
                r"\$\d+\.?\d*",
                f"cost_model({rt!r}) detail fabricated a USD price: {res['detail']!r}",
            )
            self.assertNotRegex(
                res["detail"],
                r"\b0\.00\b",
                f"cost_model({rt!r}) detail fabricated a zero USD price: {res['detail']!r}",
            )

            # 3. Fails if missing from classification logic (cannot fall back to default quota/metered)
            self.assertNotIn(
                "agy /usage",
                res["detail"],
                f"cost_model({rt!r}) must not fall back to agy quota classification",
            )
            self.assertNotIn(
                "billed per token",
                res["detail"],
                f"cost_model({rt!r}) must not fall back to generic metered API classification",
            )
            self.assertNotEqual(
                res["kind"],
                "quota",
                f"cost_model({rt!r}) must not fall back to quota default",
            )
            self.assertNotEqual(
                res["kind"],
                "metered",
                f"cost_model({rt!r}) must not fall back to metered default",
            )

        # 4. Self-hosted runtimes match the local/endpoint convention (e.g. Self-hosted, no per-token charges)
        for rt in self_hosted_keys:
            res = runtimes.cost_model(rt)
            self.assertIn(
                "Self-hosted",
                res["detail"],
                f"Self-hosted runtime {rt!r} detail must mention 'Self-hosted': {res['detail']!r}",
            )
            self.assertIn(
                "no per-token charges",
                res["detail"],
                f"Self-hosted runtime {rt!r} detail must mention 'no per-token charges': {res['detail']!r}",
            )
            # Detail must name the runtime
            clean_name = rt.replace("studio", " studio").replace("cpp", ".cpp")
            self.assertTrue(
                rt in res["detail"].lower() or clean_name in res["detail"].lower(),
                f"Detail for {rt!r} must name the runtime: {res['detail']!r}",
            )

        # 5. CLI metered/subscription runtimes match the subscription/external pricing convention
        for rt in cli_metered_keys:
            res = runtimes.cost_model(rt)
            self.assertIn(
                "subscription",
                res["detail"].lower(),
                f"CLI runtime {rt!r} detail must mention subscription: {res['detail']!r}",
            )
            self.assertIn(
                "not tracked in Agent-Forge",
                res["detail"],
                f"CLI runtime {rt!r} detail must mention 'not tracked in Agent-Forge': {res['detail']!r}",
            )
            target_name = "copilot" if rt == "copilot" else "mistral"
            self.assertIn(
                target_name,
                res["detail"].lower(),
                f"Detail for {rt!r} must name the runtime: {res['detail']!r}",
            )

    def test_unknown_runtimes_distinct_details_and_naming(self):
        """All unknown-kind runtimes return distinct details naming the specific runtime."""
        unknown_rts = [
            "opencode",
            "qwen-code",
            "goose",
            "anthropic-compat",
            "copilot",
            "mistral-vibe",
            "ollama",
            "vllm",
            "lmstudio",
            "llamacpp",
        ]
        details = {}
        for rt in unknown_rts:
            res = runtimes.cost_model(rt)
            self.assertEqual(res["kind"], "unknown")
            detail = res["detail"]
            self.assertNotIn(detail, details.values(), f"Detail for {rt!r} must be distinct from other unknown runtimes")
            details[rt] = detail

        # Each detail string must name that specific runtime
        self.assertIn("opencode", details["opencode"].lower())
        self.assertTrue(
            "qwen-code" in details["qwen-code"].lower() or "qwen code" in details["qwen-code"].lower(),
            f"Detail for 'qwen-code' must name the runtime: {details['qwen-code']!r}",
        )
        self.assertIn("goose", details["goose"].lower())
        self.assertIn("anthropic-compat", details["anthropic-compat"].lower())
        self.assertIn("copilot", details["copilot"].lower())
        self.assertTrue(
            "mistral-vibe" in details["mistral-vibe"].lower() or "mistral vibe" in details["mistral-vibe"].lower(),
            f"Detail for 'mistral-vibe' must name the runtime: {details['mistral-vibe']!r}",
        )
        self.assertIn("ollama", details["ollama"].lower())
        self.assertIn("vllm", details["vllm"].lower())
        self.assertTrue(
            "lmstudio" in details["lmstudio"].lower() or "lm studio" in details["lmstudio"].lower(),
            f"Detail for 'lmstudio' must name the runtime: {details['lmstudio']!r}",
        )
        self.assertTrue(
            "llamacpp" in details["llamacpp"].lower() or "llama.cpp" in details["llamacpp"].lower(),
            f"Detail for 'llamacpp' must name the runtime: {details['llamacpp']!r}",
        )

    def test_quota_runtime_detail(self):
        """agy detail explains tracking via agy /usage."""
        res = runtimes.cost_model("agy")
        self.assertEqual(res["kind"], "quota")
        self.assertIn("agy /usage", res["detail"])

    def test_metered_runtime_detail(self):
        """Metered runtimes explain per-token billing."""
        for rt in ("claude", "codex", "gemini", "compat", "aider"):
            res = runtimes.cost_model(rt)
            self.assertEqual(res["kind"], "metered")
            self.assertTrue(
                "token" in res["detail"].lower() or "money" in res["detail"].lower(),
                f"Detail for {rt!r} should explain per-token billing: {res['detail']!r}",
            )

    def test_result_immutability(self):
        """Mutating a returned dict must not mutate subsequent calls."""
        res1 = runtimes.cost_model("claude")
        res1["kind"] = "mutated"
        res1["extra"] = True
        res2 = runtimes.cost_model("claude")
        self.assertEqual(res2["kind"], "metered")
        self.assertNotIn("extra", res2)

    def test_unknown_runtime_raises_value_error(self):
        """cost_model() raises ValueError for runtime names not in RUNTIMES."""
        with self.assertRaises(ValueError):
            runtimes.cost_model("bogus-runtime")
        with self.assertRaises(ValueError):
            runtimes.cost_model("")
        with self.assertRaises(ValueError):
            runtimes.cost_model(" ")
        with self.assertRaises(ValueError):
            runtimes.cost_model(" agy")
        with self.assertRaises(ValueError):
            runtimes.cost_model(None)
        with self.assertRaises(ValueError):
            runtimes.cost_model("unknown")
        with self.assertRaises(ValueError):
            runtimes.cost_model(123)


if __name__ == "__main__":
    unittest.main()
