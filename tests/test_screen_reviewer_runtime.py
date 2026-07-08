from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.agents.screen_reviewer.screen_reviewer_finding_support import ScreenReviewerFindingSupport
from research_system.agents.screen_reviewer.screen_reviewer_strategy_support import ScreenReviewerStrategySupport
from research_system.agents.screen_reviewer.screen_reviewer_summary_support import ScreenReviewerSummarySupport
from research_system.agents.screen_reviewer.service import ScreenReviewerService
from research_system.runtime.history.session.session_history import SessionHistory
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout


class ScreenReviewerHybridProviderStub:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def parse(self, **kwargs):
        schema = kwargs["schema"]
        if schema.__name__ == "SubagentPhaseShadowSuggestionPayload":
            self.calls += 1
            return schema.model_validate(
                {
                    "selected_plan_variant": "summary_only",
                    "selected_node_ids": [
                        "screen_review_strategy_resolver",
                        "screen_review_summary_builder",
                    ],
                    "request_replan": True,
                    "reason": "prefer summary-only screen review for this candidate set",
                    "confidence": 0.9,
                    "notes": ["hybrid_select_screen_review_summary_only"],
                }
            )
        raise AssertionError(f"Unexpected schema for screen reviewer hybrid stub: {schema.__name__}")


class ScreenReviewerRuntimeTests(unittest.TestCase):
    def test_screen_reviewer_service_wires_supports(self) -> None:
        service = ScreenReviewerService()

        self.assertIsInstance(service.strategy_support, ScreenReviewerStrategySupport)
        self.assertIsInstance(service.finding_support, ScreenReviewerFindingSupport)
        self.assertIsInstance(service.summary_support, ScreenReviewerSummarySupport)

    def test_screen_reviewer_hybrid_can_switch_to_summary_only_variant(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            settings.subagent_phase_shadow_llm_enabled = True
            settings.subagent_phase_hybrid_llm_enabled = True
            settings.subagent_phase_hybrid_llm_min_confidence = 0.8
            settings.max_llm_calls_per_turn = 3
            provider = ScreenReviewerHybridProviderStub()
            service = ScreenReviewerService(
                settings.runs_root,
                llm_provider=provider,
                settings=settings,
            )
            result = service.run(
                source_run_id="01SCREEN",
                candidates=[
                    {"symbol": "000001", "name": "PingAn", "score": 2.1, "deep_checked": True},
                    {"symbol": "600519", "name": "Moutai", "score": 1.9, "deep_checked": True},
                ],
                scorecard={"investment_cycle": "mid_term", "candidate_count": 2},
                planning_context_hints={"recommended_next_step": "summarize only"},
            )
            run_root = next(settings.runs_root.iterdir())
            trace_payload = SessionHistory(run_root).trace()

        self.assertEqual(provider.calls, 1)
        review_plan = list(result.get("subagent_plans") or [])[0]
        review_node_ids = [str(node.get("node_id") or "") for node in list(review_plan.get("nodes") or [])]
        self.assertIn("screen_review_summary_builder", review_node_ids)
        self.assertFalse(any(node_id.startswith("screen_review_candidate_") for node_id in review_node_ids))
        self.assertEqual(result["findings"], [])
        hybrid_rows = [
            row
            for row in list(trace_payload.get("dynamic_trace") or [])
            if str(row.get("event") or "") == "subagent_phase_hybrid_applied"
        ]
        self.assertEqual(len(hybrid_rows), 1)
        self.assertEqual(hybrid_rows[0]["applied_plan_variant"], "summary_only")


if __name__ == "__main__":
    unittest.main()
