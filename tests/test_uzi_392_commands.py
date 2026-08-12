from __future__ import annotations

import importlib
import json
from pathlib import Path
import random
import unittest

from jsonschema import Draft202012Validator
import yaml

from domain_packs.finance_ashare.subagents.runtime import _requested_snapshot_types
from domain_packs.finance_ashare.subagents.task_intake import resolve_request
from domain_packs.finance_ashare.subagents.uzi_392_native_commands import (
    execute_panel_only,
    execute_scan_trap,
    execute_segmental_model,
)
from domain_packs.finance_ashare.subagents.uzi_392_native_methods import build_uzi_raw
from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import UZI_COMMAND_IDS, Uzi392FetcherRuntime
from research_system.api.validation import validate_research_request
from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.planning.capability_planner import CapabilityPlanner
from research_system.schemas.domain_pack import CapabilityPlanningRequest
from research_system.schemas.protocol import RequestEnvelope


ROOT = Path(__file__).resolve().parents[1]
COMMAND_SCHEMA = json.loads(
    (ROOT / "domain_packs/finance_ashare/artifact_schemas/uzi_native_command_result.json").read_text(encoding="utf-8")
)
EXPECTED_COMMAND_PLANS = {
    "analyze-stock": ("uzi_analyze_stock", "finance.uzi.native.analyze_stock"),
    "quick-scan": ("quick_scan", "finance.uzi.quick_scan.compose"),
    "panel-only": ("uzi_panel_only", "finance.uzi.command.panel_only"),
    "scan-trap": ("uzi_scan_trap", "finance.uzi.command.scan_trap"),
    "dcf": ("uzi_dcf", "finance.uzi.native.dcf"),
    "comps": ("uzi_comps", "finance.uzi.native.comps"),
    "lbo": ("uzi_lbo", "finance.uzi.native.quick_lbo"),
    "segmental-model": ("uzi_segmental_model", "finance.uzi.command.segmental_model"),
    "initiate": ("uzi_initiate", "finance.uzi.native.initiation_report"),
    "earnings": ("uzi_earnings", "finance.uzi.native.earnings_review"),
    "catalysts": ("uzi_catalysts", "finance.uzi.native.catalyst_calendar"),
    "thesis": ("uzi_thesis", "finance.uzi.native.thesis_tracker"),
    "screen": ("uzi_screen", "finance.uzi.native.quantitative_screen"),
    "dd": ("uzi_dd", "finance.uzi.native.dd_checklist"),
    "ic-memo": ("uzi_ic_memo", "finance.uzi.native.ic_memo"),
    "ai-readiness": ("uzi_ai_readiness", "finance.uzi.native.ai_readiness"),
    "earnings-preview": ("uzi_earnings_preview", "finance.uzi.native.earnings_preview"),
    "model-update": ("uzi_model_update", "finance.uzi.native.model_update"),
    "returns": ("uzi_returns", "finance.uzi.native.returns_attribution"),
    "rebalance": ("uzi_rebalance", "finance.uzi.native.holding_rebalance"),
}


def _row(snapshot_type: str, data: dict) -> dict:
    return {
        "snapshot_type": snapshot_type,
        "symbol": "600000",
        "facts": {"_uzi_raw_result": {"data": data, "sources": [{"provider": "fixture"}]}},
    }


def _command_snapshots() -> list[dict]:
    return [
        _row("market", {"code": "600000", "name": "浦发银行", "industry": "银行", "price": 10, "market_cap_yi": 3000}),
        _row("fundamental", {"revenue_history": [1000, 1100, 1200], "net_profit_history": [100, 110, 120], "roe_history": [10, 11, 12]}),
        _row("chain", {"main_business_breakdown": [{"name": "公司业务", "value": 0.7}, {"name": "零售业务", "value": 0.3}]}),
        _row("events", {"events": [{"title": "新产品投产"}]}),
    ]


