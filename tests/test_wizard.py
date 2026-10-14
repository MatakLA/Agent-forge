#!/usr/bin/env python3
"""Unit tests for offline recommendation engine (bin/lib/foreman/wizard.py).

Public interface:
- Run directly: python3 tests/test_wizard.py
- Run via unittest: python3 -m unittest tests/test_wizard.py
- Run via pytest: python3 -m pytest -q tests/test_wizard.py

Important invariants:
- wizard.py is stdlib only, offline-safe, and does not modify readiness.py.
- NEXT_STEPS and next_steps(name) cover every runtime and provider without raising KeyError.
- generate_report() reuses readiness.recommend() ranking logic.
- Scenario 1: only Claude Code (claude-cli) ready -> top recommendation is claude-cli.
- Scenario 2: only local-compat runtime (e.g. ollama/vllm) ready -> top recommendation is that runtime, not claude-cli.
- Scenario 3: nothing configured/ready at all -> explicitly states nothing is ready, prints concrete next steps.
"""
import io
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(ROOT, "bin", "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from foreman import readiness, wizard


class TestWizardOfflineRecommendations(unittest.TestCase):
    """Test suite for bin/lib/foreman/wizard.py offline recommendation engine."""

    def test_next_steps_covers_all_runtimes_and_providers(self):
        """Invariant: next_steps() returns a non-empty string for every runtime and provider key."""
        real_runtimes = readiness.runtime_items()
        real_providers = readiness.provider_items()

        # Loop over real runtime items
        for rt in real_runtimes:
            step = wizard.next_steps(rt)
            self.assertIsInstance(step, str)
            self.assertGreater(len(step.strip()), 0, f"next_steps({rt!r}) must be non-empty")
            assert isinstance(step, str) and len(step.strip()) > 0

        # Loop over real provider items
        for pr in real_providers:
            step = wizard.next_steps(pr)
            self.assertIsInstance(step, str)
            self.assertGreater(len(step.strip()), 0, f"next_steps({pr!r}) must be non-empty")
            assert isinstance(step, str) and len(step.strip()) > 0

    def test_next_steps_fallback_and_no_key_error(self):
        """Invariant: Unknown keys fall back to generic message and never raise KeyError."""
        unknown_key = "some-unknown-runtime-or-provider-999"

        # Lookup via function
        msg = wizard.next_steps(unknown_key)
        self.assertIsInstance(msg, str)
        self.assertIn(f"see docs/RUNTIMES.md and docs/PROVIDERS.md for {unknown_key}", msg)
        assert f"see docs/RUNTIMES.md and docs/PROVIDERS.md for {unknown_key}" in msg

        # Lookup via dict dunder getitem - must not raise KeyError
        dict_val = wizard.NEXT_STEPS[unknown_key]
        self.assertEqual(dict_val, msg)
        assert dict_val == msg

        # Dict get method fallback
        self.assertEqual(wizard.NEXT_STEPS.get(unknown_key), msg)
        assert wizard.NEXT_STEPS.get(unknown_key) == msg

    def test_generate_report_scenario_1_claude_cli_only(self):
        """Scenario 1: Only Claude Code (claude-cli) ready/installed, everything else not configured.

        Requirement: report's top recommendation is claude-cli.
        """
        runtimes_st = {
            rt: {
                "ready": False,
                "reason": f"Runtime '{rt}' is not configured.",
                "state": "missing_setting",
                "detail": f"Runtime '{rt}' is not configured.",
                "missing_settings": ["unconfigured"],
                "missing_egress": [],
            }
            for rt in readiness.runtime_items()
        }
        providers_st = {
            pr: {
                "ready": (pr == "claude-cli"),
                "reason": f"Provider '{pr}' is ready." if pr == "claude-cli" else f"Provider '{pr}' is not configured.",
                "state": "ok" if pr == "claude-cli" else "missing_setting",
                "detail": f"Provider '{pr}' is ready." if pr == "claude-cli" else f"Provider '{pr}' is not configured.",
                "missing_settings": [] if pr == "claude-cli" else ["unconfigured"],
                "missing_egress": [],
            }
            for pr in readiness.provider_items()
        }
        synthetic_status = {
            "runtimes": runtimes_st,
            "providers": providers_st,
        }

        report = wizard.generate_report(synthetic_status)
        self.assertIsInstance(report, str)
        self.assertGreater(len(report.strip()), 0)
        assert isinstance(report, str) and len(report.strip()) > 0

        # Invariant: Top recommendation is claude-cli
        self.assertIn("Recommended: claude-cli", report)
        assert "Recommended: claude-cli" in report

        # Invariant: Next steps for claude-cli is printed
        claude_cli_steps = wizard.next_steps("claude-cli")
        self.assertIn(claude_cli_steps, report)
        assert claude_cli_steps in report

        # Invariant: State and cost tiers are surfaced
        self.assertIn("state=", report)
        self.assertIn("cost_tier=", report)
        assert "state=" in report and "cost_tier=" in report

    def test_generate_report_scenario_2_local_compat_only(self):
        """Scenario 2: Only a local-compat server configured and reachable (e.g. ollama or vllm).

        Requirement: report's top recommendation is that runtime, not claude-cli.
        """
        for chosen_local in ("ollama", "vllm"):
            with self.subTest(local_runtime=chosen_local):
                runtimes_st = {
                    rt: {
                        "ready": (rt == chosen_local),
                        "reason": f"Runtime '{rt}' is ready." if rt == chosen_local else f"Runtime '{rt}' is not configured.",
                        "state": "ok" if rt == chosen_local else "missing_setting",
                        "detail": f"Runtime '{rt}' is ready." if rt == chosen_local else f"Runtime '{rt}' is not configured.",
                        "missing_settings": [] if rt == chosen_local else ["unconfigured"],
                        "missing_egress": [],
                    }
                    for rt in readiness.runtime_items()
                }
                providers_st = {
                    pr: {
                        "ready": False,
                        "reason": f"Provider '{pr}' is not configured.",
                        "state": "missing_setting",
                        "detail": f"Provider '{pr}' is not configured.",
                        "missing_settings": ["unconfigured"],
                        "missing_egress": [],
                    }
                    for pr in readiness.provider_items()
                }
                synthetic_status = {
                    "runtimes": runtimes_st,
                    "providers": providers_st,
                }

                report = wizard.generate_report(synthetic_status)
                self.assertIsInstance(report, str)
                assert isinstance(report, str)

                # Invariant: Top recommendation is chosen_local, NOT claude-cli
                self.assertIn(f"Recommended: {chosen_local}", report)
                self.assertNotIn("Recommended: claude-cli", report)
                assert f"Recommended: {chosen_local}" in report
                assert "Recommended: claude-cli" not in report

                # Invariant: Next steps for chosen_local is printed
                local_steps = wizard.next_steps(chosen_local)
                self.assertIn(local_steps, report)
                assert local_steps in report

    def test_generate_report_scenario_3_nothing_configured(self):
        """Scenario 3: Nothing configured/ready at all.

        Requirement: report explicitly states nothing is ready and still prints a concrete
        next_steps() suggestion (no exception, no empty string).
        """
        runtimes_st = {
            rt: {
                "ready": False,
                "reason": f"Runtime '{rt}' is not configured.",
                "state": "missing_setting",
                "detail": f"Runtime '{rt}' is not configured.",
                "missing_settings": ["unconfigured"],
                "missing_egress": [],
            }
            for rt in readiness.runtime_items()
        }
        providers_st = {
            pr: {
                "ready": False,
                "reason": f"Provider '{pr}' is not configured.",
                "state": "missing_setting",
                "detail": f"Provider '{pr}' is not configured.",
                "missing_settings": ["unconfigured"],
                "missing_egress": [],
            }
            for pr in readiness.provider_items()
        }
        synthetic_status = {
            "runtimes": runtimes_st,
            "providers": providers_st,
        }

        report = wizard.generate_report(synthetic_status)
        self.assertIsInstance(report, str)
        self.assertGreater(len(report.strip()), 0)
        assert isinstance(report, str) and len(report.strip()) > 0

        # Invariant: Explicitly states nothing is ready
        self.assertTrue(
            ("No runtime or provider is currently ready" in report)
            or ("No runtime or provider is ready" in report)
            or ("nothing is ready" in report.lower())
            or ("no runtime passed its readiness check" in report.lower())
        )
        assert ("No runtime or provider is currently ready" in report) or ("no runtime" in report.lower())

        # Invariant: Still prints a concrete next_steps() recommendation block
        self.assertIn("Recommended:", report)
        self.assertIn("Next steps:", report)
        assert "Recommended:" in report and "Next steps:" in report

    def test_generate_report_default_none_status(self):
        """Invariant: generate_report(None) calls readiness.all_status() without crashing."""
        report = wizard.generate_report()
        self.assertIsInstance(report, str)
        self.assertGreater(len(report.strip()), 0)
        self.assertIn("=== AI Recommendation Report ===", report)
        self.assertIn("Recommended:", report)
        self.assertIn("Next steps:", report)
        assert isinstance(report, str) and len(report.strip()) > 0

    def test_generate_report_never_raises_for_real_keys(self):
        """Invariant: generate_report() never raises for any single ready runtime or provider key."""
        all_keys = readiness.runtime_items() + readiness.provider_items()
        for target_key in all_keys:
            r_st = {
                rt: {
                    "ready": (rt == target_key),
                    "reason": f"Runtime '{rt}'",
                    "state": "ok" if rt == target_key else "missing_setting",
                    "detail": f"Runtime '{rt}'",
                    "missing_settings": [] if rt == target_key else ["key"],
                    "missing_egress": [],
                }
                for rt in readiness.runtime_items()
            }
            p_st = {
                pr: {
                    "ready": (pr == target_key),
                    "reason": f"Provider '{pr}'",
                    "state": "ok" if pr == target_key else "missing_setting",
                    "detail": f"Provider '{pr}'",
                    "missing_settings": [] if pr == target_key else ["key"],
                    "missing_egress": [],
                }
                for pr in readiness.provider_items()
            }
            st = {"runtimes": r_st, "providers": p_st}
            try:
                rep = wizard.generate_report(st)
                self.assertIsInstance(rep, str)
                self.assertIn(f"Recommended: {target_key}", rep)
                assert f"Recommended: {target_key}" in rep
            except Exception as exc:
                self.fail(f"generate_report() raised {exc!r} for key {target_key!r}")

    def test_main_cli_returns_zero(self):
        """Invariant: main() prints report to stdout and returns 0."""
        with patch("sys.stdout", new=io.StringIO()) as fake_out:
            code = wizard.main([])
            self.assertEqual(code, 0)
            assert code == 0
            output = fake_out.getvalue()
            self.assertIn("=== AI Recommendation Report ===", output)
            self.assertIn("Recommended:", output)
            assert "Recommended:" in output


if __name__ == "__main__":
    unittest.main()
