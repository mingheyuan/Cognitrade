from __future__ import annotations

import unittest
from unittest.mock import patch

from research_system.adapters.vendors.akshare_fundamental_adapter import AkShareFundamentalAdapter


class AkshareFundamentalAdapterTests(unittest.TestCase):
    def test_individual_info_failure_is_a_gap_not_a_fake_info_payload(self) -> None:
        adapter = AkShareFundamentalAdapter()
        with patch(
            "research_system.adapters.vendors.akshare_fundamental_adapter.call_with_retry",
            side_effect=ConnectionError("private upstream detail"),
        ):
            info, gap = adapter._try_get_individual_info("600519")

        self.assertEqual(info, {})
        self.assertEqual(gap, "individual_info_unavailable")
        self.assertNotIn("private upstream detail", str((info, gap)))

    def test_extract_banking_metrics_builds_same_basis_bank_fields(self) -> None:
        adapter = AkShareFundamentalAdapter()

        metrics = adapter._extract_banking_metrics(
            abstract_payload={
                "净资产收益率": "10.20%",
                "资产负债率": "91.62%",
                "营业总收入同比增长率": "3.08%",
                "净利润同比增长率": "2.26%",
                "销售净利率": "193.11%",
            },
            analysis_payload={
                "股息发放率(%)": "24.50",
                "净资产收益率(%)": "5.37",
                "加权净资产收益率(%)": "5.79",
            },
            abstract_new_payload={
                "sale_net_interest_ratio": {"value": "1.9311"},
            },
            benefit_new_payload={
                "interest_income": {"value": "1983.81亿"},
                "interest_expenses": {"value": "1049.54亿"},
                "benefit_credit_impairment_loss": {"value": "489.39亿"},
            },
            debt_new_payload={
                "general_risk_preparation": {"value": "2436.05亿"},
                "deposits_and_deposits": {"value": "3.59万亿"},
                "settle_provision": {"value": "861.10亿"},
            },
            cash_new_payload={
                "customer_loan_advance_net_addition": {"value": "561.29亿"},
                "customer_and_interbank_deposits_addition": {"value": "944.86亿"},
                "receive_interest_fee_and_commission_cash": {"value": "1983.81亿"},
                "pay_dividends_profits_interest_cash": {"value": "104.45亿"},
            },
        )

        self.assertTrue(metrics["is_bank_like"])
        self.assertAlmostEqual(metrics["roe"] or 0.0, 0.102)
        self.assertAlmostEqual(metrics["asset_liability_ratio"] or 0.0, 0.9162)
        self.assertAlmostEqual(metrics["dividend_payout_ratio"] or 0.0, 24.5)
        self.assertIsNotNone(metrics["interest_margin_proxy"])
        self.assertIsNotNone(metrics["net_interest_spread_proxy"])
        self.assertIsNotNone(metrics["loan_to_deposit_proxy"])
        self.assertIsNotNone(metrics["deposit_growth_proxy"])
        self.assertIsNotNone(metrics["provision_coverage_proxy"])
        self.assertGreaterEqual(len(metrics["available_fields"]), 6)

    def test_extract_bank_peer_comparison_maps_growth_and_dupont_rows(self) -> None:
        adapter = AkShareFundamentalAdapter()

        growth_frame = [
            {
                "代码": "行业平均",
                "简称": "行业平均",
                "营业收入增长率-TTM": 2.98,
                "净利润增长率-TTM": 2.81,
            },
            {
                "代码": "600036",
                "简称": "招商银行",
                "基本每股收益增长率-3年复合排名": 6.0,
                "营业收入增长率-TTM": 3.31,
                "净利润增长率-TTM": 4.52,
            },
        ]
        dupont_frame = [
            {
                "代码": "行业平均",
                "简称": "行业平均",
                "ROE-3年平均": 9.53,
            },
            {
                "代码": "600036",
                "简称": "招商银行",
                "ROE-3年平均排名": 4.0,
                "ROE-3年平均": 13.14,
                "净利率-3年平均": 44.24,
                "总资产周转率-3年平均": 2.93,
            },
        ]

        import pandas as pd

        payload = adapter._extract_bank_peer_comparison(
            symbol="600036",
            growth_frame=pd.DataFrame(growth_frame),
            dupont_frame=pd.DataFrame(dupont_frame),
            valuation_frame=pd.DataFrame(
                [
                    {"代码": "行业平均", "简称": "行业平均", "市净率MRQ": 0.53},
                    {"代码": "600036", "简称": "招商银行", "市净率MRQ排名": 4.0, "市净率MRQ": 0.80, "市盈率TTM": 6.77},
                ]
            ),
        )
        metrics = payload["metrics"]

        self.assertAlmostEqual(metrics["peer_roe_3y_avg"] or 0.0, 0.1314, places=4)
        self.assertAlmostEqual(metrics["peer_net_margin_3y_avg"] or 0.0, 0.4424, places=4)
        self.assertAlmostEqual(metrics["peer_revenue_growth_ttm"] or 0.0, 0.0331, places=4)
        self.assertAlmostEqual(metrics["peer_net_profit_growth_ttm"] or 0.0, 0.0452, places=4)
        self.assertAlmostEqual(metrics["peer_roe_3y_avg_vs_industry_avg"] or 0.0, 0.0361, places=4)
        self.assertAlmostEqual(metrics["peer_revenue_growth_ttm_vs_industry_avg"] or 0.0, 0.0033, places=4)
        self.assertAlmostEqual(metrics["peer_net_profit_growth_ttm_vs_industry_avg"] or 0.0, 0.0171, places=4)

    def test_fetch_bank_peer_valuation_comparison_normalizes_safe_columns(self) -> None:
        adapter = AkShareFundamentalAdapter()

        class _Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "result": {
                        "data": [
                            {
                                "CORRE_SECURITY_CODE": "行业平均",
                                "CORRE_SECURITY_NAME": "行业平均",
                                "PAIMING": None,
                                "PB_MRQ": 0.53,
                                "PE_TTM": 5.60,
                                "PEG": 1.23,
                                "PB": 0.55,
                                "TOTAL_COUNT": 42,
                            },
                            {
                                "CORRE_SECURITY_CODE": "600036",
                                "CORRE_SECURITY_NAME": "招商银行",
                                "PAIMING": 4,
                                "PB_MRQ": 0.80,
                                "PE_TTM": 6.77,
                                "PEG": 0.78,
                                "PB": 0.82,
                                "TOTAL_COUNT": 42,
                            },
                        ]
                    }
                }

        from unittest.mock import patch

        with patch("research_system.adapters.vendors.akshare_fundamental_adapter.requests.get", return_value=_Response()):
            frame = adapter._fetch_bank_peer_valuation_comparison("600036")

        self.assertEqual(list(frame["代码"]), ["行业平均", "600036"])
        self.assertIn("市净率MRQ", frame.columns)
        self.assertIn("市盈率TTM", frame.columns)
        self.assertEqual(frame.iloc[1]["市净率MRQ排名"], 4)
        self.assertAlmostEqual(frame.iloc[1]["市净率MRQ"], 0.80)

    def test_fetch_bank_special_indicators_maps_bank_hard_fields(self) -> None:
        adapter = AkShareFundamentalAdapter()

        class _Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "data": [
                        {
                            "LTDRR": 0.715239692517,
                            "NEWCAPITALADER": 17.76,
                            "HXYJBCZL": 14.13,
                            "NONPERLOAN": 0.94,
                            "BLDKBBL": 387.76,
                            "NET_INTEREST_SPREAD": 1.78,
                            "TOTALDEPOSITS": 9959197000000,
                            "GROSSLOANS": 7464373000000,
                            "ROEJQ": 13.44,
                        }
                    ]
                }

        from unittest.mock import patch

        with patch("research_system.adapters.vendors.akshare_fundamental_adapter.requests.get", return_value=_Response()):
            metrics = adapter._fetch_bank_special_indicators("600036")

        self.assertAlmostEqual(metrics["loan_to_deposit_ratio"] or 0.0, 0.715239692517)
        self.assertAlmostEqual(metrics["capital_adequacy_ratio"] or 0.0, 0.1776, places=4)
        self.assertAlmostEqual(metrics["core_tier1_capital_adequacy_ratio"] or 0.0, 0.1413, places=4)
        self.assertAlmostEqual(metrics["nonperforming_loan_ratio"] or 0.0, 0.0094, places=4)
        self.assertAlmostEqual(metrics["provision_coverage_ratio"] or 0.0, 3.8776, places=4)
        self.assertAlmostEqual(metrics["net_interest_spread"] or 0.0, 0.0178, places=4)


if __name__ == "__main__":
    unittest.main()
