from __future__ import annotations

import json
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
import unittest

import jsonschema
import yaml

from fastapi.testclient import TestClient

from domain_packs.finance_ashare.subagents.uzi_methodology import (
    build_features,
    build_panel_review,
    build_presentation,
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
from domain_packs.finance_ashare.tools.snapshot_collector import collect_snapshots
import domain_packs.finance_ashare.tools.snapshot_collector as snapshot_collector
from domain_packs.finance_ashare.tools.uzi_cache_import import import_uzi_cache_file
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
            {"market", "fundamental", "notice", "capital_flow", "sentiment", "events", "lhb"},
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
        self.assertEqual(mapping["source"]["commit"], "775c76101e8890492d21c2e2827692456fcddb58")
        self.assertEqual(mapping["profiles"]["lite"]["panel_seats"], 10)
        self.assertEqual(mapping["profiles"]["medium"]["panel_seats"], 51)
        self.assertEqual(mapping["profiles"]["deep"]["panel_seats"], 51)
        self.assertEqual(len(mapping["panel"]["member_ids"]), 66)
        required_mapping_fields = {
            "source_commit",
            "source_path",
            "provider",
            "field_mapping",
            "quality_policy",
            "migration_status",
            "result_equivalence",
            "baseline_fixture",
        }
        self.assertTrue(
            all(
                required_mapping_fields.issubset(row)
                for row in [*fetchers, *mapping["derived_methods"]]
            )
        )
        replay = json.loads(
            (repository_root / "tests" / "fixtures" / "uzi_source_snapshot_replay.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(
            {row["source_id"] for row in replay["records"]},
            {row["source_id"] for row in fetchers},
        )

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
        self.assertRegex(source["version_file_sha256"], r"^[A-Fa-f0-9]{64}$")
        self.assertRegex(source["license_sha256"], r"^[A-Fa-f0-9]{64}$")
        fixture_hashes = source["replay_fixture_sha256"]
        self.assertEqual(set(source["replay_fixtures"]), set(fixture_hashes))

        def sha256(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest().upper()

        for fixture in source["replay_fixtures"]:
            fixture_path = repository_root / fixture
            self.assertTrue(fixture_path.is_file(), fixture)
            self.assertEqual(sha256(fixture_path), str(fixture_hashes[fixture]).upper())

        for entry in [*mapping["fetchers"], *mapping["derived_methods"]]:
            self.assertIn(entry["baseline_fixture"], fixture_hashes)

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

    def test_uzi_cache_import_is_explicit_and_preserves_quality_lineage(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "raw_data.json"
            path.write_text(
                '{"ticker":"300750","fetched_at":"2026-07-12","raw_data":'
                '{"basic":{"price":100},"peers":{"data_gap":["peer_group_missing"]}}}',
                encoding="utf-8",
            )
            drafts = import_uzi_cache_file(path)

        self.assertEqual({draft.content["snapshot_type"] for draft in drafts}, {"market", "peers"})
        peers = next(draft for draft in drafts if draft.content["snapshot_type"] == "peers")
        self.assertEqual(peers.content["quality_status"], "degraded")
        self.assertTrue(peers.metadata["cache_import"])
        self.assertEqual(peers.source_refs[0]["type"], "uzi_cache")

    def test_uzi_cache_import_unwraps_real_numbered_dimension_records(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "raw_data.json"
            path.write_text(
                json.dumps(
                    {
                        "ticker": "300750.SZ",
                        "fetched_at": "2026-07-12T01:51:47",
                        "dimensions": {
                            "0_basic": {
                                "ticker": "300750.SZ",
                                "data": {"name": "宁德时代", "price": 348.76},
                                "source": "akshare:A",
                            },
                            "1_financials": {
                                "ticker": "300750.SZ",
                                "data": {"revenue_history": [100, 120]},
                                "source": "akshare:financials",
                            },
                            "20_valuation_models": {
                                "ticker": "300750.SZ",
                                "data": {"summary": {"dcf_intrinsic": 420}},
                            },
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            drafts = import_uzi_cache_file(path)

        self.assertEqual(
            {draft.content["snapshot_type"] for draft in drafts},
            {"market", "fundamental"},
        )
        market = next(draft for draft in drafts if draft.content["snapshot_type"] == "market")
        self.assertEqual(market.content["facts"]["price"], 348.76)
        self.assertEqual(market.content["source"]["source_system"], "uzi_cache")

    def test_dimension_coverage_separates_raw_sources_from_derived_products(self) -> None:
        coverage = build_dimension_coverage(
            _snapshots(),
            ["600000"],
            select_profile({"research_profile": "medium"}),
        )

        self.assertEqual(coverage["dimension_count"], 20)
        self.assertEqual(coverage["derived_dimension_count"], 3)
        self.assertEqual(coverage["total_dimension_count"], 23)
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
        self.assertEqual(valuation["status"], "available")
        self.assertNotIn("total_assets_missing", valuation["warnings"])

    def test_report_presentation_exposes_bento_dashboard_sections(self) -> None:
        rows = _snapshots()
        profile = select_profile({"research_profile": "lite"})
        valuation = build_valuation_model(rows, ["600000"], profile)
        panel = build_panel_review(rows, ["600000"], profile)
        risk = build_risk_scan("分析 600000", rows, ["600000"], profile)

        presentation = build_presentation(
            {
                "symbols": ["600000"],
                "risk": ["需要复核数据完整性"],
                "conclusion": "结构化研究结论",
            },
            valuation,
            panel,
            risk,
            profile,
            snapshot_rows=rows,
        )

        self.assertEqual(presentation["version"], "2")
        self.assertEqual(presentation["subject"]["symbol"], "600000")
        self.assertEqual(len(presentation["subject"]["metrics"]), 6)
        self.assertIn("fundamentals", presentation["dashboard"])
        self.assertIn("change_pct", presentation["dashboard"]["price"])
        self.assertIn("top_vote", presentation["great_divide"]["bull"])
        self.assertEqual(
            presentation["panel"]["distribution"],
            panel["distribution"],
        )

    def test_report_presentation_exposes_full_scan_panel_and_model_details(self) -> None:
        rows = _snapshots()
        profile = select_profile({"research_profile": "medium"})
        valuation = build_valuation_model(rows, ["600000"], profile)
        panel = build_panel_review(rows, ["600000"], profile)
        risk = build_risk_scan("分析 600000", rows, ["600000"], profile)
        institutional = build_institutional_analysis(rows, ["600000"], valuation, profile)
        workflow = build_research_workflow(rows, ["600000"], valuation, profile)
        deep = build_deep_analysis(rows, ["600000"], institutional, workflow, profile)
        coverage = build_dimension_coverage(
            rows,
            ["600000"],
            profile,
            institutional_available=True,
            workflow_available=True,
            deep_available=True,
        )

        presentation = build_presentation(
            {
                "symbols": ["600000"],
                "risk": ["需要复核数据完整性"],
                "conclusion": "结构化研究结论",
            },
            valuation,
            panel,
            risk,
            profile,
            advanced={
                "institutional_analysis": institutional,
                "research_workflow": workflow,
                "deep_analysis": deep,
                "uzi_dimension_coverage": coverage,
            },
            snapshot_rows=rows,
        )

        schema = json.loads(
            (Path(__file__).resolve().parents[1] / "domain_packs" / "finance_ashare" / "artifact_schemas" / "report_presentation.json").read_text(encoding="utf-8")
        )
        jsonschema.validate(presentation, schema)
        self.assertEqual(len(presentation["deep_scan"]), 23)
        self.assertEqual(len(presentation["panel_insights"]), panel["registered_seat_count"])
        self.assertTrue(presentation["great_divide"]["rounds"])
        self.assertTrue(presentation["institutional_modeling"][0]["methods"])
        self.assertEqual(len(presentation["institutional_modeling"][0]["methods"][0]["sensitivity"]), 25)
        self.assertTrue(presentation["risk_list"])
        self.assertEqual(presentation["buy_zones"][0]["status"], "data_gap")
        self.assertEqual(presentation["buy_zones"][0]["kind"], "observation")

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
                required_artifact_types=["research_report"],
                metadata={
                    "intent": "stock_check",
                    "required_outputs_source": "manifest_report_contract",
                    "intent_inputs": {
                        "intent": "stock_check",
                        "research_profile": "lite",
                        "requested_methods": ["dcf"],
                    },
                },
            )
        )
        capabilities = {node.capability for node in graph.nodes if node.capability}

        self.assertIn("finance.uzi.valuation.evaluate", capabilities)
        self.assertNotIn("finance.uzi.report.compose", capabilities)
        self.assertNotIn("finance.report.compose", capabilities)
        self.assertIn("finance.valuation.input", graph.governance.required_gate_ids)

    def test_specialized_intents_select_profile_and_quality_gate(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        planner = CapabilityPlanner(
            DomainPackRegistry.load(repository_root / "domain_packs")
        )
        cases = {
            "stock_screen": ("quick", "screening_result", "finance.screening.quality", 90.0),
            "sector_research": ("lite", "sector_analysis", "finance.sector.analysis", 300.0),
            "review_thesis": ("quick", "thesis_review", "finance.thesis.review", 90.0),
        }
        for intent, (profile, artifact_type, gate_id, timeout) in cases.items():
            with self.subTest(intent=intent):
                graph = planner.build_graph(
                    CapabilityPlanningRequest(
                        domain_id="finance_ashare",
                        objective=f"Run {intent}",
                        available_artifact_types=["user_request"],
                        required_artifact_types=["research_report"],
                        metadata={
                            "intent": intent,
                            "intent_inputs": {"intent": intent},
                        },
                    )
                )
                self.assertEqual(graph.metadata["selected_research_profile"], profile)
                self.assertIn(artifact_type, graph.metadata["required_artifact_types"])
                self.assertIn(gate_id, graph.governance.required_gate_ids)
                self.assertEqual(graph.governance.timeout_seconds, timeout)
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
                required_artifact_types=["valuation_model"],
                metadata={
                    "intent": "stock_check",
                    "intent_inputs": {
                        "intent": "stock_check",
                        "research_profile": "lite",
                        "requested_methods": ["dcf"],
                    },
                },
            )
        )
        valuation = next(
            node for node in graph.nodes
            if node.capability == "finance.uzi.valuation.evaluate"
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
                required_artifact_types=["research_report"],
                metadata={
                    "intent": "stock_check",
                    "required_outputs_source": "manifest_report_contract",
                    "intent_inputs": {
                        "intent": "stock_check",
                        "research_profile": "medium",
                        "requested_methods": ["lbo"],
                    },
                },
            )
        )
        capabilities = {node.capability for node in graph.nodes if node.capability}
        self.assertIn("finance.uzi.institutional.evaluate", capabilities)
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

        quick = planner.build_graph(
            CapabilityPlanningRequest(
                domain_id="finance_ashare",
                objective="快速检查 600000",
                available_artifact_types=["user_request"],
                required_artifact_types=["research_report"],
                metadata={
                    "intent": "stock_check",
                    "intent_inputs": {
                        "intent": "stock_check",
                        "research_profile": "quick",
                    },
                },
            )
        )
        quick_capabilities = {node.capability for node in quick.nodes if node.capability}
        self.assertIn("finance.uzi.valuation.evaluate", quick_capabilities)
        self.assertIn("finance.uzi.risk.scan", quick_capabilities)
        self.assertNotIn("finance.uzi.panel.review", quick_capabilities)
        quick_valuation = next(
            node for node in quick.nodes if node.capability == "finance.uzi.valuation.evaluate"
        )
        quick_methods = {
            item["method_id"] for item in quick_valuation.metadata["method_contracts"]
        }
        self.assertEqual(quick_methods, {"dcf"})

        dcf_only = planner.build_graph(
            CapabilityPlanningRequest(
                domain_id="finance_ashare",
                objective="只做 600000 的 DCF",
                available_artifact_types=["user_request"],
                required_artifact_types=["research_report"],
                metadata={
                    "intent": "stock_check",
                    "intent_inputs": {
                        "intent": "stock_check",
                        "research_profile": "lite",
                        "requested_methods": ["dcf"],
                    },
                },
            )
        )
        dcf_capabilities = {node.capability for node in dcf_only.nodes if node.capability}
        self.assertIn("finance.uzi.valuation.evaluate", dcf_capabilities)
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
        self.assertEqual(len(deep_panel["votes"]), 51)
        self.assertEqual(medium_panel["registered_seat_count"], 51)
        self.assertEqual(len(risk["signals"]), 8)
        self.assertIn("external_search_not_claimed", risk["risk_flags"])

    def test_medium_and_deep_methodology_products_preserve_data_gaps(self) -> None:
        rows = _snapshots()
        medium = select_profile({"research_profile": "medium"})
        valuation = build_valuation_model(rows, ["600000"], medium)
        institutional = build_institutional_analysis(rows, ["600000"], valuation, medium)
        workflow = build_research_workflow(rows, ["600000"], valuation, medium)
        deep = build_deep_analysis(rows, ["600000"], institutional, workflow, select_profile({"research_profile": "deep"}))

        self.assertEqual(institutional["results"][0]["symbol"], "600000")
        self.assertIn("three_statement", institutional["results"][0]["methods"])
        self.assertIn("quick_lbo", institutional["results"][0]["methods"])
        self.assertEqual(workflow["results"][0]["symbol"], "600000")
        self.assertEqual(deep["results"][0]["symbol"], "600000")
        self.assertIn("bull_bear_debate", deep["results"][0])
        self.assertIn("segment_revenue_missing", institutional["results"][0]["methods"]["segmental"]["data_gap"])

    def test_agent_os_graph_commits_uzi_artifacts_and_presentation(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
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
                self.assertEqual(payload["status"], "completed")
                run_id = payload["run_id"]
                detail = client.get(
                    f"/api/v1/runs/{run_id}",
                    headers={"Authorization": "Bearer uzi-test"},
                )
                self.assertEqual(detail.status_code, 200, detail.text)
                self.assertEqual(detail.json()["snapshot"]["graph"]["metadata"]["research_profile"], "lite")
                steps = client.get(
                    f"/api/v1/runs/{run_id}/steps",
                    headers={"Authorization": "Bearer uzi-test"},
                )
                self.assertEqual(steps.status_code, 200, steps.text)
                capabilities = {step["capability"] for step in steps.json()["steps"]}
                self.assertIn("finance.uzi.report.compose", capabilities)
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
                        "valuation_model",
                        "investor_panel_review",
                        "risk_scan",
                        "report_presentation",
                        "main_agent_synthesis",
                        "assistant_message",
                    }.issubset(types)
                )
                snapshot_rows = [row for row in rows if row["artifact_type"] == "snapshot"]
                self.assertTrue(snapshot_rows)
                self.assertTrue(
                    all(
                        row["content"]["quality_status"]
                        in {"available", "degraded", "data_gap", "assumption_only"}
                        for row in snapshot_rows
                    )
                )
                presentation = next(
                    row for row in rows if row["artifact_type"] == "report_presentation"
                )
                self.assertEqual(presentation["content"]["profile"], "lite")
                self.assertTrue(presentation["content"]["source_artifact_ids"])

    def test_medium_agent_os_run_commits_advanced_uzi_artifacts(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
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
                        "institutional_analysis",
                        "research_workflow",
                        "uzi_dimension_coverage",
                        "research_report",
                        "report_presentation",
                    }.issubset(types)
                )


if __name__ == "__main__":
    unittest.main()
