from __future__ import annotations

import unittest

from research_system.agents.retriever.service import RetrieverService


class RetrieverServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = object.__new__(RetrieverService)

    def test_dedupe_prefers_current_run_sector_snapshot(self) -> None:
        current_run_id = "01CURRENTRUN"
        historical_item = {
            "evidence_id": "old-snapshot",
            "title": "sector_snapshot:创新药",
            "summary": "sector snapshot for 创新药\nsnapshot_date: 2026-06-27\nlatest_hist: {'pct_change': -2.0}",
            "source_refs": [{"type": "skill", "skill": "sector_snapshot_skill"}],
            "metadata": {"source_type": "sector_snapshot"},
            "score": 0.98,
        }
        current_item = {
            "evidence_id": "current-snapshot",
            "title": "sector",
            "summary": "sector snapshot for 创新药\nsnapshot_date: 2026-06-28\nsignal_scorecard: {'setup_label': 'weak_tape_but_catalysts_alive'}",
            "retrieval_hint": {
                "hint_type": "sector_signal_scorecard",
                "sector": "创新药",
                "setup_label": "weak_tape_but_catalysts_alive",
            },
            "source_refs": [
                {
                    "type": "skill",
                    "skill": "sector_snapshot_skill",
                    "run_id": current_run_id,
                }
            ],
            "metadata": {"source_type": "sector_snapshot"},
            "score": 1.0,
        }

        deduped = self.service._dedupe_evidences(
            evidences=[historical_item, current_item],
            current_run_id=current_run_id,
        )

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["evidence_id"], "current-snapshot")

    def test_structured_retrieval_summary_uses_signal_scorecard_fields(self) -> None:
        evidence = {
            "evidence_id": "sector-snapshot",
            "title": "sector",
            "summary": "sector snapshot for 创新药\nsnapshot_date: 2026-06-28",
            "retrieval_hint": {
                "hint_type": "sector_signal_scorecard",
                "sector": "创新药",
                "setup_label": "weak_tape_but_catalysts_alive",
                "market_tone": "weak",
                "benchmark_view": "neutral",
                "turnover_state": "normal",
                "breadth_state": "broad_weak",
                "policy_signal_strength": "strong",
                "regulatory_signal_strength": "strong",
                "latest_pct_change": -3.85,
                "latest_excess_pct_change": -0.82,
                "up_ratio": 0.0647,
                "median_pct_change": -4.04,
                "average_turnover_rate": 2.5609,
                "top_policy_title": "创新药出海下一程怎么走",
                "top_regulatory_title": "APH03867片获得临床试验受理",
            },
            "source_refs": [],
            "metadata": {"source_type": "sector_snapshot"},
        }

        summary = self.service._structured_retrieval_summary(
            intent="sector_research",
            evidences=[evidence],
        )

        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertIn("sector=创新药", summary)
        self.assertIn("setup=weak_tape_but_catalysts_alive", summary)
        self.assertIn("breadth_state=broad_weak", summary)
        self.assertIn("policy_signal_strength=strong", summary)
        self.assertIn("top_regulatory_signal=APH03867片获得临床试验受理", summary)

    def test_dedupe_prefers_current_run_market_snapshot(self) -> None:
        current_run_id = "01MARKETRUN"
        historical_item = {
            "evidence_id": "old-market",
            "title": "market_snapshot:000001",
            "summary": "market snapshot for 000001\nsnapshot_date: 2026-06-27\nlatest_bar: {'close': 12.1}",
            "source_refs": [{"type": "skill", "skill": "market_snapshot_skill"}],
            "metadata": {"source_type": "market_snapshot"},
            "score": 0.97,
        }
        current_item = {
            "evidence_id": "current-market",
            "title": "market",
            "summary": "market snapshot for 000001\nsnapshot_date: 2026-06-28\nlatest_bar: {'close': 12.34}",
            "source_refs": [
                {
                    "type": "skill",
                    "skill": "market_snapshot_skill",
                    "run_id": current_run_id,
                }
            ],
            "metadata": {"source_type": "market_snapshot"},
            "score": 1.0,
        }

        deduped = self.service._dedupe_evidences(
            evidences=[historical_item, current_item],
            current_run_id=current_run_id,
        )

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["evidence_id"], "current-market")

    def test_distillation_summary_includes_review_findings(self) -> None:
        evidences = [
            {
                "evidence_id": "summary-1",
                "title": "distillation_summary",
                "summary": "summary text",
                "source_refs": [],
                "metadata": {"source_type": "distillation_summary"},
            },
            {
                "evidence_id": "scorecard-1",
                "title": "distillation_scorecard",
                "summary": "distillation_scorecard\n{'setup_label': 'mixed_attribution', 'author_ratio': 0.5, 'review_strength': 'high'}",
                "source_refs": [],
                "metadata": {"source_type": "distillation_scorecard"},
            },
            {
                "evidence_id": "logic-1",
                "title": "logic_block:claim",
                "summary": "logic_block type=claim source_record_id=src-1 speaker_type=reply\ntext: logic summary\nattribution_label: reply_to_other_user",
                "source_refs": [],
                "metadata": {"source_type": "logic_block"},
            },
            {
                "evidence_id": "dimension-1",
                "title": "distilled_dimension:internet",
                "summary": "dimension summary",
                "source_refs": [],
                "metadata": {"source_type": "distilled_dimension"},
            },
            {
                "evidence_id": "review-1",
                "title": "review_finding:fallback_distillation",
                "summary": "review_finding type=fallback_distillation severity=high verdict=needs_review\nreason: fallback path used",
                "source_refs": [],
                "metadata": {"source_type": "review_finding"},
            },
        ]

        summary = self.service._structured_retrieval_summary(
            intent="distill_kol_logic",
            evidences=evidences,
        )
        selected = self.service._select_distillation_evidences(
            evidences=evidences,
            top_k=5,
        )

        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertIn("speaker_type_counts={'reply': 1}", summary)
        self.assertIn("scorecard={'setup_label': 'mixed_attribution', 'author_ratio': 0.5, 'review_strength': 'high'}", summary)
        self.assertIn("review_findings=1", summary)
        self.assertIn("review_types=['fallback_distillation']", summary)
        selected_types = [item["metadata"]["source_type"] for item in selected]
        self.assertIn("review_finding", selected_types)
        self.assertIn("distilled_dimension", selected_types)
        self.assertIn("distillation_scorecard", selected_types)


if __name__ == "__main__":
    unittest.main()
