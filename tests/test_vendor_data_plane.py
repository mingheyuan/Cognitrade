from __future__ import annotations

import unittest

from research_system.adapters.vendors.fallback_merge import merge_provider_facts
from research_system.adapters.vendors.market_router import MarketRouter


class FallbackMergeTests(unittest.TestCase):
    def test_merge_provider_facts_backfills_missing_fields(self) -> None:
        merged = merge_provider_facts(
            primary_facts={"price": 12.3, "market_cap": None, "pe_ttm": None},
            fallback_fact_sets=[
                ("eastmoney", {"market_cap": 1000}),
                ("cninfo", {"pe_ttm": 18.5}),
            ],
        )

        self.assertEqual(merged["price"], 12.3)
        self.assertEqual(merged["market_cap"], 1000)
        self.assertEqual(merged["pe_ttm"], 18.5)
        self.assertTrue(merged["_fallback_applied"])
        self.assertEqual(merged["_field_sources"]["market_cap"], "eastmoney")
        self.assertEqual(merged["_field_sources"]["pe_ttm"], "cninfo")


class MarketRouterTests(unittest.TestCase):
    def test_market_router_classifies_equity_symbol(self) -> None:
        route = MarketRouter().classify(symbol="600000", query="看下浦发银行")
        self.assertEqual(route.exchange, "sh")
        self.assertEqual(route.security_type, "stock")
        self.assertEqual(route.route_hint, "stock_snapshot")

    def test_market_router_classifies_etf_symbol(self) -> None:
        route = MarketRouter().classify(symbol="510300", query="看下沪深300ETF")
        self.assertEqual(route.exchange, "sh")
        self.assertEqual(route.security_type, "etf")
        self.assertEqual(route.route_hint, "fund_like_snapshot")

    def test_market_router_classifies_convertible_bond_symbol(self) -> None:
        route = MarketRouter().classify(symbol="123001", query="看下可转债")
        self.assertEqual(route.security_type, "convertible_bond")
        self.assertEqual(route.route_hint, "bond_like_snapshot")


if __name__ == "__main__":
    unittest.main()
