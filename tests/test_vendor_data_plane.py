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

    def test_market_router_preserves_uzi_global_venue_currency_contract(self) -> None:
        cases = {
            "7203.T": ("JP", "tse", "JPY"),
            "005930.KS": ("KR", "krx", "KRW"),
            "2330.TW": ("TW", "twse", "TWD"),
            "D05.SI": ("SG", "sgx", "SGD"),
            "SHOP.TO": ("CA", "tsx", "CAD"),
            "BHP.AX": ("AU", "asx", "AUD"),
            "ASML.AS": ("NL", "euronext_amsterdam", "EUR"),
            "SHEL.L": ("GB", "lse", "GBP"),
            "SIE.DE": ("DE", "xetra", "EUR"),
            "AIR.PA": ("FR", "euronext_paris", "EUR"),
            "NESN.SW": ("CH", "six", "CHF"),
            "PETR4.SA": ("BR", "b3", "BRL"),
            "RELIANCE.NS": ("IN", "nse", "INR"),
        }
        router = MarketRouter()
        for symbol, expected in cases.items():
            route = router.classify(symbol=symbol)
            self.assertEqual((route.market_code, route.exchange, route.currency), expected)
            self.assertEqual(route.market, "global_equity")
            self.assertEqual(route.normalized_symbol, symbol)

        unknown = router.classify(symbol="EXAMPLE.XX")
        self.assertEqual(unknown.market, "global_equity")
        self.assertEqual(unknown.market_code, "G")
        self.assertEqual(unknown.exchange, "xx")


if __name__ == "__main__":
    unittest.main()
