from __future__ import annotations

import json
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
import unittest

import jsonschema
import yaml

from fastapi.testclient import TestClient

from domain_packs.finance_ashare.subagents.uzi_methodology import (
    build_features,
    build_panel_review,
    build_risk_scan,
    build_valuation_model,
    select_profile,
)
from domain_packs.finance_ashare.subagents.runtime import collect_snapshot
from domain_packs.finance_ashare.subagents.uzi_advanced_methodology import (
    build_deep_analysis,
    build_dimension_coverage,
    build_institutional_analysis,
    build_research_workflow,
)
from domain_packs.finance_ashare.vendor.uzi_3_9_2 import verify_vendored_uzi_integrity
from domain_packs.finance_ashare.tools.snapshot_collector import collect_snapshots
import domain_packs.finance_ashare.tools.snapshot_collector as snapshot_collector
from research_system.api.app import create_app
from research_system.api.config import ApiConfig
from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.planning.capability_planner import CapabilityPlanner
from research_system.runtime.platform.settings import Settings
from research_system.schemas.domain_pack import CapabilityPlanningRequest


def _snapshots() -> list[dict]:
    fixture = Path(__file__).resolve().parent / "fixtures" / "uzi_finance_snapshot_replay.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    return list(payload["snapshots"])


class UziFinancePackTests(unittest.TestCase):
    def test_main_agent_synthesis_contract_accepts_full_artifact_lineage(self) -> None:
        schema = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "domain_packs"
                / "finance_ashare"
                / "artifact_schemas"
                / "main_agent_synthesis.json"
            ).read_text(encoding="utf-8")
        )

        jsonschema.validate(
            {
                "conclusion": "结构化结论",
                "assistant_message": "面向用户的答复",
                "follow_up": ["复核披露期"],
                "source_artifact_id": "report-1",
                "source_artifact_ids": ["report-1", "valuation-1", "risk-1"],
                "model_generated": True,
            },
            schema,
        )

    def test_empty_provider_results_materialize_auditable_gap_snapshots(self) -> None:
        query = SimpleNamespace(
            artifact_id="normalized-query",
            content={"query": "分析 600000", "symbols": ["600000"]},
        )
        context = SimpleNamespace(
            node=SimpleNamespace(
                inputs={
                    "intent": "stock_check",
                    "symbols": ["600000"],
                    "research_profile": "lite",
                }
            ),
            require_artifact=lambda artifact_type: query,
            call_tool=lambda _name, _arguments: SimpleNamespace(
                status="completed",
                output={
                    "snapshots": [],
                    "symbols": ["600000"],
                    "candidate_universe": [],
                    "collection": {"quality_status": "data_gap"},
                },
                source_refs=[],
            ),
        )

        result = collect_snapshot(context)

        snapshots = [
            draft
            for draft in result.artifacts
            if draft.artifact_type == "snapshot"
        ]
        self.assertEqual(
            {draft.content["snapshot_type"] for draft in snapshots},
            {
                "market",
                "fundamental",
                "kline",
                "valuation",
                "governance",
                "events",
                "lhb",
            },
        )
        self.assertTrue(all(draft.content["quality_status"] == "data_gap" for draft in snapshots))
        self.assertTrue(
            all("None" not in draft.content.get("degrade_reasons", []) for draft in snapshots)
        )

    def test_uzi_source_mapping_is_complete_and_registered(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        mapping_path = (
            repository_root
            / "domain_packs"
            / "finance_ashare"
            / "migration"
            / "uzi_source_mapping.yaml"
        )
        mapping = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
        schema_path = mapping_path.with_name("uzi_source_mapping.schema.json")
        jsonschema.validate(
            mapping,
            json.loads(schema_path.read_text(encoding="utf-8")),
        )
        fetchers = list(mapping["fetchers"])
        self.assertEqual(len(fetchers), 22)
        self.assertEqual(
            {row["source_id"] for row in fetchers},
            {
                "0_basic", "1_financials", "2_kline", "3_macro", "4_peers", "5_chain",
                "6_research", "7_industry", "8_materials", "9_futures", "10_valuation",
                "11_governance", "12_capital_flow", "13_policy", "14_moat", "15_events",
                "16_lhb", "17_sentiment", "18_trap", "19_contests", "fund_holders", "similar_stocks",
            },
        )
        self.assertEqual(mapping["source"]["commit"], "0acf25122cabf493e7bc442faff62ceceb7f2f02")
        self.assertEqual(mapping["profiles"]["lite"]["panel_seats"], 10)
        self.assertEqual(mapping["profiles"]["medium"]["panel_seats"], 51)
        self.assertEqual(mapping["profiles"]["deep"]["panel_seats"], 66)
        required_mapping_fields = {
            "source_commit",
            "source_path",
            "target_skill",
            "migration_status",
            "result_equivalence",
            "invocation_acceptance",
        }
        self.assertTrue(all(required_mapping_fields.issubset(row) for row in fetchers))
        self.assertEqual(mapping["source_registry"]["source_count"], 73)
        self.assertEqual(mapping["contract"]["returned_object"], "exact_upstream_identity")
        self.assertEqual(mapping["contract"]["external_checkout_runtime"], "forbidden")

    def test_uzi_source_and_replay_fingerprints_are_verified(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        mapping_path = (
            repository_root
            / "domain_packs"
            / "finance_ashare"
            / "migration"
            / "uzi_source_mapping.yaml"
        )
        mapping = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
        source = mapping["source"]
        self.assertRegex(source["license_sha256"], r"^[A-Fa-f0-9]{64}$")
        vendor_root = repository_root / source["vendored_root"]
        license_hash = hashlib.sha256((vendor_root / "LICENSE").read_bytes()).hexdigest()
        self.assertEqual(license_hash, source["license_sha256"])
        integrity = verify_vendored_uzi_integrity()
        self.assertTrue(integrity["valid"], integrity)
        self.assertEqual(integrity["source_commit"], source["commit"])
        self.assertEqual(integrity["fetcher_count"], 22)
        self.assertEqual(integrity["command_count"], 20)

    def test_unconfigured_uzi_sources_are_explicit_degraded_snapshots(self) -> None:
        class DegradedSkill:
            def __init__(self, snapshot_type: str) -> None:
                self.snapshot_type = snapshot_type

            def run(self, *, query: str, symbol: str | None = None) -> dict:
                return {
                    "snapshot_type": self.snapshot_type,
                    "symbol": symbol,
                    "facts": {"availability": "data_gap"},
                    "degraded": True,
                    "degrade_reason": "provider_not_configured",
                }

        fake_skills = {
            name: type(f"{name.title()}DegradedSkill", (DegradedSkill,), {})
            for name in ("chain", "materials", "trap")
        }
        for name, skill_cls in fake_skills.items():
            skill_cls.__init__ = lambda self, _name=name: DegradedSkill.__init__(self, _name)
        with patch.dict(snapshot_collector._SYMBOL_SKILLS, fake_skills):
            rows = collect_snapshots(
                query="分析 300750",
                symbols=["300750"],
                snapshot_types=list(fake_skills),
            )
        self.assertEqual({row["snapshot_type"] for row in rows}, set(fake_skills))
        self.assertTrue(all(row["degraded"] for row in rows))
        self.assertTrue(all(row["facts"]["availability"] == "data_gap" for row in rows))

    def test_snapshot_bundle_promotes_per_dimension_quality(self) -> None:
        rows = [
            {
                "snapshot_type": "market",
                "symbol": "300750",
                "facts": {"availability": "available"},
            },
            {
                "snapshot_type": "peers",
                "symbol": "300750",
                "facts": {"availability": "data_gap", "data_gap": ["peer_group_missing"]},
                "degraded": True,
                "degrade_reason": "peer_group_missing",
            },
        ]
        result = snapshot_collector._bundle_result(
            snapshots=rows,
            symbols=["300750"],
            requested=["market", "peers"],
            universe_rows=[],
            universe_source="structured_symbols",
            enrichment_targets=[],
            query="分析 300750",
            research_profile="lite",
            settings=Settings.from_env(Path(__file__).resolve().parents[1]),
        )
        self.assertEqual(result["collection"]["quality_status"], "degraded")
        self.assertEqual(result["collection"]["quality_counts"], {"available": 1, "data_gap": 1})
        self.assertEqual(result["snapshots"][1]["quality_status"], "data_gap")

    def test_snapshot_cache_reuses_dimensions_and_refreshes_only_expired_one(self) -> None:
        calls = {"market": 0, "fundamental": 0}

        def fake_skill(snapshot_type: str):
            class Skill:
                def run(self, *, query: str, symbol: str | None = None) -> dict:
                    calls[snapshot_type] += 1
                    return {
                        "snapshot_type": snapshot_type,
                        "symbol": symbol,
                        "facts": {"availability": "available", "value": calls[snapshot_type]},
                        "source": {"provider_id": "test_provider"},
                    }

            return Skill

        with TemporaryDirectory() as temporary_directory:
            cache_root = Path(temporary_directory)
            with patch.dict(
                snapshot_collector._SYMBOL_SKILLS,
                {"market": fake_skill("market"), "fundamental": fake_skill("fundamental")},
            ):
                first = collect_snapshots(
                    query="分析 300750",
                    symbols=["300750"],
                    snapshot_types=["market", "fundamental"],
                    cache_root=cache_root,
                    research_profile="lite",
                )
                second = collect_snapshots(
                    query="分析 300750",
                    symbols=["300750"],
                    snapshot_types=["market", "fundamental"],
                    cache_root=cache_root,
                    research_profile="lite",
                )

                market_path = snapshot_collector._snapshot_cache_path(
                    cache_root=cache_root,
                    snapshot_type="market",
                    target="300750",
                    profile="lite",
                )
                self.assertIsNotNone(market_path)
                payload = json.loads(market_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["cache_layer"], "provider_raw")
                self.assertEqual(payload["snapshot_type"], "market")
                self.assertEqual(payload["cache_context"]["profile"], "lite")
                payload["expires_at"] = "2000-01-01T00:00:00+00:00"
                market_path.write_text(json.dumps(payload), encoding="utf-8")

                third = collect_snapshots(
                    query="分析 300750",
                    symbols=["300750"],
                    snapshot_types=["market", "fundamental"],
                    cache_root=cache_root,
                    research_profile="lite",
                )

        self.assertEqual(calls, {"market": 2, "fundamental": 1})
        self.assertEqual(second[0]["facts"]["value"], first[0]["facts"]["value"])
        self.assertEqual(third[1]["facts"]["value"], first[1]["facts"]["value"])

    def test_dimension_coverage_separates_raw_sources_from_derived_products(self) -> None:
        coverage = build_dimension_coverage(
            _snapshots(),
            ["600000"],
            select_profile({"research_profile": "medium"}),
        )

        self.assertEqual(coverage["dimension_count"], 22)
        self.assertEqual(coverage["derived_dimension_count"], 3)
        self.assertEqual(coverage["total_dimension_count"], 25)
        rows = {row["dimension_id"]: row for row in coverage["coverage"]["600000"]}
        self.assertEqual(rows["10_valuation"]["status"], "data_gap")
        self.assertEqual(rows["11_governance"]["status"], "data_gap")
        self.assertEqual(rows["20_valuation_models"]["layer"], "derived")

    def test_financial_mapping_marks_proxy_dcf_inputs_explicitly(self) -> None:
        rows = [
            {
                "snapshot_type": "market",
                "symbol": "300750",
                "facts": {
                    "market_overview": {
                        "latest_price": 100.0,
                        "market_cap": 100_000_000_000,
                        "total_shares": 1_000_000_000,
                    }
                },
                "source": {"vendor": "test", "provider_id": "market"},
            },
            {
                "snapshot_type": "fundamental",
                "symbol": "300750",
                "facts": {
                    "financial_abstract": {
                        "营业收入": 10_000_000_000,
                        "净利润": 1_000_000_000,
                        "经营活动产生的现金流量净额": 1_500_000_000,
                        "资产总计": 20_000_000_000,
                        "负债合计": 8_000_000_000,
                        "货币资金": 3_000_000_000,
                        "短期借款": 1_000_000_000,
                        "长期借款": 1_000_000_000,
                        "资产负债率": 0.4,
                    }
                },
                "source": {"vendor": "test", "provider_id": "fundamental"},
            },
        ]

        features = build_features(rows, "300750")
        valuation = build_valuation_model(
            rows,
            ["300750"],
            select_profile({"research_profile": "lite"}),
        )["models"][0]

        self.assertEqual(features["revenue_latest_yi"], 100.0)
        self.assertEqual(features["total_assets_yi"], 200.0)
        self.assertEqual(features["total_liabilities_yi"], 80.0)
        self.assertEqual(features["cash_yi"], 30.0)
        self.assertEqual(valuation["status"], "data_gap")
        self.assertIn("fcf_missing", valuation["data_gap"])
        self.assertNotIn("fcf_approximated_from_revenue_margin", valuation["assumption_flags"])
        self.assertEqual(features["operating_cash_flow_yi"], 15.0)
        self.assertIsNone(features["fcf_latest_yi"])
        self.assertNotIn("total_assets_missing", valuation["warnings"])

    def test_uzi_rule_features_are_derived_from_verified_snapshots(self) -> None:
        start = date(2025, 1, 1)
        bars = [
            {
                "date": (start + timedelta(days=index)).isoformat(),
                "open": 9.98 + index * 0.05,
                "high": 10.1 + index * 0.05,
                "low": 9.9 + index * 0.05,
                "close": 10 + index * 0.05,
                "volume": 1_000_000 + index * 1_000,
            }
            for index in range(300)
        ]
        annual = [
            {"报告期": year, "营业总收入": f"{revenue}亿", "净利润": f"{profit}亿", "净资产收益率": f"{roe}%"}
            for year, revenue, profit, roe in (
                (2021, 80, 8, 12),
                (2022, 100, 10, 14),
                (2023, 120, 12, 16),
                (2024, 150, 15, 18),
                (2025, 200, 25, 20),
            )
        ]
        valuation_history = [
            {"数据日期": (start + timedelta(days=index)).isoformat(), "PE(TTM)": 10 + index / 100}
            for index in range(300)
        ]
        rows = [
            {
                "snapshot_type": "market",
                "symbol": "688012",
                "snapshot_date": "2026-08-07",
                "facts": {"market_overview": {"latest_price": 25, "market_cap": 50_000_000_000, "pe_ttm": 20, "pb": 2, "ps": 3, "valuation_history": valuation_history}},
                "source": {"vendor": "fixture"},
            },
            {
                "snapshot_type": "fundamental",
                "symbol": "688012",
                "snapshot_date": "2026-08-07",
                "facts": {
                    "financial_abstract": {"营业总收入": "200亿", "净利润": "25亿", "销售毛利率": "45%", "流动比率": 2.5, "净资产收益率": "20%", "资产负债率": "30%", "基本每股收益": 2},
                    "financial_abstract_history": annual,
                    "financial_abstract_new": {"operating_income_total": {"value": 5_000_000_000}},
                    "financial_benefit_new": {"research_and_development_expenses": {"value": 500_000_000}},
                    "financial_debt_new": {"assets_total": {"value": 30_000_000_000}, "total_debt": {"value": 9_000_000_000}, "cash": {"value": 5_000_000_000}, "long_term_loan": {"value": 1_000_000_000}, "lease_debt": {"value": 100_000_000}, "bonds_payable": {"value": "", "report_date": "2026-03-31"}},
                    "financial_cash_new": {"act_cash_flow_net": {"value": 800_000_000}, "pay_fixed_assets_etc_cash": {"value": 200_000_000}},
                },
                "source": {"vendor": "fixture"},
            },
            {"snapshot_type": "kline", "symbol": "688012", "snapshot_date": "2026-08-07", "facts": {"history": {"bars": bars}}, "source": {"vendor": "fixture"}},
            {"snapshot_type": "sentiment", "symbol": "688012", "snapshot_date": "2026-08-07", "facts": {"availability": "available", "hot_rank_latest": [{"item": "marketAllCount", "value": 1000}, {"item": "calcTime", "value": "2026-08-07 10:00:00"}, {"item": "rank", "value": 100}]}, "source": {"vendor": "fixture"}},
            {"snapshot_type": "lhb", "symbol": "688012", "snapshot_date": "2026-08-07", "facts": {"availability": "available", "lhb_statistics": [{"上榜次数": 2}]}, "source": {"vendor": "fixture"}},
            {"snapshot_type": "industry", "symbol": "688012", "snapshot_date": "2026-08-07", "facts": {"availability": "available", "industry": "半导体设备"}, "source": {"vendor": "fixture"}},
            {"snapshot_type": "research", "symbol": "688012", "snapshot_date": "2026-08-07", "facts": {"availability": "available", "reports": [{"股票简称": "中微公司", "机构": "A", "东财评级": "买入", "2026-盈利预测-收益": 3}, {"股票简称": "中微公司", "机构": "B", "东财评级": "增持", "2026-盈利预测-收益": 3.4}]}, "source": {"vendor": "fixture"}},
            {"snapshot_type": "notice", "symbol": "688012", "snapshot_date": "2026-08-07", "facts": {"recent_notices": [{"公告标题": "获得重大订单"}], "notice_count": 1}, "source": {"vendor": "fixture"}},
        ]

        features = build_features(rows, "688012")

        self.assertEqual(features["name"], "中微公司")
        self.assertEqual(features["revenue_latest_yi"], 200)
        # Latest UZI v3.9.4 uses the reported total-liabilities bridge before
        # narrower interest-bearing components so DCF does not silently treat
        # the rest of the balance sheet as zero.
        self.assertAlmostEqual(features["total_debt_yi"], 90)
        self.assertEqual(features["stage_num"], 2)
        self.assertTrue(features["price_above_ma200"])
        self.assertAlmostEqual(features["sentiment_heat"], 90.09, places=2)
        self.assertEqual(features["lhb_30d_count"], 2)
        self.assertEqual(features["research_coverage"], 2)
        self.assertEqual(features["buy_rating_pct"], 100)
        self.assertAlmostEqual(features["rd_intensity"], 10)
        self.assertAlmostEqual(features["fcf_margin"], 12)
        self.assertTrue(features["fcf_positive"])
        self.assertIsNotNone(features["revenue_growth_3y_cagr"])
        self.assertEqual(features["roe_5y_above_15"], 3)
        self.assertIsNotNone(features["pe_quantile_5y"])
        self.assertTrue(features["ai_chain_hit"])
        self.assertIn("stage_num=Weinstein", " ".join(features["feature_derivations"]))

    def test_uzi_market_currency_is_explicit_for_a_h_us_and_global_symbols(self) -> None:
        cases = {
            "600519": ("A", "CN", "CNY", "¥"),
            "00700.HK": ("H", "HK", "HKD", "HK$"),
            "AAPL": ("U", "US", "USD", "$"),
            "7203.T": ("JP", "JP", "JPY", "JP¥"),
        }
        for symbol, expected in cases.items():
            features = build_features(
                [{
                    "snapshot_type": "market",
                    "symbol": symbol,
                    "snapshot_date": "2026-08-07",
                    "facts": {"market_overview": {"latest_price": 10}},
                    "source": {"vendor": "fixture"},
                }],
                symbol,
            )
            self.assertEqual(
                (features["market"], features["country"], features["currency"], features["currency_symbol"]),
                expected,
            )

    def test_uzi_rule_features_do_not_relabel_fund_products_as_managers(self) -> None:
        rows = _snapshots() + [
            {"snapshot_type": "fund_holders", "symbol": "600000", "facts": {"availability": "available", "holders": [{"基金名称": "A"}], "active_funds_count": 999}}
        ]

        features = build_features(rows, "600000")

        self.assertIsNone(features["fund_manager_count"])
        self.assertIn("fund_manager_count", features["data_gap"])

    def test_uzi_lhb_count_excludes_dated_rows_older_than_30_days(self) -> None:
        rows = _snapshots() + [
            {
                "snapshot_type": "lhb",
                "symbol": "600000",
                "snapshot_date": "2026-08-07",
                "facts": {
                    "availability": "available",
                    "lhb_statistics": [
                        {"trade_date": "20260801"},
                        {"trade_date": "20260601"},
                    ],
                },
            }
        ]

        features = build_features(rows, "600000")

        self.assertEqual(features["lhb_30d_count"], 1)

    def test_uzi_methodology_replay_matches_versioned_dcf_baseline(self) -> None:
        fixture = json.loads(
            (Path(__file__).resolve().parent / "fixtures" / "uzi_methodology_baseline.json")
            .read_text(encoding="utf-8")
        )
        rows = list(fixture["snapshots"])
        profile = select_profile({"research_profile": "lite"})
        model = build_valuation_model(rows, [fixture["symbol"]], profile)["models"][0]
        reference = fixture["uzi_reference"]

        self.assertEqual(profile["source_commit"], fixture["source_commit"])
        self.assertAlmostEqual(
            model["fair_value"],
            reference["dcf_intrinsic_per_share"],
            delta=0.5,
        )
        self.assertAlmostEqual(model["assumptions"]["wacc"], reference["dcf_wacc"], places=3)
        self.assertEqual(len(model["sensitivity"]), 25)
        self.assertEqual(
            len(build_panel_review(rows, [fixture["symbol"]], profile)["votes"]),
            fixture["uzi_reference"]["panel_seats"]["lite"],
        )

    def test_method_specific_request_replaces_default_report_outputs(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        planner = CapabilityPlanner(
            DomainPackRegistry.load(repository_root / "domain_packs")
        )
        graph = planner.build_graph(
            CapabilityPlanningRequest(
                domain_id="finance_ashare",
                objective="只做 300750 的 DCF",
                available_artifact_types=["user_request"],
                required_artifact_types=["uzi_native_method_result"],
                metadata={
                    "intent": "uzi_dcf",
                    "required_outputs_source": "intent_output_contract",
                    "intent_inputs": {
                        "intent": "uzi_dcf",
                        "symbols": ["300750"],
                    },
                },
            )
        )
        capabilities = {node.capability for node in graph.nodes if node.capability}

        self.assertIn("finance.uzi.native.dcf", capabilities)
        self.assertNotIn("finance.uzi.valuation.evaluate", capabilities)
        self.assertNotIn("finance.uzi.report.compose", capabilities)
        self.assertNotIn("finance.report.compose", capabilities)

    def test_specialized_intents_select_profile_and_quality_gate(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        planner = CapabilityPlanner(
            DomainPackRegistry.load(repository_root / "domain_packs")
        )
        cases = {
            "stock_screen": ("lite", "screening_result", "finance.screening.quality", 120),
            "sector_research": ("medium", "sector_analysis", "finance.sector.analysis", 480),
            "review_thesis": ("deep", "thesis_review", "finance.thesis.review", 1200),
        }
        for intent, (profile, artifact_type, gate_id, estimate) in cases.items():
            with self.subTest(intent=intent):
                graph = planner.build_graph(
                    CapabilityPlanningRequest(
                        domain_id="finance_ashare",
                        objective=f"Run {intent}",
                        available_artifact_types=["user_request"],
                        required_artifact_types=["research_report"],
                        metadata={
                            "intent": intent,
                            "intent_inputs": {"intent": intent, "research_profile": profile},
                        },
                    )
                )
                self.assertEqual(graph.metadata["selected_research_profile"], profile)
                self.assertIn(artifact_type, graph.metadata["required_artifact_types"])
                self.assertIn(gate_id, graph.governance.required_gate_ids)
                self.assertEqual(graph.metadata["deadline_policy"], "unbounded")
                self.assertIsNone(graph.governance.timeout_seconds)
                self.assertEqual(
                    graph.metadata["profile_policy"]["estimated_duration_seconds"],
                    estimate,
                )
                lazy_graph = planner.build_lazy_graph(
                    CapabilityPlanningRequest(
                        domain_id="finance_ashare",
                        objective=f"Run {intent}",
                        available_artifact_types=["user_request"],
                        required_artifact_types=["research_report"],
                        metadata={
                            "intent": intent,
                            "intent_inputs": {"intent": intent, "research_profile": profile},
                        },
                    )
                )
                self.assertEqual(lazy_graph.metadata["deadline_policy"], "unbounded")
                self.assertIsNone(lazy_graph.governance.timeout_seconds)
                if intent == "stock_screen":
                    self.assertNotIn(
                        "valuation_model",
                        graph.metadata["required_artifact_types"],
                    )

    def test_uzi_graph_consumes_independent_snapshot_refs(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        planner = CapabilityPlanner(
            DomainPackRegistry.load(repository_root / "domain_packs")
        )
        graph = planner.build_graph(
            CapabilityPlanningRequest(
                domain_id="finance_ashare",
                objective="使用已有快照做 300750 的 DCF",
                available_artifact_types=["user_request", "snapshot"],
                available_artifact_refs=[
                    {
                        "artifact_id": "snapshot-market",
                        "artifact_type": "snapshot",
                        "schema_version": "1",
                        "dimension": "market",
                        "scope": "symbol",
                        "symbol": "300750",
                        "quality_status": "available",
                    },
                    {
                        "artifact_id": "snapshot-fundamental",
                        "artifact_type": "snapshot",
                        "schema_version": "1",
                        "dimension": "fundamental",
                        "scope": "symbol",
                        "symbol": "300750",
                        "quality_status": "available",
                    },
                ],
                required_artifact_types=["uzi_native_method_result"],
                metadata={
                    "intent": "uzi_dcf",
                    "intent_inputs": {
                        "intent": "uzi_dcf",
                        "symbols": ["300750"],
                    },
                },
            )
        )
        valuation = next(
            node for node in graph.nodes
            if node.capability == "finance.uzi.native.dcf"
        )
        self.assertEqual(valuation.consumes[0].artifact_type, "snapshot")
        self.assertTrue(valuation.consumes[0].multiple)
        self.assertNotIn("stock_snapshot_bundle", [port.artifact_type for port in valuation.consumes])
        self.assertEqual(
            valuation.input_artifact_ids,
            ["snapshot-market", "snapshot-fundamental"],
        )

    def test_pack_method_catalog_selects_registered_advanced_method(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        planner = CapabilityPlanner(DomainPackRegistry.load(repository_root / "domain_packs"))
        graph = planner.build_graph(
            CapabilityPlanningRequest(
                domain_id="finance_ashare",
                objective="只做 300750 的 LBO",
                available_artifact_types=["user_request"],
                required_artifact_types=["uzi_native_method_result"],
                metadata={
                    "intent": "uzi_lbo",
                    "required_outputs_source": "intent_output_contract",
                    "intent_inputs": {
                        "intent": "uzi_lbo",
                        "symbols": ["300750"],
                    },
                },
            )
        )
        capabilities = {node.capability for node in graph.nodes if node.capability}
        self.assertIn("finance.uzi.native.quick_lbo", capabilities)
        self.assertNotIn("finance.uzi.method.quick_lbo", capabilities)
        self.assertNotIn("finance.uzi.panel.review", capabilities)
        self.assertIn("capability_selections", graph.metadata)

    def test_planner_rejects_degraded_bundle_as_reusable_fact(self) -> None:
        artifact = SimpleNamespace(
            status="active",
            artifact_type="stock_snapshot_bundle",
            content={
                "collection": {"quality_status": "degraded"},
                "snapshots": [{"quality_status": "available"}],
            },
            metadata={},
            created_at=datetime.now(timezone.utc),
        )
        self.assertNotIn(
            "stock_snapshot_bundle",
            CapabilityPlanner.usable_artifact_types([artifact]),
        )

    def test_profile_outputs_control_the_materialized_capability_graph(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        planner = CapabilityPlanner(
            DomainPackRegistry.load(repository_root / "domain_packs")
        )

        lite = planner.build_graph(
            CapabilityPlanningRequest(
                domain_id="finance_ashare",
                objective="速判检查 600000",
                available_artifact_types=["user_request"],
                required_artifact_types=["research_report"],
                metadata={
                    "intent": "stock_check",
                    "intent_inputs": {
                        "intent": "stock_check",
                        "research_profile": "lite",
                    },
                },
            )
        )
        lite_capabilities = {node.capability for node in lite.nodes if node.capability}
        self.assertIn("finance.uzi.native.analyze_stock", lite_capabilities)
        self.assertIn("finance.uzi.native.report.compose", lite_capabilities)
        self.assertNotIn("finance.uzi.valuation.evaluate", lite_capabilities)
        self.assertNotIn("finance.uzi.report.compose", lite_capabilities)

        dcf_only = planner.build_graph(
            CapabilityPlanningRequest(
                domain_id="finance_ashare",
                objective="只做 600000 的 DCF",
                available_artifact_types=["user_request"],
                required_artifact_types=["uzi_native_method_result"],
                metadata={
                    "intent": "uzi_dcf",
                    "intent_inputs": {
                        "intent": "uzi_dcf",
                        "symbols": ["600000"],
                    },
                },
            )
        )
        dcf_capabilities = {node.capability for node in dcf_only.nodes if node.capability}
        self.assertIn("finance.uzi.native.dcf", dcf_capabilities)
        self.assertNotIn("finance.uzi.valuation.evaluate", dcf_capabilities)
        self.assertNotIn("finance.uzi.panel.review", dcf_capabilities)
        self.assertNotIn("finance.uzi.risk.scan", dcf_capabilities)

    def test_pack_owned_uzi_methodologies_are_profile_aware_and_data_bound(self) -> None:
        profile = select_profile({"research_profile": "lite"})
        rows = _snapshots()
        valuation = build_valuation_model(rows, ["600000"], profile)
        panel = build_panel_review(rows, ["600000"], profile)
        risk = build_risk_scan("分析 600000", rows, ["600000"], profile)

        self.assertEqual(profile["selected"], "lite")
        self.assertEqual(valuation["models"][0]["symbol"], "600000")
        self.assertIn("methodology_log", valuation["models"][0])
        self.assertEqual(len(valuation["dimensions"]["600000"]), 8)
        self.assertIn("peer_group_missing", valuation["dimensions"]["600000"][3]["data_gap"])
        self.assertEqual(len(panel["votes"]), 10)
        self.assertTrue(all(vote["is_methodology_perspective"] for vote in panel["votes"]))
        medium_panel = build_panel_review(rows, ["600000"], select_profile({"research_profile": "medium"}))
        deep_panel = build_panel_review(rows, ["600000"], select_profile({"research_profile": "deep"}))
        self.assertEqual(len(medium_panel["votes"]), 51)
        self.assertEqual(len(deep_panel["votes"]), 66)
        self.assertEqual(medium_panel["registered_seat_count"], 51)
        self.assertEqual(len(risk["signals"]), 8)
        self.assertIn("external_search_not_claimed", risk["risk_flags"])

    def test_medium_and_deep_methodology_products_preserve_data_gaps(self) -> None:
        rows = _snapshots()
        medium = select_profile({"research_profile": "medium"})
        valuation = build_valuation_model(rows, ["600000"], medium)
        institutional = build_institutional_analysis(rows, ["600000"], valuation, medium)
        workflow = build_research_workflow(rows, ["600000"], valuation, medium)
        deep_profile = select_profile({"research_profile": "deep"})
        deep_institutional = build_institutional_analysis(rows, ["600000"], valuation, deep_profile)
        deep = build_deep_analysis(rows, ["600000"], deep_institutional, workflow, deep_profile)

        self.assertEqual(institutional["results"][0]["symbol"], "600000")
        self.assertIn("three_statement", institutional["results"][0]["methods"])
        self.assertIn("quick_lbo", institutional["results"][0]["methods"])
        self.assertNotIn("segmental", institutional["results"][0]["methods"])
        self.assertEqual(workflow["results"][0]["symbol"], "600000")
        self.assertEqual(deep["results"][0]["symbol"], "600000")
        self.assertIn("bull_bear_debate", deep["results"][0])
        self.assertIn("reported_segment_data_missing", deep_institutional["results"][0]["methods"]["segmental"]["data_gap"])

    def test_agent_os_graph_commits_native_uzi_analysis_and_gated_delivery(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                    # This case asserts partial delivery when synthesis is
                    # unavailable; keep the provider state deterministic.
                    "openai_api_key": None,
                }
            )
            with TestClient(
                create_app(settings=settings, api_config=ApiConfig(api_token="uzi-test"))
            ) as client:
                response = client.post(
                    "/api/v1/runs",
                    headers={"Authorization": "Bearer uzi-test"},
                    json={
                        "intent": "stock_check",
                        "user_query": "分析 600000",
                        "constraints": {"research_profile": "lite"},
                        "slots": {"symbols": ["600000"], "snapshots": _snapshots()},
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                self.assertEqual(payload["status"], "gated")
                run_id = payload["run_id"]
                detail = client.get(
                    f"/api/v1/runs/{run_id}",
                    headers={"Authorization": "Bearer uzi-test"},
                )
                self.assertEqual(detail.status_code, 200, detail.text)
                self.assertEqual(detail.json()["snapshot"]["status"], "gated")
                self.assertEqual(detail.json()["snapshot"]["graph"]["metadata"]["research_profile"], "lite")
                steps = client.get(
                    f"/api/v1/runs/{run_id}/steps",
                    headers={"Authorization": "Bearer uzi-test"},
                )
                self.assertEqual(steps.status_code, 200, steps.text)
                capabilities = {step["capability"] for step in steps.json()["steps"]}
                self.assertIn("finance.uzi.native.analyze_stock", capabilities)
                self.assertIn("finance.uzi.native.report.compose", capabilities)
                self.assertNotIn("finance.uzi.report.compose", capabilities)
                self.assertNotIn("finance.analysis.evaluate", capabilities)
                self.assertNotIn("finance.report.compose", capabilities)
                artifacts = client.get(
                    f"/api/v1/runs/{run_id}/artifacts",
                    headers={"Authorization": "Bearer uzi-test"},
                )
                self.assertEqual(artifacts.status_code, 200, artifacts.text)
                rows = artifacts.json()["artifacts"]
                types = {row["artifact_type"] for row in rows}
                self.assertTrue(
                    {
                        "snapshot",
                        "uzi_native_analysis",
                        "research_report",
                        "assistant_message",
                    }.issubset(types)
                )
                self.assertNotIn("report_presentation", types)
                snapshot_rows = [row for row in rows if row["artifact_type"] == "snapshot"]
                self.assertTrue(snapshot_rows)
                self.assertTrue(
                    all(
                        row["content"]["quality_status"]
                        in {
                            "available",
                            "degraded",
                            "data_gap",
                            "assumption_only",
                            "no_data",
                            "provider_unavailable",
                            "rate_limited",
                            "parse_failed",
                        }
                        for row in snapshot_rows
                    )
                )
                native = next(row for row in rows if row["artifact_type"] == "uzi_native_analysis")
                report = next(row for row in rows if row["artifact_type"] == "research_report")
                self.assertEqual(native["content"]["profile"], "lite")
                self.assertFalse(native["content"]["report_allowed"])
                self.assertGreater(native["content"]["critical_count"], 0)
                self.assertFalse(report["content"]["report_allowed"])
                self.assertEqual(report["content"]["native_analysis_artifact_id"], native["artifact_id"])

    def test_explicit_medium_agent_os_run_uses_medium_artifact_contract(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                    # The test validates graph/artifact materialization rather
                    # than an external model response. Keep it deterministic
                    # even when the developer machine has a real API key.
                    "openai_api_key": None,
                }
            )
            with TestClient(
                create_app(settings=settings, api_config=ApiConfig(api_token="uzi-medium-test"))
            ) as client:
                response = client.post(
                    "/api/v1/runs",
                    headers={"Authorization": "Bearer uzi-medium-test"},
                    json={
                        "intent": "stock_check",
                        "user_query": "深度分析 600000",
                        "constraints": {"research_profile": "medium"},
                        "slots": {"symbols": ["600000"], "snapshots": _snapshots()},
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                run_id = response.json()["run_id"]
                artifacts = client.get(
                    f"/api/v1/runs/{run_id}/artifacts",
                    headers={"Authorization": "Bearer uzi-medium-test"},
                )
                self.assertEqual(artifacts.status_code, 200, artifacts.text)
                types = {row["artifact_type"] for row in artifacts.json()["artifacts"]}
                self.assertTrue(
                    {
                        "uzi_native_analysis",
                        "research_report",
                    }.issubset(types)
                )
                self.assertNotIn("report_presentation", types)
                self.assertNotIn("valuation_model", types)
                self.assertNotIn("deep_analysis", types)


if __name__ == "__main__":
    unittest.main()
