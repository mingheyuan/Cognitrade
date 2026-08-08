from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

from domain_packs.finance_ashare.subagents.uzi_methodology import build_features
from research_system.adapters.vendors.yfinance_fundamental_adapter import YFinanceFundamentalAdapter


class YFinanceFundamentalAdapterTests(unittest.TestCase):
    def test_ttm_and_label_based_balance_sheet_fields_are_preserved(self) -> None:
        annual = pd.DataFrame(
            {
                pd.Timestamp("2024-12-31"): [100_000_000_000, 10_000_000_000],
                pd.Timestamp("2025-12-31"): [120_000_000_000, 12_000_000_000],
            },
            index=["Total Revenue", "Net Income"],
        )
        quarterly = pd.DataFrame(
            {
                pd.Timestamp("2025-09-30"): [28_000_000_000, 2_800_000_000],
                pd.Timestamp("2025-12-31"): [30_000_000_000, 3_000_000_000],
                pd.Timestamp("2026-03-31"): [32_000_000_000, 3_200_000_000],
                pd.Timestamp("2026-06-30"): [34_000_000_000, 3_400_000_000],
            },
            index=["Total Revenue", "Net Income"],
        )
        balance = pd.DataFrame(
            {
                pd.Timestamp("2026-06-30"): [20_000_000_000, 8_000_000_000, 50_000_000_000, 90_000_000_000, 40_000_000_000],
            },
            index=[
                "Total Debt",
                "Cash And Cash Equivalents",
                "Stockholders Equity",
                "Total Assets",
                "Total Liabilities Net Minority Interest",
            ],
        )

        class FakeTicker:
            info = {
                "longName": "Fixture Corp",
                "marketCap": 200_000_000_000,
                "totalRevenue": 124_000_000_000,
                "netIncomeToCommon": 12_400_000_000,
                "operatingCashflow": 15_000_000_000,
                "freeCashflow": 11_000_000_000,
                "returnOnEquity": 0.2,
                "profitMargins": 0.1,
                "trailingPE": 16,
                "priceToBook": 4,
            }
            financials = annual
            quarterly_financials = quarterly
            balance_sheet = balance
            cashflow = pd.DataFrame()

        fake_yfinance = SimpleNamespace(Ticker=lambda _symbol: FakeTicker())
        with patch(
            "research_system.adapters.vendors.yfinance_fundamental_adapter.import_module",
            return_value=fake_yfinance,
        ):
            result = YFinanceFundamentalAdapter().get_fundamental_snapshot(symbol="AAPL")

        self.assertEqual(result["financial_basis"], "TTM")
        self.assertEqual(result["financial_period"], "2026-06-30")
        self.assertEqual(result["financial_abstract_history"][-1]["revenue"], 124_000_000_000)
        self.assertEqual(result["financial_abstract_history"][-1]["net_income"], 12_400_000_000)
        self.assertEqual(result["financial_health"]["total_debt"], 20_000_000_000)
        self.assertEqual(result["financial_health"]["cash"], 8_000_000_000)
        self.assertEqual(result["financial_health"]["equity"], 50_000_000_000)
        self.assertEqual(result["data_gap"], [])

        features = build_features(
            [
                {
                    "snapshot_type": "market",
                    "symbol": "AAPL",
                    "facts": {"market_overview": {"latest_price": 200, "market_cap": 200_000_000_000, "total_shares": 1_000_000_000}},
                    "source": {"provider_id": "fixture_market"},
                },
                {
                    "snapshot_type": "fundamental",
                    "symbol": "AAPL",
                    "facts": result,
                    "source": {"provider_id": "yfinance_fundamental"},
                },
            ],
            "AAPL",
        )
        self.assertEqual(features["market"], "U")
        self.assertEqual(features["country"], "US")
        self.assertEqual(features["total_debt_yi"], 200)
        self.assertEqual(features["cash_yi"], 80)
        self.assertEqual(features["equity_yi"], 500)


if __name__ == "__main__":
    unittest.main()
