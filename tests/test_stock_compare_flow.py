from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.agents.collector.service import CollectorService
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.settings import Settings, ensure_workspace_layout
from research_system.schemas.protocol import RequestEnvelope
from research_system.services.task_router import TaskRouter


BANK_COMPARE_QUERY = (
    "\u4ee5 000001 \u5e73\u5b89\u94f6\u884c\u4e3a\u8d77\u70b9\uff0c\u6269\u5230\u94f6\u884c\u80a1\uff1a"
    "\u62db\u5546\u94f6\u884c\u3001\u5b81\u6ce2\u94f6\u884c\u3001\u5174\u4e1a\u94f6\u884c\u3001"
    "\u6c5f\u82cf\u94f6\u884c\u3001\u6210\u90fd\u94f6\u884c\u7b49,\u6700\u8fd1\u54ea\u51e0\u53ea"
    "\u80a1\u7968\u66f4\u503c\u5f97\u6295\u8d44"
)


class _CompareSkillRuntimeStub:
    def run(self, skill_name: str, **kwargs):
        symbol = str(kwargs.get("symbol") or "")
        if skill_name == "market_snapshot_skill":
            price_map = {
                "600519": 1500.0,
                "300750": 190.0,
                "002594": 220.0,
                "000001": 10.16,
                "600036": 46.2,
                "002142": 28.7,
                "601166": 21.4,
                "600919": 12.9,
                "601838": 19.6,
            }
            pct_map = {
                "600519": 1.2,
                "300750": 3.5,
                "002594": -0.6,
                "000001": 1.1,
                "600036": 0.8,
                "002142": 1.6,
                "601166": 0.5,
                "600919": 1.3,
                "601838": 1.4,
            }
            amount_map = {
                "600519": 5_600_000_000.0,
                "300750": 8_900_000_000.0,
                "002594": 7_200_000_000.0,
                "000001": 604_000_000.0,
                "600036": 2_100_000_000.0,
                "002142": 980_000_000.0,
                "601166": 1_500_000_000.0,
                "600919": 730_000_000.0,
                "601838": 680_000_000.0,
            }
            pb_map = {
                "000001": 0.42,
                "600036": 0.78,
                "002142": 0.73,
                "601166": 0.61,
                "600919": 0.69,
                "601838": 0.88,
            }
            pe_map = {
                "000001": 4.58,
                "600036": 7.21,
                "002142": 6.13,
                "601166": 5.37,
                "600919": 6.54,
                "601838": 7.85,
            }
            market_cap_map = {
                "000001": 197_164_128_891.68,
                "600036": 1_165_000_000_000.0,
                "002142": 324_000_000_000.0,
                "601166": 478_000_000_000.0,
                "600919": 238_000_000_000.0,
                "601838": 121_000_000_000.0,
            }
            total_shares_map = {
                "000001": 19_405_918_198.0,
                "600036": 25_220_984_560.0,
                "002142": 11_660_485_679.0,
                "601166": 20_774_190_751.0,
                "600919": 18_351_324_463.0,
                "601838": 6_171_748_039.0,
            }
            return {
                "snapshot_type": "market",
                "symbol": symbol,
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-30",
                "facts": {
                    "market_overview": {
                        "latest_price": price_map.get(symbol, 10.0),
                        "pct_change": pct_map.get(symbol, 0.0),
                        "amount": amount_map.get(symbol, 1_000_000_000.0),
                        "pb": pb_map.get(symbol),
                        "pe_ttm": pe_map.get(symbol),
                        "market_cap": market_cap_map.get(symbol),
                        "total_shares": total_shares_map.get(symbol),
                        "spot": {
                            "latest_price": price_map.get(symbol, 10.0),
                            "pct_change": pct_map.get(symbol, 0.0),
                            "amount": amount_map.get(symbol, 1_000_000_000.0),
                        }
                    }
                },
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        if skill_name == "fundamental_snapshot_skill":
            financial_map = {
                "600519": {"营业总收入": 1700.0, "归母净利润": 850.0},
                "300750": {"营业总收入": 4100.0, "归母净利润": 520.0},
                "002594": {"营业总收入": 6200.0, "归母净利润": 310.0},
                "000001": {"营业总收入": 352.77, "归母净利润": 145.23},
                "600036": {"营业总收入": 906.0, "归母净利润": 381.0},
                "002142": {"营业总收入": 184.0, "归母净利润": 73.0},
                "601166": {"营业总收入": 556.0, "归母净利润": 248.0},
                "600919": {"营业总收入": 210.0, "归母净利润": 98.0},
                "601838": {"营业总收入": 68.0, "归母净利润": 32.0},
            }
            info_map = {
                "600519": {"总资产": 2900.0, "总负债": 480.0},
                "300750": {"总资产": 5100.0, "总负债": 1400.0},
                "002594": {"总资产": 7800.0, "总负债": 4200.0},
                "000001": {"总资产": 58000.0, "总负债": 53000.0},
                "600036": {"总资产": 120000.0, "总负债": 110000.0},
                "002142": {"总资产": 32000.0, "总负债": 29000.0},
                "601166": {"总资产": 98000.0, "总负债": 90000.0},
                "600919": {"总资产": 41000.0, "总负债": 38000.0},
                "601838": {"总资产": 18000.0, "总负债": 16500.0},
            }
            bank_metrics_map = {
                "000001": {
                    "peer_pb_mrq": 0.4248,
                    "peer_pb_rank": 34.0,
                    "roe": 0.0837,
                    "peer_roe_3y_avg": 0.0920,
                    "peer_roe_rank": 23.0,
                    "peer_revenue_growth_ttm": -0.0609,
                    "peer_revenue_growth_ttm_vs_industry_avg": -0.0907,
                    "peer_net_profit_growth_ttm": -0.0140,
                    "peer_net_profit_growth_ttm_vs_industry_avg": -0.0421,
                    "capital_adequacy_ratio": 0.1346,
                    "core_tier1_capital_adequacy_ratio": 0.0951,
                    "net_interest_spread": 0.0176,
                    "nonperforming_loan_ratio": 0.0105,
                    "provision_coverage_ratio": 2.1959,
                    "asset_liability_ratio": 0.9163,
                    "dividend_payout_ratio": 0.285,
                    "pay_dividends_profits_interest_cash": 11_100_000_000.0,
                    "revenue_yoy": 0.019,
                    "net_profit_yoy": 0.058,
                    "interest_margin_proxy": 0.318,
                    "net_interest_spread_proxy": 0.025,
                    "deposit_growth_proxy": 0.021,
                    "loan_to_deposit_proxy": 0.182,
                    "provision_coverage_proxy": 2.45,
                    "is_bank_like": True,
                },
                "600036": {
                    "peer_pb_mrq": 0.80,
                    "peer_pb_rank": 4.0,
                    "roe": 0.1176,
                    "peer_roe_3y_avg": 0.1314,
                    "peer_roe_rank": 4.0,
                    "peer_revenue_growth_ttm": 0.0331,
                    "peer_revenue_growth_ttm_vs_industry_avg": 0.0033,
                    "peer_net_profit_growth_ttm": 0.0452,
                    "peer_net_profit_growth_ttm_vs_industry_avg": 0.0171,
                    "capital_adequacy_ratio": 0.1776,
                    "core_tier1_capital_adequacy_ratio": 0.1413,
                    "net_interest_spread": 0.0178,
                    "nonperforming_loan_ratio": 0.0094,
                    "provision_coverage_ratio": 3.8776,
                    "asset_liability_ratio": 0.9035,
                    "dividend_payout_ratio": 0.335,
                    "pay_dividends_profits_interest_cash": 50_800_000_000.0,
                    "revenue_yoy": 0.067,
                    "net_profit_yoy": 0.077,
                    "interest_margin_proxy": 0.421,
                    "net_interest_spread_proxy": 0.031,
                    "deposit_growth_proxy": 0.028,
                    "loan_to_deposit_proxy": 0.194,
                    "provision_coverage_proxy": 3.10,
                    "is_bank_like": True,
                },
                "002142": {
                    "peer_pb_mrq": 0.91,
                    "peer_pb_rank": 8.0,
                    "roe": 0.1080,
                    "peer_roe_3y_avg": 0.1285,
                    "peer_roe_rank": 5.0,
                    "peer_revenue_growth_ttm": 0.0465,
                    "peer_revenue_growth_ttm_vs_industry_avg": 0.0150,
                    "peer_net_profit_growth_ttm": 0.0530,
                    "peer_net_profit_growth_ttm_vs_industry_avg": 0.0220,
                    "capital_adequacy_ratio": None,
                    "core_tier1_capital_adequacy_ratio": None,
                    "net_interest_spread": 0.0176,
                    "nonperforming_loan_ratio": 0.0076,
                    "provision_coverage_ratio": 3.6939,
                    "asset_liability_ratio": 0.9070,
                    "dividend_payout_ratio": 0.301,
                    "pay_dividends_profits_interest_cash": 9_900_000_000.0,
                    "revenue_yoy": 0.061,
                    "net_profit_yoy": 0.081,
                    "interest_margin_proxy": 0.387,
                    "net_interest_spread_proxy": 0.029,
                    "deposit_growth_proxy": 0.030,
                    "loan_to_deposit_proxy": 0.205,
                    "provision_coverage_proxy": 2.88,
                    "is_bank_like": True,
                },
                "601166": {
                    "peer_pb_mrq": 0.58,
                    "peer_pb_rank": 16.0,
                    "roe": 0.1010,
                    "peer_roe_3y_avg": 0.1108,
                    "peer_roe_rank": 9.0,
                    "peer_revenue_growth_ttm": 0.0280,
                    "peer_revenue_growth_ttm_vs_industry_avg": -0.0020,
                    "peer_net_profit_growth_ttm": 0.0390,
                    "peer_net_profit_growth_ttm_vs_industry_avg": 0.0100,
                    "capital_adequacy_ratio": 0.1320,
                    "core_tier1_capital_adequacy_ratio": 0.0950,
                    "net_interest_spread": 0.0150,
                    "nonperforming_loan_ratio": 0.0108,
                    "provision_coverage_ratio": 2.2452,
                    "asset_liability_ratio": 0.9180,
                    "dividend_payout_ratio": 0.272,
                    "pay_dividends_profits_interest_cash": 21_600_000_000.0,
                    "revenue_yoy": 0.044,
                    "net_profit_yoy": 0.053,
                    "interest_margin_proxy": 0.351,
                    "net_interest_spread_proxy": 0.027,
                    "deposit_growth_proxy": 0.019,
                    "loan_to_deposit_proxy": 0.212,
                    "provision_coverage_proxy": 2.36,
                    "is_bank_like": True,
                },
                "600919": {
                    "peer_pb_mrq": 0.76,
                    "peer_pb_rank": 6.0,
                    "roe": 0.1240,
                    "peer_roe_3y_avg": 0.1352,
                    "peer_roe_rank": 3.0,
                    "peer_revenue_growth_ttm": 0.0510,
                    "peer_revenue_growth_ttm_vs_industry_avg": 0.0200,
                    "peer_net_profit_growth_ttm": 0.0610,
                    "peer_net_profit_growth_ttm_vs_industry_avg": 0.0300,
                    "capital_adequacy_ratio": 0.1224,
                    "core_tier1_capital_adequacy_ratio": 0.0850,
                    "net_interest_spread": 0.0171,
                    "nonperforming_loan_ratio": 0.0081,
                    "provision_coverage_ratio": 3.0836,
                    "asset_liability_ratio": 0.9020,
                    "dividend_payout_ratio": 0.318,
                    "pay_dividends_profits_interest_cash": 15_900_000_000.0,
                    "revenue_yoy": 0.073,
                    "net_profit_yoy": 0.088,
                    "interest_margin_proxy": 0.402,
                    "net_interest_spread_proxy": 0.032,
                    "deposit_growth_proxy": 0.031,
                    "loan_to_deposit_proxy": 0.198,
                    "provision_coverage_proxy": 3.22,
                    "is_bank_like": True,
                },
                "601838": {
                    "peer_pb_mrq": 0.82,
                    "peer_pb_rank": 5.0,
                    "roe": 0.1290,
                    "peer_roe_3y_avg": 0.1611,
                    "peer_roe_rank": 1.0,
                    "peer_revenue_growth_ttm": 0.0480,
                    "peer_revenue_growth_ttm_vs_industry_avg": 0.0170,
                    "peer_net_profit_growth_ttm": 0.0720,
                    "peer_net_profit_growth_ttm_vs_industry_avg": 0.0410,
                    "capital_adequacy_ratio": 0.1395,
                    "core_tier1_capital_adequacy_ratio": 0.0910,
                    "net_interest_spread": 0.0155,
                    "nonperforming_loan_ratio": 0.0068,
                    "provision_coverage_ratio": 4.2406,
                    "asset_liability_ratio": 0.8990,
                    "dividend_payout_ratio": 0.325,
                    "pay_dividends_profits_interest_cash": 5_200_000_000.0,
                    "revenue_yoy": 0.081,
                    "net_profit_yoy": 0.094,
                    "interest_margin_proxy": 0.438,
                    "net_interest_spread_proxy": 0.033,
                    "deposit_growth_proxy": 0.035,
                    "loan_to_deposit_proxy": 0.191,
                    "provision_coverage_proxy": 3.36,
                    "is_bank_like": True,
                },
            }
            return {
                "snapshot_type": "fundamental",
                "symbol": symbol,
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-30",
                "facts": {
                    "financial_abstract": financial_map.get(symbol, {}),
                    "info": info_map.get(symbol, {}),
                    "banking_metrics": bank_metrics_map.get(symbol, {"is_bank_like": False}),
                },
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        if skill_name == "notice_snapshot_skill":
            return {
                "snapshot_type": "notice",
                "symbol": symbol,
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-30",
                "facts": {"notice_count": 2},
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        raise AssertionError(f"unexpected skill: {skill_name}")


class StockCompareFlowTests(unittest.TestCase):
    def test_router_identifies_stock_compare_for_named_multi_stock_query(self) -> None:
        router = TaskRouter()

        decision = router.identify(RequestEnvelope(user_query="分析茅台，宁德时代，比亚迪，谁更值得投资"))

        self.assertEqual(decision.intent, "stock_compare")

    def test_collector_resolves_named_compare_targets(self) -> None:
        collector = CollectorService(_CompareSkillRuntimeStub())

        payload = collector.run(intent="stock_compare", query="分析茅台，宁德时代，比亚迪，谁更值得投资")

        self.assertEqual(payload["sources"][0]["source_type"], "compare_request")
        self.assertEqual(payload["sources"][0]["symbols"], ["600519", "300750", "002594"])
        self.assertEqual(
            [item["symbol"] for item in payload["sources"][0]["compare_targets"]],
            ["600519", "300750", "002594"],
        )
        self.assertEqual(len(payload["snapshots"]), 9)
    def test_collector_compare_invocations_emit_symbol_scoped_trace_context(self) -> None:
        collector = CollectorService(_CompareSkillRuntimeStub())
        recorded_contexts: list[dict] = []
        original_invoke = collector.registry.invoke

        def _recording_invoke(invocation):
            recorded_contexts.append(dict(invocation.context or {}))
            return original_invoke(invocation)

        collector.registry.invoke = _recording_invoke  # type: ignore[assignment]

        collector.run(
            intent="stock_compare",
            query="compare targets",
            constraints={"symbols": ["600519", "300750", "002594"]},
        )

        context_pairs = {
            (
                str(context.get("task_name") or ""),
                str(context.get("agent_path") or ""),
            )
            for context in recorded_contexts
        }
        self.assertIn(
            ("collect:compare:600519:stock_snapshot_collector", "collector/compare/600519/stock_snapshot_collector"),
            context_pairs,
        )
        self.assertIn(
            ("collect:compare:300750:stock_snapshot_collector", "collector/compare/300750/stock_snapshot_collector"),
            context_pairs,
        )
        self.assertIn(
            ("collect:compare:002594:stock_snapshot_collector", "collector/compare/002594/stock_snapshot_collector"),
            context_pairs,
        )

    def test_orchestrator_runs_stock_compare_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                orchestrator.retriever.disable_qdrant_for_test()
                orchestrator.collector.skill_runtime = _CompareSkillRuntimeStub()

                response = orchestrator.run(RequestEnvelope(user_query="分析茅台，宁德时代，比亚迪，谁更值得投资"))

                self.assertEqual(response.intent, "stock_compare")
                self.assertEqual(response.status, "completed")
                report = (response.result or {}).get("report") or {}
                self.assertIn("已按同一研究口径对 3 只股票做对比", report.get("conclusion") or "")
                self.assertIn("宁德时代（300750）", report.get("conclusion") or "")
                self.assertTrue(report.get("methodologies"))
            finally:
                orchestrator.close()

    def test_collector_compare_uses_planning_hints_to_add_capital_flow_snapshots(self) -> None:
        collector = CollectorService(_CompareSkillRuntimeStub())

        payload = collector.run(
            intent="stock_compare",
            query="分析茅台，宁德时代，比亚迪，谁更值得投资",
            constraints={
                "planning_context_hints": {
                    "recommended_next_step": "supplement same-basis cashflow and valuation assumptions",
                    "thread_topic": "compare maotai and byd",
                }
            },
        )

        strategy = payload["sources"][0]["collection_strategy"]
        self.assertEqual(strategy["preferred_methodology"], "cashflow_resilience")
        self.assertIn("capital_flow", strategy["hint_augmented_snapshot_types"])

    def test_router_and_collector_expand_bank_peers_from_single_stock_start(self) -> None:
        router = TaskRouter()
        collector = CollectorService(_CompareSkillRuntimeStub())

        decision = router.identify(RequestEnvelope(user_query=BANK_COMPARE_QUERY))
        payload = collector.run(intent="stock_compare", query=BANK_COMPARE_QUERY)

        self.assertEqual(decision.intent, "stock_compare")
        self.assertEqual((decision.trace or {}).get("source"), "query_hint")
        self.assertEqual(
            payload["sources"][0]["symbols"],
            ["000001", "600036", "002142", "601166", "600919", "601838"],
        )
        self.assertEqual(len(payload["snapshots"]), 18)

    def test_orchestrator_bank_compare_uses_scorecard_for_ranked_conclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                orchestrator.retriever.disable_qdrant_for_test()
                orchestrator.collector.skill_runtime = _CompareSkillRuntimeStub()

                response = orchestrator.run(RequestEnvelope(user_query=BANK_COMPARE_QUERY))

                self.assertEqual(response.intent, "stock_compare")
                self.assertEqual(response.status, "completed")
                report = (response.result or {}).get("report") or {}
                conclusion = report.get("conclusion") or ""
                self.assertIn("同口径银行股指标", conclusion)
                self.assertIn("PB", conclusion)
                self.assertIn("成都银行", conclusion)
                self.assertIn("江苏银行", conclusion)
                self.assertIn("招商银行", conclusion)
                self.assertTrue(report.get("methodologies"))
                self.assertIn("成都银行", conclusion)
            finally:
                orchestrator.close()

    def test_orchestrator_bank_compare_scorecard_includes_dividend_yield_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                orchestrator.retriever.disable_qdrant_for_test()
                orchestrator.collector.skill_runtime = _CompareSkillRuntimeStub()

                response = orchestrator.run(RequestEnvelope(user_query=BANK_COMPARE_QUERY))

                self.assertEqual(response.intent, "stock_compare")
                self.assertEqual(response.status, "completed")
                report = (response.result or {}).get("report") or {}
                scorecard = next(
                    (
                        item
                        for item in list(report.get("methodologies") or [])
                        if str(item.get("source_type") or item.get("metadata", {}).get("source_type") or "") == "bank_compare_scorecard"
                    ),
                    None,
                )
                self.assertIsNotNone(scorecard)
                scorecard_summary = str((scorecard or {}).get("summary") or "")
                self.assertIn("dividend_yield_proxy", scorecard_summary)
            finally:
                orchestrator.close()

    def test_orchestrator_bank_compare_scorecard_includes_peer_bank_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                orchestrator.retriever.disable_qdrant_for_test()
                orchestrator.collector.skill_runtime = _CompareSkillRuntimeStub()

                response = orchestrator.run(RequestEnvelope(user_query=BANK_COMPARE_QUERY))

                self.assertEqual(response.intent, "stock_compare")
                self.assertEqual(response.status, "completed")
                report = (response.result or {}).get("report") or {}
                scorecard = next(
                    (
                        item
                        for item in list(report.get("methodologies") or [])
                        if str(item.get("source_type") or item.get("metadata", {}).get("source_type") or "") == "bank_compare_scorecard"
                    ),
                    None,
                )
                self.assertIsNotNone(scorecard)
                scorecard_summary = str((scorecard or {}).get("summary") or "")
                self.assertIn("peer_roe_3y_avg", scorecard_summary)
                self.assertIn("peer_net_profit_growth_ttm", scorecard_summary)
                self.assertIn("\u540c\u4e1aROE", scorecard_summary)
                self.assertIn("peer_pb_mrq", scorecard_summary)
                self.assertIn("\u540c\u4e1aPB", scorecard_summary)
                self.assertIn("nonperforming_loan_ratio", scorecard_summary)
                self.assertIn("provision_coverage_ratio", scorecard_summary)
                self.assertIn("net_interest_spread", scorecard_summary)
                self.assertIn("\u8d44\u4ea7\u8d28\u91cf", scorecard_summary)
                self.assertIn("\u8d44\u672c\u7f13\u51b2", scorecard_summary)
                self.assertIn("\u6838\u5fc3\u4e00\u7ea7", scorecard_summary)
                self.assertIn("\u51c0\u606f\u5dee", scorecard_summary)
            finally:
                orchestrator.close()

    def test_orchestrator_bank_compare_does_not_report_single_bank_gap_as_global_metric_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                orchestrator.retriever.disable_qdrant_for_test()
                orchestrator.collector.skill_runtime = _CompareSkillRuntimeStub()

                response = orchestrator.run(RequestEnvelope(user_query=BANK_COMPARE_QUERY))

                report = (response.result or {}).get("report") or {}
                conclusion = report.get("conclusion") or ""
                self.assertNotIn("仍有缺口指标：dividend_payout_ratio", conclusion)
            finally:
                orchestrator.close()


if __name__ == "__main__":
    unittest.main()
