from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import importlib
import hashlib
import json
from threading import Lock
from time import sleep
import unittest
from unittest.mock import patch

import yaml
import pandas as pd

from research_system.adapters.vendors.data_source_registry import DataSourceRegistry
from research_system.adapters.vendors.market_router import MarketRouter
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from research_system.adapters.vendors.uzi_contest_adapter import UziContestAdapter
from research_system.adapters.vendors.uzi_source_catalog import (
    UZI_SOURCE_CATALOG_SHA256,
    UZI_SOURCE_ROWS,
)
from research_system.runtime.platform.settings import Settings
from domain_packs.finance_ashare.tools.security_master import AShareSecurityMaster
from domain_packs.finance_ashare.tools import snapshot_collector
from domain_packs.finance_ashare.skills.uzi_source_snapshot.skill import (
    GovernanceSnapshotSkill,
    IndustrySnapshotSkill,
)
from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import Uzi392FetcherRuntime


EXPECTED_UZI_SOURCE_IDS = {
    "em_push2", "em_quote", "em_data", "xq_api", "tencent_qt", "sina_quote", "cninfo", "hkexnews",
    "aastocks", "cls", "yicai", "wallstreetcn", "cfi", "hexun", "163money", "jrj", "investing",
    "mx_api", "akshare_lhb", "baostock", "yfinance", "ddgs", "yahoo_chart_v8", "tencent_hk_quote",
    "yahoo_equity_screener", "yahoo_fundamentals_timeseries", "yahoo_fx_chart",
    "coingecko_simple_price", "coingecko_markets", "okx_spot_tickers", "kucoin_stats", "kraken_trades",
    "gemini_ticker", "coinlore_tickers", "geckoterminal_networks", "jin10_flash", "em_kuaixun",
    "em_stock_ann", "qh99_inventory", "cfachina", "ths_news_today", "iwencai", "ths_f10", "xueqiu_f10",
    "legulegu", "stockstar", "futu", "yuncaijing", "guba_em_list", "jisilu", "fx678", "cmc", "sse",
    "szse", "csrc", "gov_cn", "miit", "ndrc", "samr", "shfe", "dce", "czce", "100ppi", "cnstock",
    "cs_cn", "stcn", "nbd", "pbc", "safe", "stats_gov", "chinamoney", "chinabond", "ine",
}


