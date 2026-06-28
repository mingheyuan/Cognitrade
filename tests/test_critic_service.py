from __future__ import annotations

import unittest

from research_system.agents.critic.service import CriticService


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


if __name__ == "__main__":
    unittest.main()
