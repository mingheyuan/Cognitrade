from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.agents.critic.service import CriticService
from research_system.runtime.history.session.session_history import SessionHistory
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout


class CriticHybridProviderStub:
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
                        "critic_strategy_resolver",
                        "summary_risk_critic",
                        "critic_summary_builder",
                    ],
                    "request_replan": True,
                    "reason": "prefer summary-risk-only critic pass",
                    "confidence": 0.91,
                    "notes": ["hybrid_select_critic_summary_only"],
                }
            )
        raise AssertionError(f"Unexpected schema for critic hybrid stub: {schema.__name__}")


class CriticServiceTests(unittest.TestCase):
    def test_critic_flags_fallback_and_low_confidence_content(self) -> None:
        service = CriticService()

        result = service.run(
            distillation_summary={
                "major_risks": ["fallback_distillation"],
                "processing_notes": ["records=3"],
            },
            logic_blocks=[
                {
                    "block_id": "block-1",
                    "block_type": "claim",
                    "normalized_claim": "innovation drug export improves growth",
                    "confidence": 0.55,
                    "risk_flags": ["quoted_content_present", "low_information_density"],
                },
                {
                    "block_id": "block-2",
                    "block_type": "claim",
                    "normalized_claim": "innovation drug export improves growth",
                    "confidence": 0.52,
                    "risk_flags": [],
                },
            ],
            distilled_dimensions=[
                {
                    "dimension_id": "dim-1",
                    "dimension_name": "export",
                    "confidence": 0.5,
                    "supported_claim_block_ids": [],
                    "evidence_block_ids": ["block-1"],
                }
            ],
        )

        summary = result["summary"]
        findings = result["findings"]
        self.assertTrue(summary["review_required"])
        self.assertGreaterEqual(summary["finding_count"], 4)
        finding_types = {item["finding_type"] for item in findings}
        self.assertIn("fallback_distillation", finding_types)
        self.assertIn("low_confidence", finding_types)
        self.assertIn("quoted_attribution", finding_types)
        self.assertIn("duplicate_claim_cluster", finding_types)
        self.assertIn("scorecard", result)
        self.assertIn("speaker_type_counts", result["scorecard"])
        subagent_plans = list(result.get("subagent_plans") or [])
        self.assertEqual(len(subagent_plans), 1)
        review_node_types = {
            str(node.get("node_id") or ""): str(node.get("subagent_type") or "")
            for node in list(subagent_plans[0].get("nodes") or [])
        }
        self.assertEqual(review_node_types.get("critic_strategy_resolver"), "critic_strategy_resolver")
        self.assertEqual(review_node_types.get("summary_risk_critic"), "summary_risk_critic")
        self.assertEqual(review_node_types.get("logic_block_critic"), "logic_block_critic")
        self.assertEqual(review_node_types.get("dimension_critic"), "dimension_critic")
        self.assertEqual(review_node_types.get("critic_summary_builder"), "critic_summary_builder")
        patch_op_types = [
            str(operation.get("op_type") or "")
            for patch in list(result.get("subagent_plan_patches") or [])
            for operation in list((patch or {}).get("operations") or [])
        ]
        self.assertIn("request_replan", patch_op_types)
        self.assertTrue(any(item.get("subagent_type") == "critic_summary_builder" for item in list(result.get("subagent_trace") or [])))

    def test_critic_differentiates_reply_repost_and_reported_attribution(self) -> None:
        service = CriticService()

        result = service.run(
            distillation_summary={
                "major_risks": [],
                "processing_notes": [],
            },
            logic_blocks=[
                {
                    "block_id": "reply-1",
                    "block_type": "claim",
                    "normalized_claim": "reply claim",
                    "speaker_type": "reply",
                    "attribution_label": "reply_to_other_user",
                    "confidence": 0.7,
                    "risk_flags": ["quoted_content_present", "reply_attribution"],
                },
                {
                    "block_id": "repost-1",
                    "block_type": "claim",
                    "normalized_claim": "repost claim",
                    "speaker_type": "repost",
                    "attribution_label": "repost_or_forward",
                    "confidence": 0.7,
                    "risk_flags": ["quoted_content_present", "repost_attribution"],
                },
                {
                    "block_id": "reported-1",
                    "block_type": "claim",
                    "normalized_claim": "reported claim",
                    "speaker_type": "reported",
                    "attribution_label": "reported_speech",
                    "confidence": 0.7,
                    "risk_flags": ["quoted_content_present", "reported_attribution"],
                },
            ],
            distilled_dimensions=[],
        )

        quoted_findings = [
            item for item in result["findings"]
            if item["finding_type"] == "quoted_attribution"
        ]
        by_target = {item["target_entry_id"]: item for item in quoted_findings}
        self.assertEqual(by_target["reply-1"]["severity"], "medium")
        self.assertIn("reply", by_target["reply-1"]["reason"].lower())
        self.assertEqual(by_target["repost-1"]["severity"], "high")
        self.assertIn("repost", by_target["repost-1"]["reason"].lower())
        self.assertEqual(by_target["reported-1"]["severity"], "medium")
        self.assertIn("paraphrase", by_target["reported-1"]["reason"].lower())
        self.assertEqual(result["scorecard"]["speaker_type_counts"]["reply"], 1)
        self.assertEqual(result["scorecard"]["speaker_type_counts"]["repost"], 1)
        self.assertEqual(result["scorecard"]["speaker_type_counts"]["reported"], 1)

    def test_critic_hybrid_can_switch_to_summary_only_variant(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            settings.subagent_phase_shadow_llm_enabled = True
            settings.subagent_phase_hybrid_llm_enabled = True
            settings.subagent_phase_hybrid_llm_min_confidence = 0.8
            settings.max_llm_calls_per_turn = 3
            provider = CriticHybridProviderStub()
            service = CriticService(
                settings.runs_root,
                llm_provider=provider,
                settings=settings,
            )

            result = service.run(
                distillation_summary={
                    "major_risks": ["fallback_distillation"],
                    "processing_notes": ["records=3"],
                },
                logic_blocks=[
                    {
                        "block_id": "block-1",
                        "block_type": "claim",
                        "normalized_claim": "innovation drug export improves growth",
                        "confidence": 0.55,
                        "risk_flags": ["quoted_content_present", "low_information_density"],
                    }
                ],
                distilled_dimensions=[
                    {
                        "dimension_id": "dim-1",
                        "dimension_name": "export",
                        "confidence": 0.5,
                        "supported_claim_block_ids": [],
                        "evidence_block_ids": ["block-1"],
                    }
                ],
            )
            run_root = next(settings.runs_root.iterdir())
            trace_payload = SessionHistory(run_root).trace()

        self.assertEqual(provider.calls, 1)
        review_plan = list(result.get("subagent_plans") or [])[0]
        review_node_ids = [str(node.get("node_id") or "") for node in list(review_plan.get("nodes") or [])]
        self.assertIn("critic_summary_builder", review_node_ids)
        self.assertIn("summary_risk_critic", review_node_ids)
        self.assertNotIn("logic_block_critic", review_node_ids)
        self.assertNotIn("dimension_critic", review_node_ids)
        finding_types = {item["finding_type"] for item in result["findings"]}
        self.assertEqual(finding_types, {"fallback_distillation"})
        hybrid_rows = [
            row
            for row in list(trace_payload.get("dynamic_trace") or [])
            if str(row.get("event") or "") == "subagent_phase_hybrid_applied"
        ]
        self.assertEqual(len(hybrid_rows), 1)
        self.assertEqual(hybrid_rows[0]["applied_plan_variant"], "summary_only")


if __name__ == "__main__":
    unittest.main()
