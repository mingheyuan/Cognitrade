from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from domain_packs.finance_ashare.subagents.uzi_392_analysis import execute_native_analysis
from tests.test_uzi_392_native_methods import _snapshots


class Uzi392AnalysisGateTests(unittest.TestCase):
    def test_missing_agent_stage_stops_at_critical_gate_without_report_permission(self) -> None:
        result = execute_native_analysis(
            _snapshots(),
            ["600000"],
            profile="lite",
            llm_provider=None,
        )

        self.assertGreater(result["critical_count"], 0)
        self.assertFalse(result["report_allowed"])
        self.assertEqual(result["quality_status"], "quality_gate_blocked")
        self.assertEqual(
            result["recovery_history"][-1]["exhausted_reason"],
            "no_remaining_agent_recovery_action",
        )

    def test_environment_cannot_bypass_native_self_review(self) -> None:
        with patch.dict(os.environ, {"UZI_SKIP_REVIEW": "1"}):
            result = execute_native_analysis(
                _snapshots(),
                ["600000"],
                profile="lite",
                llm_provider=None,
            )

        self.assertGreater(result["critical_count"], 0)
        self.assertFalse(result["report_allowed"])
        self.assertEqual(result["quality_status"], "quality_gate_blocked")

    def test_profile_is_an_explicit_user_choice(self) -> None:
        for profile in ("", "mid", "automatic"):
            with self.subTest(profile=profile):
                with self.assertRaisesRegex(ValueError, "research_profile is required"):
                    execute_native_analysis(
                        _snapshots(),
                        ["600000"],
                        profile=profile,
                        llm_provider=None,
                    )

    def test_plain_and_exchange_suffixed_symbols_are_analyzed_once(self) -> None:
        rows = _snapshots()
        rows[0]["symbol"] = "600000.SH"

        result = execute_native_analysis(
            rows,
            ["600000.SH", "600000"],
            profile="lite",
            llm_provider=None,
        )

        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["raw"]["ticker"], "600000.SH")


if __name__ == "__main__":
    unittest.main()
