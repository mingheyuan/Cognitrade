from __future__ import annotations

import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from research_system.adapters.vendors import (
    akshare_capital_flow_adapter as capital_flow_module,
)
from research_system.adapters.vendors import akshare_lhb_adapter as lhb_module
from research_system.adapters.vendors import (
    efinance_capital_flow_adapter as efinance_flow_module,
)
from research_system.adapters.vendors import efinance_market_adapter as efinance_market_module


class FinanceDataAdapterTests(unittest.TestCase):
    def test_efinance_market_overview_includes_quote_and_valuation_fields(self) -> None:
        quote = pd.Series(
            {
                "名称": "贵州茅台",
                "最新价": 1418.2,
                "涨跌幅": 1.25,
                "成交额": 3_000_000_000,
                "今开": 1400.0,
            }
        )
        base = pd.Series(
            {
                "股票名称": "贵州茅台",
                "所处行业": "白酒",
                "总市值": 1_780_000_000_000,
                "流通市值": 1_780_000_000_000,
                "市净率": 7.1,
                "市盈率(动)": 21.8,
            }
        )
        fake_efinance = SimpleNamespace(
            stock=SimpleNamespace(
                get_quote_snapshot=lambda _symbol: quote,
                get_base_info=lambda _symbol: base,
            )
        )

        with patch.object(efinance_market_module, "import_module", return_value=fake_efinance):
            result = efinance_market_module.EFinanceMarketAdapter().get_market_overview(
                symbol="600519.SH",
                latest_bar={"close": 1401.0},
            )

        self.assertEqual(result["normalized_symbol"], "600519")
        self.assertEqual(result["security_name"], "贵州茅台")
        self.assertEqual(result["industry"], "白酒")
        self.assertEqual(result["latest_price"], 1418.2)
        self.assertEqual(result["market_cap"], 1_780_000_000_000.0)
        self.assertEqual(result["pe_dynamic"], 21.8)
        self.assertNotIn("pe_ttm", result)

    def test_efinance_market_history_failure_falls_back_to_current_quote(self) -> None:
        quote = pd.Series(
            {
                "名称": "贵州茅台",
                "最新价": 1418.2,
                "涨跌幅": 1.25,
                "今开": 1400.0,
                "昨收": 1400.7,
                "最高": 1420.0,
                "最低": 1390.0,
            }
        )

        def unavailable_history(*_args, **_kwargs):
            raise ConnectionError("push2his unavailable")

        fake_efinance = SimpleNamespace(
            stock=SimpleNamespace(
                get_quote_history=unavailable_history,
                get_quote_snapshot=lambda _symbol: quote,
            )
        )

        with (
            patch.object(efinance_market_module, "import_module", return_value=fake_efinance),
            patch.object(efinance_market_module, "date") as frozen_date,
        ):
            frozen_date.today.return_value = date(2026, 8, 23)
            result = efinance_market_module.EFinanceMarketAdapter().get_market_snapshot(
                symbol="600519.SH",
                start_date="20260801",
                end_date="20260823",
            )

        self.assertEqual(result["history_mode"], "quote_snapshot_fallback")
        self.assertEqual(result["bar_count"], 1)
        self.assertEqual(result["latest_bar"]["close"], 1418.2)
        self.assertEqual(result["latest_bar"]["pre_close"], 1400.7)
        self.assertEqual(result["latest_bar"]["date"], "2026-08-23")

    def test_efinance_historical_market_failure_never_uses_current_quote(self) -> None:
        history_error = ConnectionError("push2his unavailable")
        history = Mock(side_effect=history_error)
        current_quote = Mock(return_value=pd.Series({"最新价": 1418.2}))
        fake_efinance = SimpleNamespace(stock=SimpleNamespace(
            get_quote_history=history,
            get_quote_snapshot=current_quote,
        ))

        with (
            patch.object(efinance_market_module, "import_module", return_value=fake_efinance),
            patch.object(efinance_market_module, "date") as frozen_date,
        ):
            frozen_date.today.return_value = date(2026, 9, 11)
            with self.assertRaises(ConnectionError) as caught:
                efinance_market_module.EFinanceMarketAdapter().get_market_snapshot(
                    symbol="600519.SH",
                    start_date="20260801",
                    end_date="20260823",
                )

        self.assertIs(caught.exception, history_error)
        history.assert_called_once_with("600519", beg="20260801", end="20260823", klt=101, fqt=0)
        current_quote.assert_not_called()

    def test_efinance_capital_flow_normalizes_and_orders_recent_history(self) -> None:
        history = pd.DataFrame(
            [
                {"日期": "2026-08-20", "股票代码": "600519", "主力净流入": 100_000_000},
                {"日期": "2026-08-21", "股票代码": "600519", "主力净流入": -50_000_000},
            ]
        )
        fake_efinance = SimpleNamespace(
            stock=SimpleNamespace(get_history_bill=lambda symbol: history)
        )

        with (
            patch.object(
                efinance_flow_module.EFinanceCapitalFlowAdapter,
                "_bounded_history_rows",
                side_effect=ConnectionError("bounded endpoint unavailable"),
            ),
            patch.object(efinance_flow_module, "import_module", return_value=fake_efinance),
        ):
            result = efinance_flow_module.EFinanceCapitalFlowAdapter().get_capital_flow_snapshot(
                symbol="600519.SH"
            )

        self.assertEqual(result["normalized_symbol"], "600519")
        self.assertEqual(result["latest_flow"]["date"], "2026-08-21")
        self.assertEqual(result["latest_flow"]["main_net_inflow"], -50_000_000.0)
        self.assertEqual(
            [row["date"] for row in result["main_fund_flow_20d"]],
            ["2026-08-20", "2026-08-21"],
        )
        self.assertEqual(result["main_20d"], "+5000.0万")

    def test_efinance_capital_flow_uses_bounded_target_history(self) -> None:
        rows = [
            {"日期": f"2026-08-{day:02d}", "股票代码": "688012", "主力净流入": day}
            for day in range(1, 22)
        ]
        with patch.object(
            efinance_flow_module.EFinanceCapitalFlowAdapter,
            "_bounded_history_rows",
            return_value=rows,
        ):
            result = efinance_flow_module.EFinanceCapitalFlowAdapter().get_capital_flow_snapshot(
                symbol="688012.SH"
            )

        self.assertEqual(len(result["main_fund_flow_20d"]), 20)
        self.assertEqual(result["latest_flow"]["date"], "2026-08-21")

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
        latest_date = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
        date_frame = pd.DataFrame({"date": [latest_date]})
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
        self.assertEqual(result["lhb_count_30d"], 3)

    def test_lhb_successful_empty_date_response_is_a_verified_zero_event(self) -> None:
        with patch.object(
            lhb_module.ak,
            "stock_lhb_stock_detail_date_em",
            return_value=pd.DataFrame(),
        ):
            result = lhb_module.AkShareLHBAdapter().get_lhb_snapshot(symbol="600519")

        self.assertEqual(result["observation_status"], "verified_empty")
        self.assertEqual(result["observation_window_days"], 30)
        self.assertEqual(result["lhb_statistics"], [])
        self.assertIsNone(result["latest_lhb_date"])

    def test_lhb_historical_record_outside_window_is_not_counted(self) -> None:
        historical_date = (date.today() - timedelta(days=31)).strftime("%Y%m%d")
        with patch.object(
            lhb_module.ak,
            "stock_lhb_stock_detail_date_em",
            return_value=pd.DataFrame({"date": [historical_date]}),
        ):
            result = lhb_module.AkShareLHBAdapter().get_lhb_snapshot(symbol="600519")

        self.assertEqual(result["observation_status"], "verified_empty")
        self.assertEqual(result["lhb_count_30d"], 0)
        self.assertEqual(result["latest_historical_lhb_date"], historical_date)

    def test_lhb_statistics_match_zero_padded_numeric_provider_codes(self) -> None:
        latest_date = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
        date_frame = pd.DataFrame({"date": [latest_date]})
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
