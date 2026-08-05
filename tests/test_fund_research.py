from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from domain_packs.finance_ashare.skills.fund_snapshot.skill import FundSnapshotSkill
from domain_packs.finance_ashare.skills.uzi_source_snapshot.skill import (
    PeersSnapshotSkill,
    ValuationSnapshotSkill,
)
from domain_packs.finance_ashare.subagents.fund_methodology import build_fund_analysis
from domain_packs.finance_ashare.subagents.runtime import collect_fund_snapshot
from domain_packs.finance_ashare.subagents.uzi_methodology import build_features
from domain_packs.finance_ashare.subagents.uzi_panel_rules import evaluate_investor
from domain_packs.finance_ashare.tools.fund_master import FundResolution, FundSecurityMaster
from research_system.api.errors import ApiError
from research_system.api.app import create_app
from research_system.api.config import ApiConfig
from research_system.api.validation import validate_research_request
from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.intake.pack_intake import PackIntakeService
from research_system.runtime.planning.capability_planner import CapabilityPlanner
from research_system.runtime.platform.settings import Settings
from research_system.schemas.domain_pack import CapabilityPlanningRequest
from research_system.schemas.protocol import RequestEnvelope


def _catalog_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"基金代码": "110011", "基金简称": "易方达优质精选混合", "基金类型": "混合型-偏股"},
            {"基金代码": "005827", "基金简称": "易方达蓝筹精选混合", "基金类型": "混合型-偏股"},
            {"基金代码": "000001", "基金简称": "华夏成长混合", "基金类型": "混合型-灵活"},
        ]
    )


def _ranking_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"基金代码": "110011", "基金简称": "易方达优质精选混合", "日期": "2026-07-31", "单位净值": 4.1, "近1年": 35.0, "近2年": 50.0, "近3年": 75.0, "近6月": 15.0, "近3月": 8.0, "手续费": "0.15%"},
            {"基金代码": "005827", "基金简称": "易方达蓝筹精选混合", "日期": "2026-07-31", "单位净值": 2.2, "近1年": 22.0, "近2年": 37.0, "近3年": 55.0, "近6月": 10.0, "近3月": 5.0, "手续费": "0.12%"},
            {"基金代码": "000001", "基金简称": "华夏成长混合", "日期": "2026-07-31", "单位净值": 1.8, "近1年": 12.0, "近2年": 25.0, "近3年": 36.0, "近6月": 7.0, "近3月": 4.0, "手续费": "0.18%"},
        ]
    )


def _nav_frame(code: str) -> pd.DataFrame:
    base = {"110011": 1.0, "005827": 1.2, "000001": 1.4}[code]
    rows = []
    for index in range(90):
        # All values are source-like observations; no default is inserted by the methodology.
        value = base * (1 + index * {"110011": 0.004, "005827": 0.003, "000001": 0.002}[code])
        if code == "110011" and 45 <= index <= 50:
            value *= 0.9
        rows.append({"净值日期": date(2026, 1, 1) + timedelta(days=index), "单位净值": value})
    return pd.DataFrame(rows)


def _holdings_frame(_code: str, _year: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"序号": 1, "股票代码": "600519", "股票名称": "贵州茅台", "占净值比例": 9.5, "季度": "2026年2季度"},
            {"序号": 2, "股票代码": "00700", "股票名称": "腾讯控股", "占净值比例": 8.2, "季度": "2026年2季度"},
        ]
    )


def _overview_frame(code: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"item": "基金经理", "value": f"经理{code[-1]}"},
            {"item": "最新规模", "value": "100亿元"},
            {"item": "业绩比较基准", "value": "沪深300指数收益率×80%+中债指数收益率×20%"},
        ]
    )


def _achievement_payload(code: str) -> dict:
    fund_return = {"110011": 35.0, "005827": 22.0, "000001": 12.0}[code]
    benchmark_return = 10.0
    return {
        "stage_performance_list": [
            {
                "period_time": "近1年",
                "self_nav": str(fund_return),
                "standard_index_nav": str(benchmark_return),
                "self_max_draw_down": "12.0%",
                "standard_index_max_draw_down": "10.0%",
                "self_nav_rank": "1/10",
            }
        ],
        "annual_performance_list": [],
    }