class Uzi392CommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        Uzi392FetcherRuntime(workspace_root=ROOT / "workspace")

    def test_command_inventory_is_exact_and_each_row_has_an_independent_intent(self) -> None:
        self.assertEqual(set(EXPECTED_COMMAND_PLANS), set(UZI_COMMAND_IDS))
        intents = [intent for intent, _capability in EXPECTED_COMMAND_PLANS.values()]
        self.assertEqual(len(intents), len(set(intents)))

    def test_all_source_parity_method_commands_have_manifest_intents_and_plans(self) -> None:
        manifest = yaml.safe_load((ROOT / "domain_packs/finance_ashare/domain.yaml").read_text(encoding="utf-8"))
        intent_rows = {str(row["intent_id"]): row for row in manifest["intake"]["intents"]}
        declared_intents = set(intent_rows)
        planner = CapabilityPlanner(DomainPackRegistry.load(ROOT / "domain_packs"))
        for command_id, (intent, capability) in EXPECTED_COMMAND_PLANS.items():
            with self.subTest(command_id=command_id):
                self.assertIn(intent, declared_intents)
                intent_inputs = {
                    "intent": intent,
                    "symbols": ["600000"],
                    "research_profile": "lite",
                    "portfolio_holdings": [{"ticker": "600000", "weight": 1, "return_pct": 2}],
                }
                graph = planner.build_graph(
                    CapabilityPlanningRequest(
                        domain_id="finance_ashare",
                        objective=command_id,
                        available_artifact_types=["user_request"],
                        required_artifact_types=list(intent_rows[intent]["output_artifact_types"]),
                        metadata={"intent": intent, "intent_inputs": intent_inputs},
                    )
                )
                self.assertIn(capability, {node.capability for node in graph.nodes})

    def test_dcf_api_intake_and_snapshot_scope_are_command_specific(self) -> None:
        validated = validate_research_request(
            RequestEnvelope(intent="uzi_dcf", user_query="/dcf 600000", constraints={"symbol": "600000"})
        )
        self.assertEqual(validated.intent, "uzi_dcf")
        intake = resolve_request(
            {"intent": "uzi_dcf", "user_query": "/dcf 600000", "constraints": {"symbol": "600000"}}
        )
        self.assertEqual(intake["status"], "accepted")
        self.assertEqual(_requested_snapshot_types(inputs={"intent": "uzi_dcf"}, sector=None), ["market", "fundamental"])

    def test_panel_only_uses_the_exact_upstream_scoring_and_panel_functions(self) -> None:
        rows = _command_snapshots()
        raw = build_uzi_raw(rows, "600000")
        score_fns = importlib.import_module("lib.pipeline.score_fns")
        expected_dimensions = score_fns.score_dimensions(raw)
        random.seed(392)
        expected_panel = score_fns.generate_panel(expected_dimensions, raw)
        random.seed(392)
        expected_synthesis = score_fns.generate_synthesis(
            raw,
            expected_dimensions,
            expected_panel,
        )
        random.seed(392)
        actual = execute_panel_only(rows, ["600000"])
        Draft202012Validator(COMMAND_SCHEMA).validate(actual)
        self.assertEqual(
            actual["results"][0]["result"],
            {
                "dimensions": expected_dimensions,
                "panel": expected_panel,
                "synthesis": expected_synthesis,
            },
        )

    def test_scan_trap_does_not_turn_an_empty_search_into_safe(self) -> None:
        empty = [
            _row(
                "trap",
                {
                    "trap_level": "🟢 安全",
                    "trap_score": 9,
                    "signals_hit_count": 0,
                    "snippets": {f"signal_{index}": [] for index in range(1, 9)},
                },
            )
        ]
        result = execute_scan_trap(empty, ["600000"], query="检查 600000")
        self.assertEqual(result["quality_status"], "data_gap")
        self.assertIn("600000:trap_search_evidence_missing", result["data_gap"])

    def test_scan_trap_escalates_one_level_for_explicit_promotion_context(self) -> None:
        rows = [
            _row(
                "trap",
                {
                    "trap_level": "🟢 安全",
                    "trap_score": 9,
                    "signals_hit_count": 0,
                    "snippets": {"signal_1": [{"title": "公开搜索结果", "body": "普通内容", "url": "https://example.test"}]},
                },
            )
        ]
        result = execute_scan_trap(rows, ["600000"], query="朋友推荐我买 600000")
        command_result = result["results"][0]["result"]["command_result"]
        self.assertEqual(command_result["trap_level"], "🟡 注意")
        self.assertIs(command_result["context_escalated"], True)

    def test_segmental_command_preserves_exact_discovery_and_validation_outputs(self) -> None:
        rows = _command_snapshots()
        raw = build_uzi_raw(rows, "600000")
        segmental = importlib.import_module("lib.segmental_model")
        expected = segmental.discover_segments(raw).to_dict()
        discovery = execute_segmental_model(rows, ["600000"])
        self.assertEqual(discovery["phase"], "awaiting_assumptions")
        self.assertEqual(discovery["results"][0]["result"]["skeleton"], expected)
        filled = {**expected, "segments": [{**row, "drivers": ["真实披露驱动"], "thesis_tag": "stable_cash_cow", "bull_growth_3y_cagr": 8, "base_growth_3y_cagr": 5, "bear_growth_3y_cagr": 2} for row in expected["segments"]]}
        validated = execute_segmental_model(rows, ["600000"], {"segmental_model": filled})
        expected_validation = segmental.validate_model(filled, raw)
        self.assertEqual(validated["results"][0]["result"]["validation"], expected_validation)


if __name__ == "__main__":
    unittest.main()
