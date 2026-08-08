from __future__ import annotations

import json
from pathlib import Path
import unittest

import jsonschema

from domain_packs.finance_ashare.subagents.runtime import _requested_snapshot_types
from domain_packs.finance_ashare.subagents.task_intake import resolve_request
from domain_packs.finance_ashare.subagents.uzi_methodology import (
    UZI_QUICK_SCAN_DIMENSIONS,
    UZI_QUICK_SCAN_INVESTOR_IDS,
    build_quick_scan,
)
from research_system.api.validation import validate_research_request
from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.planning.capability_planner import CapabilityPlanner
from research_system.schemas.domain_pack import CapabilityPlanningRequest
from research_system.schemas.protocol import RequestEnvelope


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _row(snapshot_type: str, data: dict, *, quality_status: str = "available") -> dict:
    projections = {
        "fundamental": {"financial_abstract": data},
        "kline": {"history": data},
    }.get(snapshot_type, {})
    return {
        "snapshot_type": snapshot_type,
        "symbol": "600519",
        "quality_status": quality_status,
        "facts": {
            **data,
            **projections,
            "availability": quality_status,
            "_uzi_raw_result": {
                "ticker": "600519",
                "data": data,
                "source": f"test:{snapshot_type}",
                "fallback": False,
            },
        },
    }


class UziQuickScanTests(unittest.TestCase):
    def test_command_contract_uses_exact_dimensions_and_investor_order(self) -> None:
        rows = [
            _row(
                "fundamental",
                {
                    "roe": "18.0%",
                    "net_margin": "15.0%",
                    "roe_history": [16.0, 17.0, 18.0],
                    "revenue_history": [100.0, 115.0, 132.0],
                    "net_profit_history": [20.0, 23.0, 27.0],
                    "financial_health": {"debt_ratio": 28.0},
                },
            ),
            _row(
                "kline",
                {
                    "indicators": {
                        "stage": 2,
                        "rsi_14": 56.0,
                        "ma_bull_alignment": True,
                    },
                    "volatility_1y": 21.0,
                },
            ),
            _row(
                "valuation",
                {
                    "pe_ttm": 22.0,
                    "pb": 6.0,
                    "pe_quantile_5y": 35.0,
                },
            ),
            _row(
                "trap",
                {
                    "trap_level": "🟢 安全",
                    "trap_score": 9,
                    "signals_hit": "0/8",
                    "signals_hit_count": 0,
                    "signals_hit_detail": [],
                    "recommendation": "未发现明显推广痕迹。",
                },
            ),
        ]

        result = build_quick_scan(rows, ["600519"])

        self.assertEqual(result["required_dimensions"], list(UZI_QUICK_SCAN_DIMENSIONS))
        self.assertEqual(result["investor_ids"], list(UZI_QUICK_SCAN_INVESTOR_IDS))
        self.assertEqual([vote["investor_id"] for vote in result["votes"]], list(UZI_QUICK_SCAN_INVESTOR_IDS))
        self.assertEqual(result["output_format"], "markdown")
        self.assertIs(result["html_generated"], False)
        self.assertEqual(result["trap_level"], "🟢 安全")
        self.assertIn("## 10 位方法论投票", result["markdown"])
        self.assertNotIn("<html", result["markdown"].casefold())

        schema = json.loads(
            (REPOSITORY_ROOT / "domain_packs/finance_ashare/artifact_schemas/quick_scan.json").read_text(
                encoding="utf-8"
            )
        )
        jsonschema.validate(result, schema)

    def test_missing_trap_data_never_defaults_to_safe(self) -> None:
        rows = [
            _row("fundamental", {"roe": "18.0%"}),
            _row("kline", {"indicators": {"rsi_14": 50.0}}),
            _row("valuation", {"pe_ttm": 22.0}),
            _row("trap", {}, quality_status="data_gap"),
        ]

        result = build_quick_scan(rows, ["600519"])

        self.assertIsNone(result["trap_level"])
        self.assertIn("18_trap", result["data_gap"])
        self.assertIn("trap_status_unknown", result["risk_flags"])
        self.assertIn("数据缺口，无法判断", result["markdown"])

    def test_api_intake_planner_and_snapshot_selection_are_independent_of_lite(self) -> None:
        validated = validate_research_request(
            RequestEnvelope(
                intent="quick_scan",
                user_query="速判 600519",
                constraints={"symbol": "600519"},
            )
        )
        self.assertEqual(validated.intent, "quick_scan")
        self.assertNotIn("research_profile", validated.constraints)

        intake = resolve_request(
            {
                "intent": "quick_scan",
                "user_query": "速判 600519",
                "constraints": {"symbol": "600519"},
            }
        )
        self.assertEqual(intake["status"], "accepted")
        self.assertEqual(intake["intent"], "quick_scan")
        self.assertNotIn("research_profile", intake["constraints"])

        self.assertEqual(
            _requested_snapshot_types(
                inputs={
                    "intent": "quick_scan",
                    "research_profile": "deep",
                    "snapshot_types": ["market", "governance"],
                },
                sector=None,
            ),
            ["fundamental", "kline", "valuation", "trap"],
        )

        planner = CapabilityPlanner(DomainPackRegistry.load(REPOSITORY_ROOT / "domain_packs"))
        graph = planner.build_graph(
            CapabilityPlanningRequest(
                domain_id="finance_ashare",
                objective="速判 600519",
                available_artifact_types=["user_request"],
                required_artifact_types=["quick_scan"],
                metadata={
                    "intent": "quick_scan",
                    "intent_inputs": {"intent": "quick_scan", "symbols": ["600519"]},
                },
            )
        )
        capabilities = [node.capability for node in graph.nodes]
        self.assertIn("finance.ashare.snapshot.collect", capabilities)
        self.assertIn("finance.uzi.quick_scan.compose", capabilities)
        self.assertNotIn("finance.uzi.report.compose", capabilities)


if __name__ == "__main__":
    unittest.main()
