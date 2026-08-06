from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

import yaml
import pandas as pd

from research_system.adapters.vendors.data_source_registry import DataSourceRegistry
from research_system.adapters.vendors.market_router import MarketRouter
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from research_system.adapters.vendors.uzi_contest_adapter import UziContestAdapter
from research_system.adapters.vendors.uzi_source_catalog import UZI_SOURCE_ROWS
from research_system.runtime.platform.settings import Settings
from domain_packs.finance_ashare.tools.security_master import AShareSecurityMaster
from domain_packs.finance_ashare.skills.uzi_source_snapshot.skill import IndustrySnapshotSkill


EXPECTED_UZI_SOURCE_IDS = {
    "em_push2", "em_quote", "em_data", "xq_api", "tencent_qt", "sina_quote", "cninfo", "hkexnews",
    "aastocks", "cls", "yicai", "wallstreetcn", "cfi", "hexun", "163money", "jrj", "investing",
    "mx_api", "akshare_lhb", "baostock", "yfinance", "ddgs", "yahoo_chart_v8", "tencent_hk_quote",
    "coingecko_simple_price", "coingecko_markets", "okx_spot_tickers", "kucoin_stats", "kraken_trades",
    "gemini_ticker", "coinlore_tickers", "geckoterminal_networks", "jin10_flash", "em_kuaixun",
    "em_stock_ann", "qh99_inventory", "cfachina", "ths_news_today", "iwencai", "ths_f10", "xueqiu_f10",
    "legulegu", "stockstar", "futu", "yuncaijing", "guba_em_list", "jisilu", "fx678", "cmc", "sse",
    "szse", "csrc", "gov_cn", "miit", "ndrc", "samr", "shfe", "dce", "czce", "100ppi", "cnstock",
    "cs_cn", "stcn", "nbd", "pbc", "safe", "stats_gov", "chinamoney", "chinabond", "ine",
}


