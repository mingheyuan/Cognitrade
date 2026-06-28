from __future__ import annotations

import unittest

from research_system.schemas.review import ClaimVerdictBreakdown, PosteriorReviewSummary


class ReviewSchemaTests(unittest.TestCase):
    def test_posterior_review_summary_model_dump_is_stable(self) -> None:
        summary = PosteriorReviewSummary(
            review_required=False,
            finding_count=3,
            reviewable_claim_count=3,
            claim_type_counts={
                "direction": 1,
                "catalyst": 1,
                "time_window": 1,
            },
            benchmark_scope_counts={
                "sector_vs_hs300": 3,
            },
            benchmark_scope_explanations={
                "sector_vs_hs300": "板块方向主张按板块相对 HS300 表现复核。",
            },
            verdict_by_claim_type={
                "direction": ClaimVerdictBreakdown(confirmed=1),
                "catalyst": ClaimVerdictBreakdown(rejected=1),
                "time_window": ClaimVerdictBreakdown(inconclusive=1),
            },
            overall_verdict="mixed",
            summary_text="overall_verdict=mixed; findings=3; confirmed=1; rejected=1; inconclusive=1; benchmark_scopes=sector_vs_hs300",
            confirmed=1,
            rejected=1,
            inconclusive=1,
            notes=["sample"],
        )

        payload = summary.model_dump(mode="json")

        self.assertEqual(payload["finding_count"], 3)
        self.assertEqual(payload["reviewable_claim_count"], 3)
        self.assertEqual(payload["confirmed"], 1)
        self.assertEqual(payload["rejected"], 1)
        self.assertEqual(payload["inconclusive"], 1)
        self.assertEqual(payload["benchmark_scope_counts"], {"sector_vs_hs300": 3})
        self.assertEqual(
            payload["benchmark_scope_explanations"],
            {"sector_vs_hs300": "板块方向主张按板块相对 HS300 表现复核。"},
        )
        self.assertEqual(payload["overall_verdict"], "mixed")
        self.assertEqual(
            payload["summary_text"],
            "overall_verdict=mixed; findings=3; confirmed=1; rejected=1; inconclusive=1; benchmark_scopes=sector_vs_hs300",
        )
        self.assertEqual(
            payload["verdict_by_claim_type"],
            {
                "direction": {"confirmed": 1, "rejected": 0, "inconclusive": 0},
                "catalyst": {"confirmed": 0, "rejected": 1, "inconclusive": 0},
                "time_window": {"confirmed": 0, "rejected": 0, "inconclusive": 1},
            },
        )


if __name__ == "__main__":
    unittest.main()