class UziSourceParityTests(unittest.TestCase):
    def test_governance_structured_sources_distinguish_verified_empty_from_failure(self) -> None:
        pledge_frame = pd.DataFrame({"股票代码": ["000001"], "质押比例": [1.2]})
        insider_frame = pd.DataFrame({"股票代码": ["000001"], "变动数量": [1000]})
        with (
            patch(
                "domain_packs.finance_ashare.skills.uzi_source_snapshot.skill.ak.stock_gpzy_pledge_ratio_em",
                return_value=pledge_frame,
            ),
            patch(
                "domain_packs.finance_ashare.skills.uzi_source_snapshot.skill.ak.stock_ggcg_em",
                return_value=insider_frame,
            ),
        ):
            result = GovernanceSnapshotSkill._structured_governance("600519.SH")

        self.assertEqual(result["observation_status"], "verified_empty")
        self.assertEqual(
            result["source_coverage"],
            {"pledge": "available", "insider_trades_1y": "available"},
        )
        self.assertEqual(result["pledge"], [])
        self.assertEqual(result["insider_trades_1y"], [])

    def test_capital_flow_fetcher_is_the_exact_pinned_uzi_source(self) -> None:
        source_path = (
            Path(__file__).resolve().parents[1]
            / "domain_packs"
            / "finance_ashare"
            / "vendor"
            / "uzi_3_9_2"
            / "scripts"
            / "fetch_capital_flow.py"
        )
        self.assertEqual(
            hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "d92fdac22e1fee2363e27e21984aa98159969eef1b5a8bf5fc9f48546b8ecfbe",
        )

    def test_capital_flow_snapshot_invokes_uzi_without_provider_substitution(self) -> None:
        upstream = {
            "ticker": "688256.SH",
            "data": {
                "holder_count_history": [{"股东户数": 12345}],
                "main_fund_flow_20d": [{"日期": "2026-08-21", "主力净流入": 10}],
            },
            "source": "akshare:multi",
            "fallback": False,
        }

        with patch.object(Uzi392FetcherRuntime, "collect", return_value=upstream) as collect:
            snapshot = snapshot_collector._run_uzi_fetcher_snapshot(
                query="分析寒武纪",
                symbol="688256",
                snapshot_type="capital_flow",
                provider_registry=None,
            )

        collect.assert_called_once_with(
            "12_capital_flow",
            ticker="688256",
            industry=None,
            dependencies=None,
            options=None,
        )
        self.assertEqual(snapshot["facts"]["_uzi_raw_result"], upstream)

    def test_capital_flow_snapshot_recovers_empty_uzi_main_flow_with_provider_chain(self) -> None:
        upstream = {
            "ticker": "688256.SH",
            "data": {
                "holder_count_history": [{"股东户数": 12345}],
                "main_fund_flow_20d": [],
            },
            "source": "akshare:multi",
            "fallback": False,
        }
        fallback = {
            "facts": {
                "availability": "available",
                "latest_flow": {"date": "2026-08-21", "main_net_inflow": 10},
                "recent_flows": [{"date": "2026-08-21", "main_net_inflow": 10}],
                "main_fund_flow_20d": [{"date": "2026-08-21", "main_net_inflow": 10}],
            },
            "source": {
                "provider_id": "efinance_capital_flow",
                "source_id": "efinance",
                "vendor": "efinance",
            },
            "source_trace": [
                {
                    "provider_id": "efinance_capital_flow",
                    "source_id": "efinance",
                    "status": "available",
                }
            ],
        }

        with (
            patch.object(Uzi392FetcherRuntime, "collect", return_value=upstream),
            patch(
                "domain_packs.finance_ashare.tools.snapshot_collector.CapitalFlowSnapshotSkill.run",
                return_value=fallback,
            ),
        ):
            snapshot = snapshot_collector._run_uzi_fetcher_snapshot(
                query="分析寒武纪",
                symbol="688256",
                snapshot_type="capital_flow",
                provider_registry=None,
            )

        self.assertEqual(snapshot["facts"]["_uzi_raw_result"], upstream)
        self.assertEqual(snapshot["facts"]["main_fund_flow_20d"], fallback["facts"]["main_fund_flow_20d"])
        self.assertEqual(snapshot["field_sources"]["main_fund_flow_20d"], "efinance_capital_flow")
        self.assertEqual(snapshot["source"]["provider_id"], "uzi_3_9_2.12_capital_flow")
        self.assertTrue(snapshot["fallback_applied"])
        self.assertFalse(snapshot["degraded"])

    def test_uzi_resource_sensitive_fetchers_share_one_serial_execution_lane(self) -> None:
        state_lock = Lock()
        active = 0
        maximum_active = 0

        def collect(_runtime, fetcher_id: str, **kwargs):
            nonlocal active, maximum_active
            with state_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                sleep(0.03)
                return {
                    "ticker": kwargs["ticker"],
                    "data": {"fetcher_id": fetcher_id},
                    "source": f"uzi:{fetcher_id}",
                    "fallback": False,
                }
            finally:
                with state_lock:
                    active -= 1

        snapshot_types = ("industry", "valuation", "capital_flow")
        with (
            patch.object(Uzi392FetcherRuntime, "collect", autospec=True, side_effect=collect),
            ThreadPoolExecutor(max_workers=3) as executor,
        ):
            futures = [
                executor.submit(
                    snapshot_collector._run_uzi_fetcher_snapshot,
                    query="分析寒武纪",
                    symbol="688256",
                    snapshot_type=snapshot_type,
                    provider_registry=None,
                )
                for snapshot_type in snapshot_types
            ]
            for future in futures:
                future.result()

        self.assertEqual(maximum_active, 1)
        self.assertEqual(snapshot_collector._UZI_MAX_WORKERS, 6)

    def test_uzi_providers_do_not_add_an_aggregate_collection_timeout(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        tools_root = repository_root / "domain_packs" / "finance_ashare" / "tools"
        for filename in ("financial_data_provider.yaml", "fund_data_provider.yaml"):
            with self.subTest(filename=filename):
                tool = yaml.safe_load((tools_root / filename).read_text(encoding="utf-8"))
                self.assertIsNone(tool.get("timeout_seconds"))

    def test_catalog_fields_are_deep_equal_to_pinned_upstream_registry(self) -> None:
        Uzi392FetcherRuntime(workspace_root=Path(__file__).resolve().parents[1] / "workspace")
        upstream = importlib.import_module("lib.data_source_registry")
        upstream_rows = tuple(
            (
                row.id,
                row.name_cn,
                row.base_url,
                tuple(row.markets),
                tuple(row.dims),
                row.tier,
                row.access,
                row.health,
            )
            for row in upstream.SOURCES
        )
        self.assertEqual(UZI_SOURCE_ROWS, upstream_rows)

    def test_catalog_matches_all_73_uzi_392_source_ids(self) -> None:
        catalog_ids = {str(row[0]) for row in UZI_SOURCE_ROWS}
        self.assertEqual(len(UZI_SOURCE_ROWS), 73)
        self.assertEqual(catalog_ids, EXPECTED_UZI_SOURCE_IDS)
        self.assertTrue(all(row[3] and row[4] for row in UZI_SOURCE_ROWS))
        normalized_rows = [
            [row[0], row[1], row[2], list(row[3]), list(row[4]), row[5], row[6], row[7]]
            for row in UZI_SOURCE_ROWS
        ]
        digest = hashlib.sha256(
            json.dumps(
                normalized_rows,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(digest, UZI_SOURCE_CATALOG_SHA256)
        registry_ids = {source.id for source in DataSourceRegistry().list_all() if source.upstream}
        self.assertEqual(registry_ids, EXPECTED_UZI_SOURCE_IDS)

        repository_root = Path(__file__).resolve().parents[1]
        config = yaml.safe_load(
            (repository_root / "workspace" / "config" / "data_sources.yml").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(config["catalog"]["source_count"], 73)
        self.assertEqual(
            config["catalog"]["commit"],
            "b004d7a988ca53a4805bf36784af091fa1209f22",
        )
        self.assertEqual(config["catalog"]["version"], "3.9.4")
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
