from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pandas as pd

from research_system.adapters.vendors.akshare_market_adapter import AkShareMarketAdapter


class AkShareMarketAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = AkShareMarketAdapter()
        AkShareMarketAdapter._spot_cache_df = None
        AkShareMarketAdapter._spot_cache_at = None

    @patch("research_system.adapters.vendors.akshare_market_adapter.ak.stock_zh_a_daily")
    def test_get_market_snapshot_keeps_complete_bar_series(self, mock_daily) -> None:
        mock_daily.return_value = pd.DataFrame(
            [
                {"date": "2026-07-10", "open": 99.0, "high": 101.0, "low": 98.0, "close": 100.0, "volume": 1000},
                {"date": "2026-07-11", "open": 100.0, "high": 103.0, "low": 99.0, "close": 102.0, "volume": 1200},
                {"date": "2026-07-13", "open": 102.0, "high": 104.0, "low": 101.0, "close": 103.0, "volume": 1500},
            ]
        )

        payload = self.adapter.get_market_snapshot(
            symbol="600519",
            start_date="20260701",
            end_date="20260714",
        )

        self.assertEqual(payload["bar_count"], 3)
        self.assertEqual(len(payload["bars"]), 3)
        self.assertEqual(payload["bars"][0]["date"], "2026-07-10")
        self.assertEqual(payload["latest_bar"], payload["bars"][-1])

    @patch("research_system.adapters.vendors.akshare_market_adapter.ak.stock_value_em")
    @patch("research_system.adapters.vendors.akshare_market_adapter.ak.stock_zh_a_spot")
    def test_get_market_overview_prefers_latest_bar_and_merges_valuation(self, mock_spot, mock_value) -> None:
        mock_spot.side_effect = AssertionError("spot endpoint should not be called when latest_bar is available")
        mock_value.return_value = pd.DataFrame(
            [
                {
                    "数据日期": "2026-07-01",
                    "当日收盘价": 10.16,
                    "当日涨跌幅": 1.0945,
                    "总市值": 197164128891.68,
                    "流通市值": 197160902634.48,
                    "总股本": 19405918198,
                    "流通股本": 19405600653,
                    "PE(TTM)": 4.5788,
                    "PE(静)": 4.6246,
                    "市净率": 0.4248,
                    "PEG值": -1.0992,
                    "市现率": 1.0338,
                    "市销率": 1.4823,
                }
            ]
        )
        latest_bar = {
            "open": 10.05,
            "high": 10.18,
            "low": 9.99,
            "close": 10.16,
            "amount": 604000000.0,
            "volume": 90688982.0,
            "pre_close": 10.05,
        }

        payload = self.adapter.get_market_overview(symbol="000001", latest_bar=latest_bar)

        self.assertTrue(payload["spot_found"])
        self.assertTrue(payload["valuation_found"])
        self.assertAlmostEqual(payload["latest_price"] or 0.0, 10.16)
        self.assertAlmostEqual(payload["pct_change"] or 0.0, 1.0945, places=3)
        self.assertAlmostEqual(payload["pb"] or 0.0, 0.4248)
        self.assertAlmostEqual(payload["pe_ttm"] or 0.0, 4.5788)
        self.assertAlmostEqual(payload["market_cap"] or 0.0, 197164128891.68)
        self.assertIn("spot", payload)
        self.assertIn("valuation", payload)
        self.assertEqual(mock_spot.call_count, 0)

    @patch("research_system.adapters.vendors.akshare_market_adapter.ak.stock_value_em")
    @patch("research_system.adapters.vendors.akshare_market_adapter.ak.stock_zh_a_spot")
    def test_get_market_overview_keeps_spot_when_valuation_degrades(self, mock_spot, mock_value) -> None:
        mock_spot.return_value = pd.DataFrame(
            [
                {
                    "代码": "000001",
                    "名称": "平安银行",
                    "最新价": 10.16,
                    "涨跌幅": 1.09,
                    "成交额": 604000000.0,
                }
            ]
        )
        mock_value.side_effect = RuntimeError("valuation endpoint unavailable")

        payload = self.adapter.get_market_overview(symbol="000001")

        self.assertTrue(payload["spot_found"])
        self.assertTrue(payload["valuation_found"])
        self.assertAlmostEqual(payload["latest_price"] or 0.0, 10.16)
        self.assertIsNone(payload["pb"])
        self.assertEqual(payload["valuation"]["availability"], "degraded")
        self.assertIn("stock_value_unavailable", payload["valuation"]["note"])

    @patch("research_system.adapters.vendors.akshare_market_adapter.ak.stock_zh_a_spot")
    def test_spot_dataframe_uses_short_ttl_cache(self, mock_spot) -> None:
        mock_spot.return_value = pd.DataFrame(
            [
                {
                    "代码": "000001",
                    "名称": "平安银行",
                    "最新价": 10.16,
                    "涨跌幅": 1.09,
                    "成交额": 604000000.0,
                }
            ]
        )

        first = self.adapter._spot_dataframe()
        second = self.adapter._spot_dataframe()

        self.assertEqual(mock_spot.call_count, 1)
        self.assertTrue(first.equals(second))

        AkShareMarketAdapter._spot_cache_at = datetime.now(UTC) - AkShareMarketAdapter._spot_cache_ttl - timedelta(seconds=1)
        third = self.adapter._spot_dataframe()

        self.assertEqual(mock_spot.call_count, 2)
        self.assertTrue(first.equals(third))


if __name__ == "__main__":
    unittest.main()
