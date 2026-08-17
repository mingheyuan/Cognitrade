from __future__ import annotations

from pathlib import Path
import importlib
import hashlib
import json
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
from domain_packs.finance_ashare.skills.uzi_source_snapshot.skill import IndustrySnapshotSkill
from domain_packs.finance_ashare.vendor.uzi_3_9_2 import runtime as uzi_runtime
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
            "data": {"holder_count_history": [{"股东户数": 12345}]},
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

    def test_capital_flow_default_pipeline_imports_actual_vendored_module(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        Uzi392FetcherRuntime(workspace_root=repository_root / "workspace")
        module = importlib.import_module("fetch_capital_flow")
        expected_path = (
            repository_root
            / "domain_packs"
            / "finance_ashare"
            / "vendor"
            / "uzi_3_9_2"
            / "scripts"
            / "fetch_capital_flow.py"
        ).resolve()
        self.assertEqual(Path(module.__file__).resolve(), expected_path)
        self.assertEqual(
            Path(module.ds.__file__).resolve(),
            expected_path.parent / "lib" / "data_sources.py",
        )

        upstream = {
            "ticker": "600519.SH",
            "data": {
                "main_fund_flow_20d": [{"主力净流入-净额": 1000}],
                "northbound": "",
            },
            "source": "akshare:multi",
            "fallback": False,
        }
        registry = importlib.import_module("lib.pipeline.fetchers.registry")
        fetcher = registry.get_fetcher("12_capital_flow")
        self.assertIsNotNone(fetcher)
        with patch.object(module, "main", return_value=upstream) as main:
            result = fetcher.fetch("600519").to_dict()

        self.assertEqual(
            result["data"]["main_fund_flow_20d"],
            upstream["data"]["main_fund_flow_20d"],
        )
        self.assertIsNone(result["data"]["northbound"])
        self.assertEqual(result["source"], "legacy:fetch_capital_flow")
        self.assertEqual(result["_pipeline"]["dim_key"], "12_capital_flow")
        self.assertEqual(result["_pipeline"]["quality"], "partial")
        main.assert_called_once_with("600519")

    def test_fetcher_mapping_runtime_snapshot_and_ledger_are_cross_checked(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        Uzi392FetcherRuntime(workspace_root=repository_root / "workspace")
        pipeline_registry = importlib.import_module("lib.pipeline.fetchers.registry")
        migration_root = (
            repository_root / "domain_packs" / "finance_ashare" / "migration"
        )
        mapping = yaml.safe_load(
            (migration_root / "uzi_source_mapping.yaml").read_text(encoding="utf-8")
        )
        ledger = yaml.safe_load(
            (migration_root / "uzi_3_9_2_capability_ledger.yaml").read_text(
                encoding="utf-8"
            )
        )
        mapping_by_id = {row["source_id"]: row for row in mapping["fetchers"]}
        ledger_by_id = {row["id"]: row for row in ledger["fetchers"]}
        snapshot_type_by_id = {
            fetcher_id: snapshot_type
            for snapshot_type, fetcher_id in snapshot_collector._UZI_FETCHER_BY_SNAPSHOT_TYPE.items()
        }

        expected_ids = set(uzi_runtime.UZI_FETCHER_IDS)
        self.assertEqual(set(uzi_runtime._FETCHERS), expected_ids)
        self.assertEqual(set(pipeline_registry.FETCHER_REGISTRY), expected_ids)
        self.assertEqual(set(snapshot_type_by_id), expected_ids)
        self.assertEqual(set(mapping_by_id), expected_ids)
        self.assertEqual(set(ledger_by_id), expected_ids)
        self.assertEqual(mapping["source"]["commit"], uzi_runtime.UZI_SOURCE_COMMIT)
        self.assertEqual(ledger["upstream"]["commit"], uzi_runtime.UZI_SOURCE_COMMIT)

        vendor_root = repository_root / mapping["source"]["vendored_root"]
        source_prefix = Path("skills/deep-analysis")
        acceptance_refs: set[str] = set()
        for fetcher_id in uzi_runtime.UZI_FETCHER_IDS:
            with self.subTest(fetcher_id=fetcher_id):
                mapping_row = mapping_by_id[fetcher_id]
                ledger_row = ledger_by_id[fetcher_id]
                snapshot_type = snapshot_type_by_id[fetcher_id]
                skill = snapshot_collector._SYMBOL_SKILLS[snapshot_type]
                source_path = Path(mapping_row["source_path"])
                vendored_path = vendor_root / source_path.relative_to(source_prefix)

                self.assertTrue(vendored_path.is_file(), vendored_path)
                self.assertEqual(
                    uzi_runtime._FETCHERS[fetcher_id].module,
                    source_path.stem,
                )
                pipeline_fetcher = pipeline_registry.get_fetcher(fetcher_id)
                self.assertIsNotNone(pipeline_fetcher)
                self.assertEqual(pipeline_fetcher._legacy_module, source_path.stem)
                actual_module = importlib.import_module(source_path.stem)
                self.assertEqual(
                    Path(actual_module.__file__).resolve(),
                    vendored_path.resolve(),
                )
                self.assertEqual(mapping_row["target_skill"], skill.skill_name)
                self.assertEqual(ledger_row["target"], skill.skill_name)
                self.assertEqual(mapping_row["migration_status"], "source_parity")
                self.assertEqual(ledger_row["status"], "source_parity")
                self.assertEqual(
                    mapping_row["source_commit"], uzi_runtime.UZI_SOURCE_COMMIT
                )
                acceptance_refs.add(mapping_row["invocation_acceptance"])

        for reference in acceptance_refs:
            with self.subTest(invocation_acceptance=reference):
                module_name, class_name, method_name = reference.rsplit(".", 2)
                acceptance_module = importlib.import_module(module_name)
                acceptance_class = getattr(acceptance_module, class_name)
                self.assertTrue(callable(getattr(acceptance_class, method_name)))

    def test_fund_holder_snapshot_applies_the_exact_profile_statistics_scope(self) -> None:
        upstream = {
            "ticker": "688256.SH",
            "data": {"fund_managers": []},
            "source": "akshare:fund_portfolio_hold_em",
            "fallback": False,
        }

        with patch.object(Uzi392FetcherRuntime, "collect", return_value=upstream) as collect:
            snapshot_collector._run_uzi_fetcher_snapshot(
                query="分析寒武纪",
                symbol="688256",
                snapshot_type="fund_holders",
                provider_registry=None,
                research_profile="deep",
            )

        collect.assert_called_once_with(
            "6_fund_holders",
            ticker="688256",
            industry=None,
            dependencies=None,
            options={"fund_stats_top_n": 100},
        )

    def test_uzi_default_pipeline_uses_hard_timeout_process_jobs_and_serial_group(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        Uzi392FetcherRuntime(workspace_root=repository_root / "workspace")
        pipeline_collect = importlib.import_module("lib.pipeline.collect")
        pipeline_registry = importlib.import_module("lib.pipeline.fetchers.registry")
        pipeline_schema = importlib.import_module("lib.pipeline.schema")
        fetch_valuation = importlib.import_module("fetch_valuation")
        fetch_capital_flow = importlib.import_module("fetch_capital_flow")
        def main(*args):
            return {
                "ticker": "688256.SH",
                "data": {"args": list(args)},
                "source": "uzi:actual-module-main",
                "fallback": False,
            }

        class BasicFetcher:
            def fetch(self, ticker):
                return pipeline_schema.DimResult(
                    dim_key="0_basic",
                    data={"name": "寒武纪", "price": 100, "industry": "半导体"},
                    source="fixture:basic",
                    quality=pipeline_schema.Quality.FULL,
                )

        actual_fetchers = {
            "10_valuation": pipeline_registry.get_fetcher("10_valuation"),
            "12_capital_flow": pipeline_registry.get_fetcher("12_capital_flow"),
        }

        def get_fetcher(dim_key: str):
            if dim_key == "0_basic":
                return BasicFetcher()
            return actual_fetchers[dim_key]

        observed_calls = []

        def run_jobs(jobs, *, max_workers, overall_timeout):
            observed_calls.append((list(jobs), max_workers, overall_timeout))
            return [
                pipeline_collect.ProcessOutcome(
                    key=job.key,
                    value=job.target(*job.args, **job.kwargs),
                )
                for job in jobs
            ]

        with (
            patch.object(fetch_valuation, "main", side_effect=main) as valuation_main,
            patch.object(fetch_capital_flow, "main", side_effect=main) as capital_flow_main,
            patch.object(
                pipeline_collect,
                "FETCHER_REGISTRY",
                {"0_basic": object(), **actual_fetchers},
            ),
            patch.object(pipeline_collect, "DEPENDENT_DIMS", set()),
            patch.object(pipeline_collect, "get_fetcher", side_effect=get_fetcher),
            patch.object(pipeline_collect, "_mini_racer_disabled", return_value=False),
            patch.object(pipeline_collect, "run_process_jobs", side_effect=run_jobs),
            patch("builtins.print"),
        ):
            result = pipeline_collect.collect("688256", max_workers=2)

        valuation_main.assert_called_once_with("688256")
        capital_flow_main.assert_called_once_with("688256")
        jobs = [job for call in observed_calls for job in call[0]]
        mini_jobs = [job for job in jobs if job.key in {"10_valuation", "12_capital_flow"}]
        self.assertEqual({job.serial_group for job in mini_jobs}, {"mini_racer"})
        self.assertEqual({job.timeout_sec for job in jobs}, {120.0})
        self.assertEqual([call[2] for call in observed_calls], [120.0, 300.0, 300.0])
        self.assertEqual(
            set(result),
            {"0_basic", "10_valuation", "12_capital_flow"},
        )

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

        a_share_suffixes = master.resolve(
            query="分析 600519.SH 和 000001.SZ",
            supplied_symbols=["688256.SH"],
            allow_network=False,
        )
        self.assertEqual(a_share_suffixes.symbols, ["688256", "600519", "000001"])
        self.assertNotIn("SH", a_share_suffixes.symbols)
        self.assertNotIn("SZ", a_share_suffixes.symbols)

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