class UziSourceParityTests(unittest.TestCase):
    def test_catalog_matches_all_70_uzi_392_source_ids(self) -> None:
        catalog_ids = {str(row[0]) for row in UZI_SOURCE_ROWS}
        self.assertEqual(len(UZI_SOURCE_ROWS), 70)
        self.assertEqual(catalog_ids, EXPECTED_UZI_SOURCE_IDS)
        self.assertTrue(all(row[3] and row[4] for row in UZI_SOURCE_ROWS))
        registry_ids = {source.id for source in DataSourceRegistry().list_all() if source.upstream}
        self.assertEqual(registry_ids, EXPECTED_UZI_SOURCE_IDS)

        repository_root = Path(__file__).resolve().parents[1]
        config = yaml.safe_load(
            (repository_root / "workspace" / "config" / "data_sources.yml").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(config["catalog"]["source_count"], 70)
        self.assertEqual(config["catalog"]["version"], "3.9.2")
        self.assertEqual(set(config["sources"]), EXPECTED_UZI_SOURCE_IDS)

        provider_policy = yaml.safe_load(
            (repository_root / "workspace" / "config" / "provider_policy.yml").read_text(
                encoding="utf-8"
            )
        )
        expected_dimensions = {
            "market", "fundamental", "kline", "macro", "peers", "chain", "research",
            "industry", "materials", "futures", "valuation", "governance", "capital_flow",
            "policy", "moat", "events", "lhb", "sentiment", "trap", "contests",
            "fund_holders", "similar_stocks",
        }
        self.assertTrue(expected_dimensions.issubset(provider_policy["provider_chains"]))
        self.assertTrue(
            all(provider_policy["provider_chains"][dimension]["providers"] for dimension in expected_dimensions)
        )

    def test_provider_chain_includes_zero_key_and_keyed_fallbacks_in_order(self) -> None:
        settings = Settings.from_env(Path(__file__).resolve().parents[1]).model_copy(
            update={"tushare_token": "configured", "mx_api_key": "configured"}
        )
        registry = VendorProviderRegistry(settings)
        self.assertEqual(
            [provider.provider_id for provider in registry.get_provider_chain("market", "cn_equity")],
            ["akshare_market", "efinance_market", "tushare_market", "baostock_market", "direct_http_market"],
        )
        self.assertEqual(
            [provider.provider_id for provider in registry.get_provider_chain("fundamental", "cn_equity")],
            ["mx_api_fundamental", "akshare_fundamental", "tushare_fundamental", "baostock_fundamental"],
        )

    def test_market_router_canonicalizes_a_h_and_us_symbols(self) -> None:
        router = MarketRouter()
        self.assertEqual(router.classify(symbol="600519").market, "cn_equity")
        self.assertEqual(router.classify(symbol="HK00700").normalized_symbol, "00700.HK")
        self.assertEqual(router.classify(symbol="AAPL").market, "us_equity")

        master = AShareSecurityMaster()
        resolution = master.resolve(query="比较 HK00700、00700.HK、SH600519 和 AAPL")
        self.assertIn("00700.HK", resolution.symbols)
        self.assertIn("600519", resolution.symbols)
        self.assertIn("AAPL", resolution.symbols)
        self.assertNotIn("HK00700", resolution.symbols)
        self.assertNotIn("SH600519", resolution.symbols)

    def test_public_contest_fallback_does_not_require_xueqiu_credentials(self) -> None:
        settings = Settings.from_env(Path(__file__).resolve().parents[1]).model_copy(
            update={"sensitive_sources_enabled": False, "sensitive_source_allowlist": [], "xueqiu_cookie": None}
        )
        adapter = UziContestAdapter(settings)
        with patch.object(adapter, "_taoguba", return_value=[{"title": "real public evidence"}]), patch.object(
            adapter, "_ths", return_value=[]
        ), patch.object(adapter, "_dpswang", return_value=[]):
            result = adapter.get_snapshot(symbol="600519")
        self.assertEqual(result["availability"], "available")
        self.assertEqual(result["taoguba_mentions"][0]["title"], "real public evidence")
        self.assertFalse(result["xueqiu_cubes"])

    def test_browser_sources_require_all_explicit_policy_gates(self) -> None:
        from research_system.adapters.vendors.uzi_browser_adapter import UziBrowserAdapter

        settings = Settings.from_env(Path(__file__).resolve().parents[1]).model_copy(
            update={
                "browser_sources_enabled": True,
                "sensitive_sources_enabled": False,
                "sensitive_source_allowlist": ["iwencai"],
            }
        )
        result = UziBrowserAdapter(settings).collect_many(
            source_ids=("iwencai",), symbol="600519", query="贵州茅台"
        )
        self.assertFalse(result["evidence"])
        self.assertEqual(result["source_trace"][0]["status"], "disabled")
        self.assertEqual(result["source_trace"][0]["reason"], "sensitive_sources_disabled")

    def test_industry_dimension_falls_back_to_cninfo_profile(self) -> None:
        skill = IndustrySnapshotSkill()
        with patch(
            "domain_packs.finance_ashare.skills.uzi_source_snapshot.skill.ak.stock_individual_info_em",
            side_effect=ConnectionError("eastmoney unavailable"),
        ), patch(
            "domain_packs.finance_ashare.skills.uzi_source_snapshot.skill.ak.stock_profile_cninfo",
            return_value=pd.DataFrame(
                [{"所属行业": "酒、饮料和精制茶制造业", "主营业务": "白酒生产与销售"}]
            ),
        ), patch.object(
            skill, "_safe_search", return_value={"results": [], "source_trace": []}
        ):
            result = skill._industry_context("600519", query="贵州茅台")

        self.assertEqual(result["industry"], "酒、饮料和精制茶制造业")
        self.assertEqual(result["resolved_sector"]["sector_name"], "酿酒行业")
        self.assertTrue(
            any(
                row.get("source_id") == "cninfo" and row.get("status") == "available"
                for row in result["_source_trace"]
            )
        )


if __name__ == "__main__":
    unittest.main()
