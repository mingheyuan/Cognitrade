from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.agents.collector.service import CollectorService
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.settings import Settings, ensure_workspace_layout
from research_system.schemas.protocol import RequestEnvelope
from research_system.services.task_router import TaskRouter


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
                "600519": 5600000000.0,
                "300750": 8900000000.0,
                "002594": 7200000000.0,
                "000001": 604000000.0,
                "600036": 2100000000.0,
                "002142": 980000000.0,
                "601166": 1500000000.0,
                "600919": 730000000.0,
                "601838": 680000000.0,
            }
            return {
                "snapshot_type": "market",
                "symbol": symbol,
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-30",
                "facts": {
                    "market_overview": {
                        "spot": {
                            "最新价": price_map.get(symbol, 10.0),
                            "涨跌幅": pct_map.get(symbol, 0.0),
                            "成交额": amount_map.get(symbol, 1000000000.0),
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
            return {
                "snapshot_type": "fundamental",
                "symbol": symbol,
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-30",
                "facts": {
                    "financial_abstract": financial_map.get(symbol, {}),
                    "info": info_map.get(symbol, {}),
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

    def test_orchestrator_runs_stock_compare_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                orchestrator.retriever.disable_qdrant_for_test()
                stub_runtime = _CompareSkillRuntimeStub()
                orchestrator.collector.skill_runtime = stub_runtime

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
        query = "以 000001 平安银行为起点，扩到银行股：招商银行、宁波银行、兴业银行、江苏银行、成都银行等,最近哪几只股票更值得投资"
        router = TaskRouter()
        collector = CollectorService(_CompareSkillRuntimeStub())

        decision = router.identify(RequestEnvelope(user_query=query))
        payload = collector.run(intent="stock_compare", query=query)

        self.assertEqual(decision.intent, "stock_compare")
        self.assertEqual((decision.trace or {}).get("source"), "query_hint")
        self.assertEqual(
            payload["sources"][0]["symbols"],
            ["000001", "600036", "002142", "601166", "600919", "601838"],
        )
        self.assertEqual(len(payload["snapshots"]), 18)


if __name__ == "__main__":
    unittest.main()
