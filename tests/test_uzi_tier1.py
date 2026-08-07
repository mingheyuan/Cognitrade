from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from fastapi.testclient import TestClient

from domain_packs.finance_ashare.subagents.task_intake import resolve_request
from domain_packs.finance_ashare.subagents.uzi_tier1_methodology import (
    build_ai_readiness,
    build_earnings_preview,
    build_holding_rebalance,
    build_model_update,
    build_returns_attribution,
)
from research_system.api.app import create_app
from research_system.api.config import ApiConfig
from research_system.api.errors import ApiError
from research_system.api.validation import validate_research_request
from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.planning.capability_planner import CapabilityPlanner
from research_system.runtime.platform.settings import Settings
from research_system.schemas.domain_pack import CapabilityPlanningRequest
from research_system.schemas.protocol import RequestEnvelope


def _observed_rows() -> list[dict]:
    return [
        {
            "snapshot_type": "market",
            "symbol": "300001",
            "quality_status": "available",
            "facts": {"market_overview": {"name": "示例光通信", "industry": "光通信", "latest_price": 20, "pe_ttm": 25}},
        },
        {
            "snapshot_type": "fundamental",
            "symbol": "300001",
            "quality_status": "available",
            "facts": {"financial_abstract": {"revenue": 100, "eps": 2, "net_profit": 20, "net_margin": 20}},
        },
        {
            "snapshot_type": "chain",
            "symbol": "300001",
            "quality_status": "available",
            "facts": {"description": "AI 光模块 CPO 已量产并取得在手订单"},
        },
        {
            "snapshot_type": "industry",
            "symbol": "300001",
            "quality_status": "available",
            "facts": {"name": "光通信"},
        },
        {
            "snapshot_type": "events",
            "symbol": "300001",
            "quality_status": "available",
            "facts": {"events": ["高速光模块新产能投产"]},
        },
        {
            "snapshot_type": "research",
            "symbol": "300001",
            "quality_status": "available",
            "facts": {"consensus_eps": 2.4, "consensus_revenue": 115, "target_price_avg": 25},
        },
        {
            "snapshot_type": "moat",
            "symbol": "300001",
            "quality_status": "available",
            "facts": {"ai_irreplaceable": True},
        },
    ]


