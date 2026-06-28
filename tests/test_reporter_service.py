from __future__ import annotations

import unittest

from research_system.agents.reporter.service import ReporterService
from research_system.runtime.provider_registry import LLMProviderError


class ReporterProviderStub:
    available = False


class ReporterLLMSuccessStub:
    available = True

    def parse(self, **kwargs):
        class Parsed:
            conclusion = "posterior review completed"
            evidence_ids = ["review-direction", "review-catalyst", "review-window"]
            risk = "use review findings as primary source"
            follow_up = "inspect rejected claims if any"
            degraded_note = None

        return Parsed()


class ReporterLLMErrorStub:
    available = True

    def parse(self, **kwargs):
        raise LLMProviderError("simulated llm error")


class ReporterServiceTests(unittest.TestCase):
    def test_distillation_fallback_report_includes_attribution_digest(self) -> None:
        service = ReporterService(ReporterProviderStub())

        payload = service.run(
            intent="distill_kol_logic",
            query="distill attribution report",
            retrieval_summary="Distillation scorecard",
            evidences=[
                {
                    "evidence_id": "scorecard-1",
                    "title": "distillation_scorecard",
                    "summary": "distillation_scorecard\n{'setup_label': 'mixed_attribution', 'author_ratio': 0.33, 'review_strength': 'high'}",
                    "metadata": {"source_type": "distillation_scorecard"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "logic-author",
                    "title": "logic_block:claim",
                    "summary": "logic_block type=claim source_record_id=src-1 speaker_type=author\ntext: author claim",
                    "metadata": {"source_type": "logic_block"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "logic-reply",
                    "title": "logic_block:claim",
                    "summary": "logic_block type=claim source_record_id=src-2 speaker_type=reply\ntext: reply claim",
                    "metadata": {"source_type": "logic_block"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "logic-repost",
                    "title": "logic_block:claim",
                    "summary": "logic_block type=claim source_record_id=src-3 speaker_type=repost\ntext: repost claim",
                    "metadata": {"source_type": "logic_block"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "review-1",
                    "title": "review_finding:quoted_attribution",
                    "summary": "review finding",
                    "metadata": {"source_type": "review_finding"},
                    "source_refs": [],
                },
            ],
        )

        self.assertIn("Scorecard:", payload["conclusion"])
        self.assertIn("mixed_attribution", payload["conclusion"])
        self.assertIn("author_ratio", payload["conclusion"])
        self.assertIsInstance(payload["scorecard"], dict)
        self.assertEqual(payload["scorecard"]["setup_label"], "mixed_attribution")
        self.assertIn("author-original versus reply/repost/reported content", payload["follow_up"])

    def test_review_thesis_fallback_report_counts_confirmed_findings(self) -> None:
        service = ReporterService(ReporterProviderStub())

        payload = service.run(
            intent="review_thesis",
            query="review thesis",
            retrieval_summary="Posterior review: review_findings=3; claim_type_counts={'direction': 1, 'catalyst': 1, 'time_window': 1}",
            evidences=[
                {
                    "evidence_id": "review-direction",
                    "title": "review_finding",
                    "summary": "review_finding claim_type=direction verdict=confirmed\nbenchmark=HS300 scope=sector\nexpected_direction=underperform\nexcess_metric=-0.82",
                    "metadata": {"source_type": "review_finding"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "review-catalyst",
                    "title": "review_finding",
                    "summary": "review_finding claim_type=catalyst verdict=confirmed\nbenchmark=HS300 scope=sector\nnotes: policy_signal_count=1",
                    "metadata": {"source_type": "review_finding"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "review-window",
                    "title": "review_finding",
                    "summary": "review_finding claim_type=time_window verdict=confirmed\nbenchmark=HS300 scope=sector\nnotes: theme_news_count=1",
                    "metadata": {"source_type": "review_finding"},
                    "source_refs": [],
                },
            ],
        )

        self.assertIn("confirmed=3", payload["conclusion"])
        self.assertIn("claim_type_distribution={'direction': {'confirmed': 1, 'rejected': 0, 'inconclusive': 0}", payload["conclusion"])
        self.assertEqual(
            payload["review_summary"]["verdict_by_claim_type"],
            {
                "direction": {"confirmed": 1, "rejected": 0, "inconclusive": 0},
                "catalyst": {"confirmed": 1, "rejected": 0, "inconclusive": 0},
                "time_window": {"confirmed": 1, "rejected": 0, "inconclusive": 0},
            },
        )
        self.assertFalse(payload["review_summary"]["review_required"])
        self.assertEqual(payload["review_summary"]["reviewable_claim_count"], 3)
        self.assertEqual(
            payload["review_summary"]["claim_type_counts"],
            {"direction": 1, "catalyst": 1, "time_window": 1},
        )
        self.assertEqual(
            payload["review_summary"]["benchmark_scope_counts"],
            {"sector": 3},
        )
        self.assertEqual(
            payload["review_summary"]["benchmark_scope_explanations"],
            {"sector": "按当前 review policy 的默认 benchmark 口径复核。"},
        )
        self.assertEqual(payload["review_summary"]["overall_verdict"], "confirmed")
        self.assertEqual(
            payload["review_summary"]["summary_text"],
            "overall_verdict=confirmed; findings=3; confirmed=3; rejected=0; inconclusive=0; benchmark_scopes=sector",
        )
        self.assertEqual(payload["degraded_note"], "llm_unavailable")

    def test_review_thesis_llm_success_still_returns_review_summary(self) -> None:
        service = ReporterService(ReporterLLMSuccessStub())

        payload = service.run(
            intent="review_thesis",
            query="review thesis",
            retrieval_summary="Posterior review summary",
            evidences=[
                {
                    "evidence_id": "review-direction",
                    "title": "review_finding",
                    "summary": "review_finding claim_type=direction verdict=confirmed\nbenchmark=HS300 scope=sector",
                    "metadata": {"source_type": "review_finding"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "review-catalyst",
                    "title": "review_finding",
                    "summary": "review_finding claim_type=catalyst verdict=rejected\nbenchmark=HS300 scope=sector",
                    "metadata": {"source_type": "review_finding"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "review-window",
                    "title": "review_finding",
                    "summary": "review_finding claim_type=time_window verdict=inconclusive\nbenchmark=HS300 scope=sector",
                    "metadata": {"source_type": "review_finding"},
                    "source_refs": [],
                },
            ],
        )

        self.assertEqual(payload["conclusion"], "overall_verdict=mixed. posterior review completed")
        self.assertEqual(
            payload["review_summary"],
            {
                "review_required": False,
                "finding_count": 3,
                "reviewable_claim_count": 3,
                "claim_type_counts": {
                    "direction": 1,
                    "catalyst": 1,
                    "time_window": 1,
                },
                "benchmark_scope_counts": {
                    "sector": 3,
                },
                "benchmark_scope_explanations": {
                    "sector": "按当前 review policy 的默认 benchmark 口径复核。",
                },
                "overall_verdict": "mixed",
                "summary_text": "overall_verdict=mixed; findings=3; confirmed=1; rejected=1; inconclusive=1; benchmark_scopes=sector",
                "confirmed": 1,
                "rejected": 1,
                "inconclusive": 1,
                "verdict_by_claim_type": {
                    "direction": {"confirmed": 1, "rejected": 0, "inconclusive": 0},
                    "catalyst": {"confirmed": 0, "rejected": 1, "inconclusive": 0},
                    "time_window": {"confirmed": 0, "rejected": 0, "inconclusive": 1},
                },
                "notes": [],
            },
        )
        self.assertIsNone(payload["degraded_note"])

    def test_review_thesis_llm_error_still_returns_review_summary(self) -> None:
        service = ReporterService(ReporterLLMErrorStub())

        payload = service.run(
            intent="review_thesis",
            query="review thesis",
            retrieval_summary="Posterior review summary",
            evidences=[
                {
                    "evidence_id": "review-direction",
                    "title": "review_finding",
                    "summary": "review_finding claim_type=direction verdict=confirmed\nbenchmark=HS300 scope=sector",
                    "metadata": {"source_type": "review_finding"},
                    "source_refs": [],
                }
            ],
        )

        self.assertEqual(payload["review_summary"]["finding_count"], 1)
        self.assertEqual(payload["review_summary"]["confirmed"], 1)
        self.assertEqual(payload["review_summary"]["overall_verdict"], "confirmed")
        self.assertTrue(str(payload["degraded_note"]).startswith("llm_error:"))


if __name__ == "__main__":
    unittest.main()
