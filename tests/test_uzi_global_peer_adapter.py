from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from domain_packs.finance_ashare.skills.uzi_source_snapshot.skill import PeersSnapshotSkill
from research_system.adapters.vendors.uzi_global_peer_adapter import (
    apply_yearly_fx,
    build_global_peer_comparison,
    normalize_yahoo_timeseries,
    rank_global_candidates,
    to_yahoo_symbol,
)


def _annual(symbol: str, gross_margin: float, net_margin: float) -> dict:
    return {
        "symbol": symbol,
        "currency": "USD",
        "basis": "annual",
        "source": "fixture",
        "periods": {
            "2024-12-31": {
                "revenue_base": 100,
                "gross_margin": gross_margin,
                "net_margin": net_margin,
                "roe": 10,
            }
        },
    }


class UziGlobalPeerAdapterTests(unittest.TestCase):
    def test_yahoo_symbol_mapping_for_supported_research_markets(self) -> None:
        self.assertEqual(to_yahoo_symbol("600519"), "600519.SS")
        self.assertEqual(to_yahoo_symbol("000001"), "000001.SZ")
        self.assertEqual(to_yahoo_symbol("00700.HK"), "0700.HK")
        self.assertEqual(to_yahoo_symbol("AAPL"), "AAPL")
        self.assertEqual(to_yahoo_symbol("7203.T"), "7203.T")
        self.assertEqual(to_yahoo_symbol("005930.KS"), "005930.KS")
        self.assertEqual(to_yahoo_symbol("600519.SH"), "600519.SS")

    def test_timeseries_normalization_preserves_negative_values(self) -> None:
        payload = {
            "timeseries": {
                "result": [
                    {
                        "meta": {"type": ["annualTotalRevenue"]},
                        "annualTotalRevenue": [{
                            "asOfDate": "2024-12-31", "periodType": "12M", "currencyCode": "JPY",
                            "reportedValue": {"raw": 1200},
                        }],
                    },
                    {
                        "meta": {"type": ["annualNetIncome"]},
                        "annualNetIncome": [{
                            "asOfDate": "2024-12-31", "periodType": "12M", "currencyCode": "JPY",
                            "reportedValue": {"raw": -180},
                        }],
                    },
                    {
                        "meta": {"type": ["annualStockholdersEquity"]},
                        "annualStockholdersEquity": [{
                            "asOfDate": "2024-12-31", "periodType": "12M", "currencyCode": "JPY",
                            "reportedValue": {"raw": 600},
                        }],
                    },
                ]
            }
        }
        result = normalize_yahoo_timeseries("6758.T", payload)
        self.assertEqual(result["currency"], "JPY")
        self.assertEqual(result["periods"]["2024-12-31"]["net_margin"], -15.0)
        self.assertEqual(result["periods"]["2024-12-31"]["roe"], -30.0)

    def test_candidate_ranking_deduplicates_issuer_and_avoids_mixed_currency_scale(self) -> None:
        target = {
            "symbol": "AAPL", "name": "Apple Inc.", "industry": "Consumer Electronics",
            "sector": "Technology", "market_cap": 3000, "currency": "USD",
        }
        candidates = [
            {"symbol": "AAPL.BA", "name": "Apple Inc.", "industry": "Consumer Electronics"},
            {"symbol": "005930.KS", "name": "Samsung Electronics Co., Ltd.", "industry": "Consumer Electronics", "sector": "Technology", "market_cap": 3000, "currency": "KRW", "data_coverage": 0.9},
            {"symbol": "005935.KS", "name": "Samsung Electronics Co., Ltd.", "industry": "Consumer Electronics", "sector": "Technology", "market_cap": 100, "currency": "KRW", "data_coverage": 0.4, "is_secondary": True},
            {"symbol": "6758.T", "name": "Sony Group Corporation", "industry": "Consumer Electronics", "sector": "Technology", "market_cap": 1000, "currency": "USD", "data_coverage": 0.8},
        ]
        ranked = rank_global_candidates(target, candidates)
        self.assertEqual([row["symbol"] for row in ranked], ["6758.T", "005930.KS"])
        samsung = next(row for row in ranked if row["symbol"] == "005930.KS")
        self.assertNotIn("规模可比", samsung["selection_reasons"])

    def test_global_comparison_requires_three_peers_before_percentile_or_ready_conclusion(self) -> None:
        class Provider:
            def profile(self, symbol):
                return {"symbol": symbol, "name": "Target", "industry": "Hardware"}

            def discover(self, _symbol, limit=30):
                return [{"symbol": "P1", "name": "Peer 1"}, {"symbol": "BAD", "name": "Bad"}, {"symbol": "P2", "name": "Peer 2"}][:limit]

            def financials(self, symbol):
                if symbol == "BAD":
                    raise TimeoutError("fixture timeout")
                return _annual(symbol, 30 if symbol == "P1" else 40, 10)

        result = build_global_peer_comparison("TARGET", provider=Provider(), limit=8)
        self.assertEqual(result["peer_count"], 2)
        self.assertEqual(result["conclusion_status"], "insufficient_peers")
        self.assertEqual(result["target_percentile"], {})
        self.assertEqual(result["failed_symbols"], ["BAD"])

    def test_fx_adds_base_fields_without_overwriting_native_currency_values(self) -> None:
        raw = {
            "currency": "JPY",
            "periods": {"2024-03-31": {"revenue": 1200, "net_income": -50, "gross_margin": 25}},
        }
        normalized = apply_yearly_fx(raw, {"2024": 0.0068}, "USD")
        facts = normalized["periods"]["2024-03-31"]
        self.assertEqual(facts["revenue"], 1200)
        self.assertEqual(facts["revenue_base"], 8.16)
        self.assertEqual(facts["net_income_base"], -0.34)
        self.assertEqual(normalized["currency"], "JPY")
        self.assertEqual(normalized["base_currency"], "USD")

    @patch("domain_packs.finance_ashare.skills.uzi_source_snapshot.skill.UziSourceSnapshotSkill._industry_context")
    @patch("domain_packs.finance_ashare.skills.uzi_source_snapshot.skill.FundamentalSnapshotSkill.run")
    @patch("domain_packs.finance_ashare.skills.uzi_source_snapshot.skill.AkShareSectorAdapter.get_sector_snapshot")
    @patch("domain_packs.finance_ashare.skills.uzi_source_snapshot.skill.fetch_global_peer_comparison")
    @patch("domain_packs.finance_ashare.skills.uzi_source_snapshot.skill.call_with_retry")
    def test_peer_snapshot_keeps_local_and_global_comparison(
        self,
        retry_call,
        global_fetch,
        sector_fetch,
        fundamental_run,
        industry_context,
    ) -> None:
        retry_call.side_effect = lambda callback, **_kwargs: callback()
        fundamental_run.return_value = {
            "snapshot_type": "fundamental", "symbol": "600519",
            "facts": {"info": {"股票简称": "贵州茅台"}},
            "source": {"skill": "test", "vendor": "test", "from_cache": False},
            "source_trace": [],
        }
        industry_context.return_value = {"industry": "白酒", "resolved_sector": {"sector_name": "酿酒行业", "sector_type": "industry"}}
        sector_fetch.return_value = {"facts": {"top_constituents": [{"symbol": "000858", "name": "五粮液"}]}}
        global_fetch.return_value = {"peer_count": 3, "conclusion_status": "ready", "peers": [{"symbol": "DEO"}]}

        with patch(
            "domain_packs.finance_ashare.skills.uzi_source_snapshot.skill.ak.stock_financial_analysis_indicator_em",
            side_effect=lambda symbol: pd.DataFrame([
                {
                    "REPORT_DATE_NAME": "2025年报",
                    "ROEJQ": 31.2 if symbol.startswith("600519") else 22.4,
                }
            ]),
        ):
            result = PeersSnapshotSkill().run(query="贵州茅台同行", symbol="600519")

        self.assertEqual(result["facts"]["comparison"]["peers"][0]["symbol"], "000858")
        self.assertEqual(result["facts"]["comparison"]["peers"][0]["roe"], 22.4)
        self.assertEqual(result["facts"]["comparison"]["roe_comparison"]["target"], 31.2)
        self.assertEqual(result["facts"]["comparison"]["roe_comparison"]["peer_average"], 22.4)
        self.assertEqual(result["facts"]["global_peer_comparison"]["peer_count"], 3)
        global_fetch.assert_called_once_with(
            "600519", basic={"name": "贵州茅台", "industry": "白酒"}, limit=8,
        )


if __name__ == "__main__":
    unittest.main()