def _manager_tenure_frame(code: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "起始期": "2024-01-01",
                "截止期": "至今",
                "基金经理": f"经理{code[-1]}",
                "任职期间": "2年",
                "任职回报": "20.0%",
            }
        ]
    )


def _scale_history_rows(_code: str) -> list[dict]:
    return [
        {
            "report_date": "2026-06-30",
            "subscriptions_100m": 1.0,
            "redemptions_100m": 2.0,
            "total_shares_100m": 10.0,
            "net_assets_100m_cny": 12.0,
            "reported_share_change_pct": -9.09,
        },
        {
            "report_date": "2026-03-31",
            "subscriptions_100m": 1.5,
            "redemptions_100m": 1.0,
            "total_shares_100m": 11.0,
            "net_assets_100m_cny": 13.0,
            "reported_share_change_pct": 4.76,
        },
    ]


class FundResearchTests(unittest.TestCase):
    def test_api_accepts_and_validates_fund_research_contract(self) -> None:
        request = validate_research_request(
            RequestEnvelope(
                intent="fund_research",
                user_query="比较两只基金",
                constraints={
                    "fund_codes": ["110011", "005827"],
                    "fund_category": "混合型",
                    "candidate_pool_size": 8,
                    "investment_cycle": "long_term",
                    "background": True,
                },
            )
        )
        self.assertEqual(request.intent, "fund_research")
        self.assertTrue(request.constraints["background"])

        with self.assertRaises(ApiError):
            validate_research_request(
                RequestEnvelope(
                    intent="fund_research",
                    user_query="比较基金",
                    constraints={"fund_codes": ["not-a-code"]},
                )
            )

        with self.assertRaises(ApiError):
            validate_research_request(
                RequestEnvelope(
                    intent="fund_research",
                    user_query="比较基金",
                    constraints={"fund_category": "混合型", "background": "yes"},
                )
            )

    def test_api_executes_fund_research_graph_and_commits_artifacts(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        payload = {
            "query": "比较基金",
            "fund_codes": ["110011", "005827"],
            "fund_category": "混合型",
            "investment_cycle": "long_term",
            "candidate_pool_size": 2,
            "funds": [
                {
                    "fund_code": "110011",
                    "fund_name": "基金 A",
                    "fund_type": "混合型",
                    "returns_pct": {"year_1": 20, "year_3": 50, "month_3": 5, "month_6": 10},
                    "risk": {"max_drawdown_pct": -10, "annualized_volatility_pct": 15},
                    "fee_pct": 0.15,
                    "benchmark_definition": "沪深300指数收益率×80%+中债指数收益率×20%",
                    "benchmark_performance": [
                        {
                            "period": "近1年",
                            "fund_return_pct": 20,
                            "benchmark_return_pct": 8,
                            "excess_return_pct": 12,
                        }
                    ],
                    "manager_tenures": [
                        {
                            "manager": "经理 A",
                            "start_date": "2024-01-01",
                            "end_date": None,
                            "current": True,
                            "tenure_return_pct": 20,
                        }
                    ],
                    "scale_change": {
                        "latest_report_date": "2026-06-30",
                        "latest_total_shares_100m": 10,
                        "latest_net_assets_100m_cny": 12,
                        "total_share_change_pct": -9.09,
                    },
                    "top_holdings": [],
                    "data_gap": ["fund_holdings_missing"],
                },
                {
                    "fund_code": "005827",
                    "fund_name": "基金 B",
                    "fund_type": "混合型",
                    "returns_pct": {"year_1": 10, "year_3": 30, "month_3": 3, "month_6": 6},
                    "risk": {"max_drawdown_pct": -15, "annualized_volatility_pct": 20},
                    "fee_pct": 0.2,
                    "top_holdings": [],
                    "data_gap": ["fund_holdings_missing"],
                },
            ],
            "quality_status": "degraded",
            "data_gap": [],
            "provider_errors": [],
            "source_refs": [{"type": "fixture", "id": "fund-e2e"}],
            "retrieved_at": "2026-08-03T00:00:00+00:00",
        }
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
            with patch(
                "domain_packs.finance_ashare.skills.fund_snapshot.skill.FundSnapshotSkill.run",
                return_value=payload,
            ):
                with TestClient(
                    create_app(settings=settings, api_config=ApiConfig(api_token="fund-test"))
                ) as client:
                    response = client.post(
                        "/api/v1/runs",
                        headers={"Authorization": "Bearer fund-test"},
                        json={
                            "intent": "fund_research",
                            "user_query": "比较 110011 和 005827",
                            "constraints": {
                                "fund_codes": ["110011", "005827"],
                                "fund_category": "混合型",
                                "candidate_pool_size": 2,
                                "investment_cycle": "long_term",
                            },
                        },
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    run_id = response.json()["run_id"]
                    artifacts = client.get(
                        f"/api/v1/runs/{run_id}/artifacts",
                        headers={"Authorization": "Bearer fund-test"},
                    )
                    self.assertEqual(artifacts.status_code, 200, artifacts.text)
                    artifact_types = {
                        row["artifact_type"]
                        for row in artifacts.json()["artifacts"]
                    }
                    self.assertTrue(
                        {"fund_snapshot", "fund_analysis", "research_report"}.issubset(
                            artifact_types
                        )
                    )
                    analysis_artifact = next(
                        row
                        for row in artifacts.json()["artifacts"]
                        if row["artifact_type"] == "fund_analysis"
                    )
                    first_candidate = analysis_artifact["content"]["candidates"][0]
                    self.assertEqual(
                        analysis_artifact["content"]["methodology_version"],
                        "uzi-3.9.2-fund-selection-v3",
                    )
                    self.assertEqual(first_candidate["benchmark_performance"][0]["excess_return_pct"], 12)
                    self.assertTrue(first_candidate["manager_tenures"][0]["current"])
                    self.assertEqual(first_candidate["scale_change"]["latest_total_shares_100m"], 10)

                    replay = client.get(
                        f"/api/v1/runs/{run_id}/replay",
                        headers={"Authorization": "Bearer fund-test"},
                    )
                    self.assertEqual(replay.status_code, 200, replay.text)
                    replay_types = {
                        row["artifact_type"] for row in replay.json()["artifacts"]
                    }
                    self.assertIn("fund_analysis", replay_types)

                    events = client.get(
                        f"/api/v1/runs/{run_id}/events",
                        headers={"Authorization": "Bearer fund-test"},
                    )
                    self.assertEqual(events.status_code, 200, events.text)
                    self.assertTrue(
                        any(
                            row["event_type"] == "artifact_committed"
                            for row in events.json()["events"]
                        )
                    )

    def test_fund_master_resolves_mutual_fund_before_bond_prefix_routing(self) -> None:
        master = FundSecurityMaster(catalog_loader=_catalog_frame)

        resolved = master.resolve("分析 110011")

        self.assertEqual(resolved.fund_codes, ["110011"])
        self.assertEqual(resolved.matches[0]["fund_name"], "易方达优质精选混合")

    def test_pack_intake_declares_fund_research_and_keeps_fund_codes_out_of_symbols(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        service = PackIntakeService(DomainPackRegistry.load(repository_root / "domain_packs"))
        resolution = FundResolution(
            fund_codes=["110011"],
            matches=[{"fund_code": "110011", "fund_name": "易方达优质精选混合"}],
            ambiguous_terms=[],
            source="test_fund_catalog",
        )

        with patch(
            "domain_packs.finance_ashare.subagents.task_intake._FUND_MASTER.resolve",
            return_value=resolution,
        ):
            decision = service.resolve(RequestEnvelope(user_query="分析 110011"))

        self.assertEqual(decision.status, "accepted")
        self.assertEqual(decision.intent, "fund_research")
        self.assertEqual(decision.slots["fund_codes"], ["110011"])
        self.assertNotIn("symbols", decision.slots)
        self.assertIn("finance.fund.analyze", decision.candidate_capabilities)

    def test_fund_snapshot_uses_real_rank_nav_and_holdings_fields(self) -> None:
        skill = FundSnapshotSkill(
            fund_master=FundSecurityMaster(catalog_loader=_catalog_frame),
            open_rank_loader=lambda _category: _ranking_frame(),
            exchange_rank_loader=lambda: pd.DataFrame(),
            nav_loader=_nav_frame,
            holdings_loader=_holdings_frame,
            overview_loader=_overview_frame,
            achievement_loader=_achievement_payload,
            manager_tenure_loader=_manager_tenure_frame,
            scale_history_loader=_scale_history_rows,
        )

        payload = skill.run(
            query="选择混合型基金",
            fund_category="混合型",
            candidate_pool_size=3,
            investment_cycle="long_term",
        )

        self.assertEqual(payload["fund_category"], "混合型")
        self.assertEqual(len(payload["funds"]), 3)
        first = payload["funds"][0]
        self.assertEqual(first["holdings_period"], "2026年2季度")
        self.assertEqual(first["top10_concentration_pct"], 17.7)
        self.assertGreater(first["risk"]["observation_count"], 2)
        self.assertNotIn("fund_holdings_missing", first["data_gap"])
        self.assertIn("沪深300", first["benchmark_definition"])
        self.assertEqual(first["benchmark_performance"][0]["excess_return_pct"], 25.0)
        self.assertTrue(first["manager_tenures"][0]["current"])
        self.assertEqual(first["scale_change"]["total_share_change_pct"], -9.090909)

    def test_fund_methodology_ranks_only_comparable_observed_evidence(self) -> None:
        skill = FundSnapshotSkill(
            fund_master=FundSecurityMaster(catalog_loader=_catalog_frame),
            open_rank_loader=lambda _category: _ranking_frame(),
            exchange_rank_loader=lambda: pd.DataFrame(),
            nav_loader=_nav_frame,
            holdings_loader=_holdings_frame,
            overview_loader=_overview_frame,
            achievement_loader=_achievement_payload,
            manager_tenure_loader=_manager_tenure_frame,
            scale_history_loader=_scale_history_rows,
        )
        snapshot = skill.run(
            query="选择混合型基金",
            fund_category="混合型",
            candidate_pool_size=3,
            investment_cycle="long_term",
        )

        analysis = build_fund_analysis(snapshot)

        self.assertEqual(analysis["candidate_count"], 3)
        self.assertTrue(analysis["recommendations"])
        eligible = [row for row in analysis["candidates"] if row["selection_eligible"]]
        expected_leader = max(eligible, key=lambda row: float(row["score"]))
        self.assertEqual(analysis["recommendations"][0]["fund_code"], expected_leader["fund_code"])
        self.assertGreaterEqual(analysis["recommendations"][0]["score_coverage"], 0.6)
        self.assertIn("缺失字段不补 0", analysis["selection_policy"]["non_imputation_rule"])
        self.assertIn("benchmark_excess_1y", analysis["candidates"][0]["metrics"])
        self.assertEqual(analysis["methodology_version"], "uzi-3.9.2-fund-selection-v3")

    def test_fund_without_three_year_return_uses_complete_since_inception_history(self) -> None:
        ranking = _ranking_frame().iloc[[0]].copy()
        ranking = ranking.drop(columns=["近3年"])
        ranking.loc[:, "成立来"] = 50.0
        nav = pd.DataFrame(
            [
                {
                    "净值日期": date(2024, 1, 1) + timedelta(days=index),
                    "单位净值": 1.0 + index / 2_000,
                }
                for index in range(800)
            ]
        )
        skill = FundSnapshotSkill(
            fund_master=FundSecurityMaster(catalog_loader=_catalog_frame),
            open_rank_loader=lambda _category: ranking,
            exchange_rank_loader=lambda: pd.DataFrame(),
            nav_loader=lambda _code: nav,
            holdings_loader=_holdings_frame,
            overview_loader=_overview_frame,
            achievement_loader=_achievement_payload,
            manager_tenure_loader=_manager_tenure_frame,
            scale_history_loader=_scale_history_rows,
        )

        snapshot = skill.run(
            query="分析成立不足三年的基金",
            fund_codes=["110011"],
            candidate_pool_size=1,
            investment_cycle="long_term",
        )
        fund = snapshot["funds"][0]
        analysis = build_fund_analysis(snapshot)
        candidate = analysis["candidates"][0]

        self.assertEqual(len(fund["nav_history"]), 800)
        self.assertEqual(fund["risk"]["period_start"], "2024-01-01")
        self.assertIn("return_3y_annualized", candidate["metrics"])
        self.assertNotIn("return_3y_annualized", candidate["data_gap"])
        self.assertAlmostEqual(candidate["metrics"]["return_3y_annualized"], 20.3637, places=4)

    def test_missing_fund_metrics_do_not_become_zero_or_a_recommendation(self) -> None:
        analysis = build_fund_analysis(
            {
                "fund_category": "混合型",
                "investment_cycle": "long_term",
                "funds": [
                    {
                        "fund_code": "110011",
                        "fund_name": "易方达优质精选混合",
                        "fund_type": "混合型-偏股",
                        "returns_pct": {},
                        "risk": {},
                        "fee_pct": None,
                        "data_gap": ["fund_ranking_missing", "nav_history_missing"],
                    }
                ],
                "data_gap": [],
            }
        )

        self.assertEqual(analysis["recommendations"], [])
        candidate = analysis["candidates"][0]
        self.assertIsNone(candidate["score"])
        self.assertNotIn("return_1y", candidate["metrics"])
        self.assertIn("no_fund_met_selection_evidence_gate", analysis["data_gap"])

    def test_fund_intent_plans_dedicated_agent_os_chain(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        registry = DomainPackRegistry.load(repository_root / "domain_packs")
        graph = CapabilityPlanner(registry).build_graph(
            CapabilityPlanningRequest(
                domain_id="finance_ashare",
                objective="选择混合型基金",
                available_artifact_types=["user_request"],
                required_artifact_types=[],
                metadata={
                    "intent": "fund_research",
                    "intent_inputs": {"fund_category": "混合型", "candidate_pool_size": 3},
                },
            )
        )

        capabilities = [node.capability for node in graph.nodes]
        self.assertIn("finance.fund.snapshot.collect", capabilities)
        self.assertIn("finance.fund.analyze", capabilities)
        self.assertIn("finance.fund.report.compose", capabilities)
        self.assertNotIn("finance.ashare.snapshot.collect", capabilities)
        fund_node = next(
            node for node in graph.nodes
            if node.capability == "finance.fund.snapshot.collect"
        )
        self.assertEqual(fund_node.tools, ["fund_data_provider"])

        stock_graph = CapabilityPlanner(registry).build_graph(
            CapabilityPlanningRequest(
                domain_id="finance_ashare",
                objective="分析 600000",
                available_artifact_types=["user_request"],
                required_artifact_types=[],
                metadata={
                    "intent": "stock_check",
                    "intent_inputs": {"symbols": ["600000"]},
                },
            )
        )
        stock_node = next(
            node for node in stock_graph.nodes
            if node.capability == "finance.ashare.snapshot.collect"
        )
        self.assertEqual(stock_node.tools, ["financial_data_provider"])

    def test_fund_snapshot_runs_through_governed_pack_tool(self) -> None:
        query = SimpleNamespace(
            artifact_id="normalized-fund-query",
            content={
                "query": "分析 110011",
                "fund_codes": ["110011"],
                "fund_category": "混合型",
            },
        )
        calls: list[tuple[str, dict]] = []

        def call_tool(name: str, arguments: dict):
            calls.append((name, arguments))
            return SimpleNamespace(
                status="completed",
                output={
                    "query": arguments["query"],
                    "fund_codes": ["110011"],
                    "fund_category": "混合型",
                    "funds": [],
                    "quality_status": "data_gap",
                    "data_gap": ["fund_candidates_unavailable"],
                    "provider_errors": [],
                    "source_refs": [{"type": "fund_catalog", "id": "catalog"}],
                    "retrieved_at": "2026-08-03T00:00:00+00:00",
                },
                source_refs=[{"type": "fund_catalog", "id": "catalog"}],
            )

        context = SimpleNamespace(
            node=SimpleNamespace(inputs={"candidate_pool_size": 3, "investment_cycle": "long_term"}),
            require_artifact=lambda _artifact_type: query,
            call_tool=call_tool,
        )

        draft = collect_fund_snapshot(context)

        self.assertEqual(calls[0][0], "fund_data_provider")
        self.assertEqual(calls[0][1]["fund_codes"], ["110011"])
        self.assertEqual(draft.artifact_type, "fund_snapshot")
        self.assertEqual(draft.metadata["quality_status"], "data_gap")
        self.assertEqual(draft.source_refs[0]["id"], "catalog")

    def test_uzi_392_ocf_contract_does_not_alias_ocf_to_fcf(self) -> None:
        features = build_features(
            [
                {
                    "snapshot_type": "fundamental",
                    "symbol": "600519",
                    "facts": {
                        "financial_abstract": {
                            "net_profit": 10,
                            "operating_cash_flow": 12,
                        }
                    },
                }
            ],
            "600519",
        )

        self.assertEqual(features["operating_cash_flow_yi"], 12)
        self.assertEqual(features["ocf_to_net_income_ratio"], 1.2)
        self.assertIsNone(features["fcf_latest_yi"])

    def test_uzi_392_peers_missing_industry_returns_explicit_self_only_fallback(self) -> None:
        skill = PeersSnapshotSkill()
        with (
            patch(
                "domain_packs.finance_ashare.skills.uzi_source_snapshot.skill.FundamentalSnapshotSkill.run",
                return_value={"facts": {"info": {"股票简称": "测试公司"}}},
            ),
            patch.object(
                skill,
                "_industry_context",
                return_value={"availability": "data_gap", "data_gap": ["industry_missing"]},
            ),
        ):
            facts = skill._collect(query="分析测试公司", symbol="600000")

        self.assertEqual(facts["availability"], "degraded")
        self.assertTrue(facts["comparison"]["fallback_used"])
        self.assertEqual(facts["comparison"]["self_only"][0]["symbol"], "600000")
        self.assertIn("peer_group_missing", facts["data_gap"])

    def test_uzi_392_valuation_records_cninfo_market_pe_fallback(self) -> None:
        skill = ValuationSnapshotSkill()
        pe_frame = pd.DataFrame({"行业加权市盈率": [10.0, 20.0, 600.0, None]})
        with (
            patch(
                "domain_packs.finance_ashare.skills.uzi_source_snapshot.skill.AkShareMarketAdapter.get_market_overview",
                return_value={"valuation": {"PE(TTM)": 15.0}},
            ),
            patch.object(
                skill,
                "_industry_context",
                return_value={"availability": "data_gap", "data_gap": ["industry_missing"]},
            ),
            patch(
                "domain_packs.finance_ashare.skills.uzi_source_snapshot.skill.ak.stock_industry_pe_ratio_cninfo",
                return_value=pe_frame,
            ),
        ):
            facts = skill._collect(query="分析 600000", symbol="600000")

        self.assertEqual(facts["valuation"]["industry_pe"], 15.0)
        self.assertIn("cninfo 市场加权均值", facts["valuation"]["industry_pe_fallback_reason"])
        self.assertEqual(facts["valuation"]["industry_pe_source"]["scope"], "market_weighted_average")

    def test_cash_profit_rule_is_skipped_when_ocf_ratio_is_missing(self) -> None:
        result = evaluate_investor("chanos", {})

        self.assertIn("cash_matches_eps", result["data_gap_rules"])
        self.assertEqual(result["pass_rules"], [])
        self.assertEqual(result["fail_rules"], [])
        self.assertEqual(result["signal"], "insufficient_evidence")
        self.assertTrue(
            all(row["rule_id"] != "cash_matches_eps" for row in result["pass_rules"])
        )


if __name__ == "__main__":
    unittest.main()
