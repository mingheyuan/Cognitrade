from __future__ import annotations

import unittest

from research_system.agents.reviewer.service import ReviewerService


class ReviewerServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ReviewerService({"benchmark_resolution": "sector", "default_broad_index": "HS300"})
        self.snapshot = {
            "snapshot_type": "sector",
            "sector": "创新药",
            "snapshot_date": "2026-06-28",
            "facts": {
                "latest_hist": {"pct_change": -3.85},
                "benchmark": {
                    "benchmark_code": "000300",
                    "benchmark_name": "HS300",
                    "latest_hist": {"pct_change": -3.03},
                },
                "relative_strength": {"latest_excess_pct_change": -0.82},
                "signal_scorecard": {"setup_label": "weak_tape_but_catalysts_alive"},
                "policy_signals": [
                    {"title": "创新药出海政策窗口", "policy_priority": 5},
                ],
                "regulatory_signals": [
                    {"title": "某创新药注册证书获批", "signal_priority": 5},
                ],
                "theme_news": [
                    {"title": "创新药板块继续分化"},
                ],
                "catalyst_digest": {
                    "top_policy_titles": ["创新药出海政策窗口"],
                    "top_regulatory_titles": ["某创新药注册证书获批"],
                },
            },
        }

    def test_reviewer_confirms_underperform_direction_with_negative_excess(self) -> None:
        result = self.service.run(
            source_run_id="01SOURCE",
            query="review thesis for innovation drug",
            source_entries=[
                {
                    "entry_id": "report-1",
                    "kind": "report_fragment",
                    "payload": {
                        "conclusion": "短期偏弱，可能跑输基准。",
                        "risk": "如果催化落空，可能继续弱于市场。",
                    },
                }
            ],
            latest_sector_snapshot=self.snapshot,
        )

        self.assertEqual(result["summary"]["finding_count"], 3)
        self.assertEqual(result["summary"]["reviewable_claim_count"], 3)
        self.assertEqual(result["summary"]["claim_type_counts"]["direction"], 1)
        self.assertEqual(result["summary"]["claim_type_counts"]["catalyst"], 1)
        self.assertEqual(result["summary"]["claim_type_counts"]["time_window"], 1)
        self.assertEqual(result["summary"]["benchmark_scope_counts"], {"sector_vs_hs300": 3})
        self.assertEqual(
            result["summary"]["benchmark_scope_explanations"],
            {"sector_vs_hs300": "板块方向主张按板块相对 HS300 表现复核。"},
        )
        self.assertEqual(
            result["summary"]["verdict_by_claim_type"],
            {
                "direction": {"confirmed": 1, "rejected": 0, "inconclusive": 0},
                "catalyst": {"confirmed": 1, "rejected": 0, "inconclusive": 0},
                "time_window": {"confirmed": 1, "rejected": 0, "inconclusive": 0},
            },
        )
        direction = next(item for item in result["findings"] if item["claim_type"] == "direction")
        self.assertEqual(direction["expected_direction"], "underperform")
        self.assertEqual(direction["verdict"], "confirmed")
        self.assertEqual(direction["benchmark_name"], "HS300")
        self.assertEqual(direction["benchmark_scope"], "sector_vs_hs300")
        self.assertAlmostEqual(direction["excess_metric"], -0.82)

    def test_reviewer_confirms_catalyst_claim_when_policy_and_regulatory_signals_exist(self) -> None:
        result = self.service.run(
            source_run_id="01SOURCE",
            query="review catalyst thesis",
            source_entries=[
                {
                    "entry_id": "logic-1",
                    "kind": "logic_block",
                    "payload": {
                        "block_type": "catalyst",
                        "text": "创新药后续催化仍在，审批和出海信号持续。",
                    },
                }
            ],
            latest_sector_snapshot=self.snapshot,
        )

        self.assertEqual(result["summary"]["finding_count"], 1)
        finding = result["findings"][0]
        self.assertEqual(finding["claim_type"], "catalyst")
        self.assertEqual(finding["verdict"], "confirmed")
        self.assertEqual(finding["window_size"], 10)
        self.assertIn("policy_signal_count=1", finding["notes"])
        self.assertIn("top_regulatory_title=某创新药注册证书获批", finding["notes"])

    def test_reviewer_marks_time_window_claim_with_theme_signals(self) -> None:
        result = self.service.run(
            source_run_id="01SOURCE",
            query="review near-term thesis",
            source_entries=[
                {
                    "entry_id": "logic-2",
                    "kind": "logic_block",
                    "payload": {
                        "block_type": "claim",
                        "text": "短期几日内还有交易窗口，关注创新药主题催化。",
                    },
                }
            ],
            latest_sector_snapshot=self.snapshot,
        )

        self.assertEqual(result["summary"]["finding_count"], 2)
        self.assertEqual(result["summary"]["claim_type_counts"]["catalyst"], 1)
        self.assertEqual(result["summary"]["claim_type_counts"]["time_window"], 1)
        finding = next(item for item in result["findings"] if item["claim_type"] == "time_window")
        self.assertEqual(finding["verdict"], "confirmed")
        self.assertEqual(finding["window_type"], "trading_days")
        self.assertEqual(finding["window_size"], 5)
        self.assertIn("theme_news_count=1", finding["notes"])

    def test_reviewer_extracts_direction_and_time_window_from_logic_claim(self) -> None:
        result = self.service.run(
            source_run_id="01SOURCE",
            query="review logic claim thesis",
            source_entries=[
                {
                    "entry_id": "logic-3",
                    "kind": "logic_block",
                    "payload": {
                        "block_type": "claim",
                        "text": "短期受益于创新药出海，板块相对基准有望偏强。",
                        "direction": "bullish",
                        "time_horizon": "短期",
                    },
                }
            ],
            latest_sector_snapshot=self.snapshot,
            latest_market_snapshot={
                "snapshot_type": "market",
                "symbol": "000001",
                "snapshot_date": "2026-06-28",
                "facts": {"latest_bar": {"pct_change": -1.25}},
            },
        )

        self.assertEqual(result["summary"]["reviewable_claim_count"], 3)
        self.assertEqual(result["summary"]["claim_type_counts"]["direction"], 1)
        self.assertEqual(result["summary"]["claim_type_counts"]["time_window"], 1)
        self.assertEqual(result["summary"]["claim_type_counts"]["catalyst"], 1)
        self.assertEqual(result["summary"]["benchmark_scope_counts"], {"stock_vs_sector": 3})
        self.assertEqual(
            result["summary"]["benchmark_scope_explanations"],
            {"stock_vs_sector": "个股方向主张按个股相对所属板块表现复核。"},
        )
        direction = next(item for item in result["findings"] if item["claim_type"] == "direction")
        self.assertEqual(direction["expected_direction"], "outperform")
        self.assertEqual(direction["verdict"], "rejected")
        self.assertEqual(direction["benchmark_scope"], "stock_vs_sector")
        self.assertEqual(direction["benchmark_name"], "创新药")

    def test_reviewer_uses_stock_absolute_scope_when_only_market_snapshot_exists(self) -> None:
        result = self.service.run(
            source_run_id="01SOURCE",
            query="review stock thesis",
            source_entries=[
                {
                    "entry_id": "logic-4",
                    "kind": "logic_block",
                    "payload": {
                        "block_type": "claim",
                        "text": "短期走势仍偏弱。",
                        "direction": "bearish",
                    },
                }
            ],
            latest_sector_snapshot=None,
            latest_market_snapshot={
                "snapshot_type": "market",
                "symbol": "000001",
                "snapshot_date": "2026-06-28",
                "facts": {"latest_bar": {"pct_change": -1.25}},
            },
            latest_fundamental_snapshot=None,
            latest_notice_snapshot=None,
        )

        self.assertTrue(result["summary"]["review_required"])
        self.assertIn("missing_sector_snapshot", result["summary"]["notes"])

    def test_reviewer_extracts_more_reviewable_claims_from_distillation_scorecard(self) -> None:
        result = self.service.run(
            source_run_id="01SOURCE",
            query="review scorecard thesis",
            source_entries=[
                {
                    "entry_id": "score-1",
                    "kind": "source_record",
                    "payload": {
                        "title": "distillation_scorecard",
                        "source_type": "distillation_scorecard",
                        "scorecard": {
                            "setup_label": "momentum_expansion",
                            "review_strength": "high",
                            "attribution_risk": "mixed",
                        },
                    },
                }
            ],
            latest_sector_snapshot=self.snapshot,
        )

        self.assertEqual(result["summary"]["reviewable_claim_count"], 2)
        self.assertEqual(result["summary"]["claim_type_counts"]["direction"], 1)
        self.assertEqual(result["summary"]["claim_type_counts"]["time_window"], 1)
        self.assertEqual(result["summary"]["finding_count"], 2)
        direction = next(item for item in result["findings"] if item["claim_type"] == "direction")
        self.assertEqual(direction["expected_direction"], "outperform")

    def test_reviewer_marks_missing_snapshot_as_review_required(self) -> None:
        result = self.service.run(
            source_run_id="01SOURCE",
            query="review thesis",
            source_entries=[
                {
                    "entry_id": "report-1",
                    "kind": "report_fragment",
                    "payload": {
                        "conclusion": "相对基准可能偏强。",
                        "risk": "",
                    },
                }
            ],
            latest_sector_snapshot=None,
        )

        self.assertTrue(result["summary"]["review_required"])
        self.assertEqual(result["summary"]["finding_count"], 0)
        self.assertIn("missing_sector_snapshot", result["summary"]["notes"])


if __name__ == "__main__":
    unittest.main()
