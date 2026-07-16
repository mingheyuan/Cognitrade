from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from research_system.adapters.vendors import akshare_capital_flow_adapter as capital_flow_module
from research_system.adapters.vendors import akshare_lhb_adapter as lhb_module


class FinanceDataAdapterTests(unittest.TestCase):
    def test_capital_flow_keeps_individual_flow_when_main_rank_fails(self) -> None:
        individual_flow = pd.DataFrame([{"date": "2026-07-15", "net_flow": 123.4}])

        with (
            patch.object(
                capital_flow_module.ak,
                "stock_individual_fund_flow",
                return_value=individual_flow,
            ),
            patch.object(
                capital_flow_module.ak,
                "stock_main_fund_flow",
                side_effect=ConnectionError("main rank endpoint unavailable"),
            ),
        ):
            result = capital_flow_module.AkShareCapitalFlowAdapter().get_capital_flow_snapshot(
                symbol="600519"
            )

        self.assertEqual(result["latest_flow"]["net_flow"], 123.4)
        self.assertEqual(len(result["recent_flows"]), 1)
        self.assertIsNone(result["main_flow_rank"])
        self.assertEqual(result["main_flow_rank_status"], "provider_unavailable")

    def test_lhb_statistics_use_period_and_filter_to_requested_symbol(self) -> None:
        date_frame = pd.DataFrame({"date": ["20260715"]})
        buy_frame = pd.DataFrame({"side": ["buy"], "amount": [100.0]})
        sell_frame = pd.DataFrame({"side": ["sell"], "amount": [80.0]})
        statistics_frame = pd.DataFrame(
            {
                "\u4ee3\u7801": ["600519", "000001"],
                "\u4e0a\u699c\u6b21\u6570": [3, 5],
            }
        )

        def detail_loader(*, flag: str, **_kwargs):
            if flag == "\u4e70\u5165":
                return buy_frame
            if flag == "\u5356\u51fa":
                return sell_frame
            raise AssertionError(f"unexpected flag: {flag}")

        with (
            patch.object(
                lhb_module.ak,
                "stock_lhb_stock_detail_date_em",
                return_value=date_frame,
            ),
            patch.object(
                lhb_module.ak,
                "stock_lhb_stock_detail_em",
                side_effect=detail_loader,
            ),
            patch.object(
                lhb_module.ak,
                "stock_lhb_stock_statistic_em",
                return_value=statistics_frame,
            ) as statistics_loader,
        ):
            result = lhb_module.AkShareLHBAdapter().get_lhb_snapshot(symbol="600519")

        self.assertEqual(
            statistics_loader.call_args.kwargs["symbol"],
            "\u8fd1\u4e00\u6708",
        )
        self.assertEqual(
            [row["\u4ee3\u7801"] for row in result["lhb_statistics"]],
            ["600519"],
        )
        self.assertEqual(result["buy_detail"][0]["side"], "buy")
        self.assertEqual(result["sell_detail"][0]["side"], "sell")

    def test_lhb_statistics_match_zero_padded_numeric_provider_codes(self) -> None:
        date_frame = pd.DataFrame({"date": ["20260715"]})
        detail_frame = pd.DataFrame({"side": ["buy"], "amount": [100.0]})
        statistics_frame = pd.DataFrame({"\u4ee3\u7801": [1]})

        with (
            patch.object(
                lhb_module.ak,
                "stock_lhb_stock_detail_date_em",
                return_value=date_frame,
            ),
            patch.object(
                lhb_module.ak,
                "stock_lhb_stock_detail_em",
                return_value=detail_frame,
            ),
            patch.object(
                lhb_module.ak,
                "stock_lhb_stock_statistic_em",
                return_value=statistics_frame,
            ),
        ):
            result = lhb_module.AkShareLHBAdapter().get_lhb_snapshot(symbol="000001")

        self.assertEqual(len(result["lhb_statistics"]), 1)


if __name__ == "__main__":
    unittest.main()
