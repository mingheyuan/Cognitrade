from __future__ import annotations

import unittest

from research_system.runtime.presentation.display import build_display_summary


class DisplaySummaryTests(unittest.TestCase):
    def test_prefers_review_summary_text_for_review_thesis(self) -> None:
        payload = build_display_summary(
            result_payload={
                "intent": "review_thesis",
                "report": {
                    "conclusion": "overall_verdict=confirmed. posterior review completed",
                    "review_summary": {
                        "summary_text": "overall_verdict=confirmed; findings=3; confirmed=3; rejected=0; inconclusive=0; benchmark_scopes=sector_vs_hs300",
                    },
                },
            },
            summary_payload={"status": "completed", "message": "Run completed successfully"},
        )

        self.assertEqual(
            payload,
            "overall_verdict=confirmed; findings=3; confirmed=3; rejected=0; inconclusive=0; benchmark_scopes=sector_vs_hs300",
        )

    def test_falls_back_to_conclusion_when_review_summary_missing(self) -> None:
        payload = build_display_summary(
            result_payload={
                "intent": "stock_check",
                "report": {
                    "conclusion": "A concise stock conclusion.",
                },
            },
            summary_payload={"status": "completed", "message": "Run completed successfully"},
        )

        self.assertEqual(payload, "A concise stock conclusion.")


if __name__ == "__main__":
    unittest.main()