class UziTier1MethodologyTests(unittest.TestCase):
    def test_tier1_replay_fixture_preserves_no_imputation_contract(self) -> None:
        fixture = json.loads(
            (Path(__file__).parent / "fixtures" / "uzi_tier1_baseline.json").read_text(encoding="utf-8")
        )
        returns_case = fixture["returns_attribution_case"]
        returns = build_returns_attribution(returns_case["holdings"])
        for key, expected in returns_case["expected"].items():
            self.assertEqual(returns[key], expected)
        rebalance_case = fixture["rebalance_case"]
        rebalance = build_holding_rebalance(
            rebalance_case["holdings"],
            rebalance_case["targets"],
        )
        self.assertEqual(rebalance["estimated_cost"], rebalance_case["expected"]["estimated_cost"])

    def test_ai_readiness_uses_three_observed_gates(self) -> None:
        result = build_ai_readiness(_observed_rows(), ["300001"])

        self.assertEqual(result["quality_status"], "available")
        self.assertEqual(result["companies"][0]["rating"], "strong")
        self.assertEqual(result["companies"][0]["gates_passed"], 3)
        self.assertTrue(result["companies"][0]["leverage_points"])

    def test_ai_readiness_marks_missing_sources_as_gap_not_negative_fact(self) -> None:
        result = build_ai_readiness(_observed_rows()[:2], ["300001"])

        company = result["companies"][0]
        self.assertEqual(company["rating"], "evidence_incomplete")
        self.assertEqual([gate["status"] for gate in company["gates"]], ["data_gap", "data_gap", "data_gap"])

    def test_earnings_scenarios_are_labeled_assumptions(self) -> None:
        result = build_earnings_preview(_observed_rows(), ["300001"])

        preview = result["previews"][0]
        self.assertEqual(len(preview["scenarios"]), 3)
        self.assertTrue(all(row["status"] == "assumption_only" for row in preview["scenarios"]))
        self.assertIn("scenario_band_plus_minus_5pct", result["assumption_flags"])
        self.assertIsNone(preview["implied_move"]["value"])

    def test_model_update_requires_explicit_updates(self) -> None:
        missing = build_model_update(_observed_rows(), ["300001"], None)
        supplied = build_model_update(_observed_rows(), ["300001"], {"net_margin": 22})

        self.assertEqual(missing["quality_status"], "data_gap")
        self.assertIn("model_updates_missing", missing["data_gap"])
        self.assertEqual(supplied["updates"][0]["before"], 20)
        self.assertEqual(supplied["updates"][0]["after"], 22)
        self.assertIsNone(supplied["impacts"][0]["fair_value_after"])

    def test_missing_return_is_not_imputed_as_zero(self) -> None:
        result = build_returns_attribution(
            [
                {"ticker": "600519", "weight": 0.6, "return_pct": 10, "industry": "消费"},
                {"ticker": "300750", "weight": 0.4, "industry": "新能源"},
            ],
            benchmark_return=4,
        )

        self.assertEqual(result["observed_weight_coverage"], 0.6)
        self.assertEqual(result["observed_contribution_pct"], 6.0)
        self.assertIsNone(result["total_return_pct"])
        self.assertIsNone(result["benchmark"]["excess_return_pct"])
        self.assertIn("return_pct_missing:300750", result["data_gap"])

    def test_rebalance_never_invents_fee_or_portfolio_value(self) -> None:
        result = build_holding_rebalance(
            [
                {"ticker": "600519", "weight": 0.7, "market": "A"},
                {"ticker": "300750", "weight": 0.3, "market": "A"},
            ],
            targets={"600519": 0.5, "300750": 0.5},
        )

        self.assertIsNone(result["estimated_cost"])
        self.assertIn("portfolio_value_missing", result["data_gap"])
        self.assertTrue(all(row["trade_amount"] is None for row in result["drift_table"] if row["breached"]))

    def test_equal_weight_target_is_an_explicit_policy_assumption(self) -> None:
        result = build_holding_rebalance(
            [
                {"ticker": "600519", "weight": 0.8, "value": 800, "price": 10},
                {"ticker": "300750", "weight": 0.2, "value": 200, "price": 10},
            ],
            fee_model={"default": {"commission_rate": 0, "buy_stamp_rate": 0, "sell_stamp_rate": 0}},
        )

        self.assertEqual(result["quality_status"], "assumption_only")
        self.assertEqual(result["target_mode"], "equal_weight_policy_assumption")
        self.assertIn("equal_weight_target_not_observed_market_data", result["assumption_flags"])


class UziTier1IntegrationTests(unittest.TestCase):
    def test_api_validates_portfolio_and_model_update_contracts(self) -> None:
        request = validate_research_request(
            RequestEnvelope(
                intent="portfolio_research",
                user_query="做组合收益归因和再平衡",
                constraints={
                    "portfolio_holdings": [
                        {"ticker": "600519", "weight": 60, "return_pct": 8},
                        {"ticker": "300750", "weight": 40, "return_pct": -2},
                    ],
                    "benchmark_return": 3,
                    "rebalance_targets": {"600519": 50, "300750": 50},
                },
            )
        )
        self.assertEqual(request.intent, "portfolio_research")

        stock = validate_research_request(
            RequestEnvelope(
                intent="stock_check",
                user_query="更新 300001 模型",
                constraints={
                    "symbol": "300001",
                    "research_profile": "medium",
                    "requested_methods": ["model_update"],
                    "model_updates": {"net_margin": 22},
                },
            )
        )
        self.assertEqual(stock.constraints["model_updates"]["net_margin"], 22)

        with self.assertRaises(ApiError):
            validate_research_request(
                RequestEnvelope(
                    intent="portfolio_research",
                    user_query="做组合归因",
                    constraints={"portfolio_holdings": [{"ticker": "600519", "weight": 0.0}]},
                )
            )

    def test_task_intake_routes_portfolio_before_stock(self) -> None:
        decision = resolve_request(
            {
                "user_query": "分析我的组合收益归因并给再平衡建议",
                "constraints": {
                    "portfolio_holdings": [
                        {"ticker": "600519", "weight": 0.6, "return_pct": 8},
                        {"ticker": "300750", "weight": 0.4, "return_pct": -2},
                    ]
                },
            }
        )

        self.assertEqual(decision["status"], "accepted")
        self.assertEqual(decision["intent"], "portfolio_research")
        self.assertEqual(len(decision["slots"]["portfolio_holdings"]), 2)

    def test_planner_selects_all_five_tier1_capabilities(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        registry = DomainPackRegistry.load(repository_root / "domain_packs")
        planner = CapabilityPlanner(registry)

        stock_graph = planner.build_graph(
            CapabilityPlanningRequest(
                domain_id="finance_ashare",
                objective="运行三个 Tier-1 个股方法",
                available_artifact_types=["user_request"],
                required_artifact_types=["research_report"],
                metadata={
                    "intent": "stock_check",
                    "intent_inputs": {
                        "intent": "stock_check",
                        "symbols": ["300001"],
                        "research_profile": "deep",
                        "requested_methods": ["ai_readiness", "earnings_preview", "model_update"],
                        "model_updates": {"net_margin": 22},
                    },
                },
            )
        )
        stock_capabilities = {node.capability for node in stock_graph.nodes}
        self.assertTrue(
            {
                "finance.uzi.tier1.ai_readiness",
                "finance.uzi.tier1.earnings_preview",
                "finance.uzi.tier1.model_update",
            }.issubset(stock_capabilities)
        )

        portfolio_graph = planner.build_graph(
            CapabilityPlanningRequest(
                domain_id="finance_ashare",
                objective="组合归因与再平衡",
                available_artifact_types=["user_request"],
                required_artifact_types=["returns_attribution", "holding_rebalance"],
                metadata={
                    "intent": "portfolio_research",
                    "intent_inputs": {
                        "intent": "portfolio_research",
                        "portfolio_holdings": [{"ticker": "600519", "weight": 1, "return_pct": 5}],
                    },
                },
            )
        )
        portfolio_capabilities = {node.capability for node in portfolio_graph.nodes}
        self.assertTrue(
            {
                "finance.uzi.tier1.returns_attribution",
                "finance.uzi.tier1.holding_rebalance",
            }.issubset(portfolio_capabilities)
        )

    def test_api_executes_portfolio_graph_and_commits_both_artifacts(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                    "openai_api_key": None,
                }
            )
            with TestClient(create_app(settings=settings, api_config=ApiConfig(api_token="tier1-test"))) as client:
                response = client.post(
                    "/api/v1/runs",
                    headers={"Authorization": "Bearer tier1-test"},
                    json={
                        "intent": "portfolio_research",
                        "user_query": "做组合收益归因和再平衡",
                        "constraints": {
                            "portfolio_holdings": [
                                {"ticker": "600519", "name": "贵州茅台", "weight": 60, "return_pct": 8, "value": 600000, "market": "A", "price": 1500},
                                {"ticker": "300750", "name": "宁德时代", "weight": 40, "return_pct": -2, "value": 400000, "market": "A", "price": 200},
                            ],
                            "benchmark_return": 3,
                            "rebalance_targets": {"600519": 50, "300750": 50},
                            "drift_threshold": 5,
                            "fee_model": {"A": {"commission_rate": 0.00025, "buy_stamp_rate": 0, "sell_stamp_rate": 0.0005}},
                        },
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                run_id = response.json()["run_id"]
                artifacts = client.get(
                    f"/api/v1/runs/{run_id}/artifacts",
                    headers={"Authorization": "Bearer tier1-test"},
                )
                self.assertEqual(artifacts.status_code, 200, artifacts.text)
                rows = artifacts.json()["artifacts"]
                artifact_types = {row["artifact_type"] for row in rows}
                self.assertIn("returns_attribution", artifact_types)
                self.assertIn("holding_rebalance", artifact_types)
                returns = next(row["content"] for row in rows if row["artifact_type"] == "returns_attribution")
                self.assertEqual(returns["total_return_pct"], 4.0)


if __name__ == "__main__":
    unittest.main()
