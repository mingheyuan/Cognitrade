from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_system.agents.collector.service import CollectorService
from research_system.agents.screener.service import ScreenerService
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.settings import Settings, ensure_workspace_layout
from research_system.schemas.protocol import RequestEnvelope
from research_system.services.task_router import TaskRouter


class _SkillRuntimeStub:
    def run(self, skill_name: str, **kwargs):
        if skill_name == "sector_snapshot_skill":
            return {
                "snapshot_type": "sector",
                "sector": "创新药",
                "theme": "创新药",
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-28",
                "facts": {
                    "top_constituents": [
                        {"code": "300363", "name": "博腾股份"},
                        {"code": "300347", "name": "泰格医药"},
                    ]
                },
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        if skill_name == "market_snapshot_skill":
            symbol = kwargs.get("symbol")
            return {
                "snapshot_type": "market",
                "symbol": symbol,
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-28",
                "facts": {
                    "market_overview": {
                        "spot": {
                            "最新价": 12.3 if symbol == "300363" else 45.6,
                            "涨跌幅": 2.1 if symbol == "300363" else 0.8,
                            "成交额": 1800000000 if symbol == "300363" else 1100000000,
                        }
                    }
                },
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        if skill_name == "fundamental_snapshot_skill":
            return {
                "snapshot_type": "fundamental",
                "symbol": kwargs.get("symbol"),
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-28",
                "facts": {"financial_abstract": {"营业总收入": 100}},
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        if skill_name == "notice_snapshot_skill":
            return {
                "snapshot_type": "notice",
                "symbol": kwargs.get("symbol"),
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-28",
                "facts": {"notice_count": 3},
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        raise AssertionError(f"unexpected skill: {skill_name}")


class TaskRouterStockScreenTests(unittest.TestCase):
    def test_router_identifies_stock_screen_for_recommendation_query(self) -> None:
        router = TaskRouter()
        decision = router.identify(RequestEnvelope(user_query="最近有什么股票值得投入"))
        self.assertEqual(decision.intent, "stock_screen")
        self.assertIsNone(decision.clarification)

    def test_router_identifies_stock_screen_when_cycle_is_present(self) -> None:
        router = TaskRouter()
        decision = router.identify(RequestEnvelope(user_query="最近有什么股票值得投入 中线"))
        self.assertEqual(decision.intent, "stock_screen")


class StockScreenFlowTests(unittest.TestCase):
    def test_collector_builds_candidates_and_sets_default_cycle_assumption(self) -> None:
        collector = CollectorService(_SkillRuntimeStub())
        with patch(
            "research_system.subagents.collector.call_with_retry",
            return_value=self._spot_table(),
        ):
            payload = collector.run(intent="stock_screen", query="最近有什么股票值得投入")
        self.assertEqual(payload["sources"][0]["source_type"], "screen_request")
        self.assertEqual(payload["sources"][0]["investment_cycle"], "mid_term")
        self.assertEqual(payload["sources"][0]["investment_cycle_assumption"], "default_mid_term")
        self.assertGreaterEqual(len(payload["sources"][0]["candidate_universe"]), 2)
        self.assertEqual(len(payload["snapshots"]), 0)

    def test_screener_ranks_candidates(self) -> None:
        screener = ScreenerService()
        payload = screener.run(
            query="最近有什么股票值得投入",
            source_payloads=[
                {
                    "candidate_universe": [
                        {"symbol": "300363", "name": "博腾股份", "sector": "创新药", "theme": "创新药"},
                        {"symbol": "300347", "name": "泰格医药", "sector": "创新药", "theme": "创新药"},
                    ],
                    "sector_context": {"sector": "创新药", "theme": "创新药"},
                    "investment_cycle": "mid_term",
                }
            ],
            snapshot_payloads=[
                {
                    "snapshot_type": "market",
                    "symbol": "300363",
                    "facts": {"market_overview": {"spot": {"最新价": 12.3, "涨跌幅": 2.1, "成交额": 1800000000}}},
                },
                {
                    "snapshot_type": "fundamental",
                    "symbol": "300363",
                    "facts": {"financial_abstract": {"营业总收入": 100}},
                },
                {
                    "snapshot_type": "notice",
                    "symbol": "300363",
                    "facts": {"notice_count": 3},
                },
                {
                    "snapshot_type": "market",
                    "symbol": "300347",
                    "facts": {"market_overview": {"spot": {"最新价": 45.6, "涨跌幅": 0.8, "成交额": 1100000000}}},
                },
                {
                    "snapshot_type": "fundamental",
                    "symbol": "300347",
                    "facts": {"financial_abstract": {"营业总收入": 80}},
                },
                {
                    "snapshot_type": "notice",
                    "symbol": "300347",
                    "facts": {"notice_count": 1},
                },
            ],
            constraints={"top_k": 2},
        )
        self.assertEqual(len(payload["screened_candidates"]), 2)
        self.assertEqual(payload["screened_candidates"][0]["symbol"], "300363")
        self.assertIn("优先关注", payload["scorecard"]["summary"])
        self.assertEqual(payload["enrichment_targets"], [])
        self.assertEqual(payload["scorecard"]["deep_checked_count"], 2)

    def test_screener_marks_deep_checked_candidates_after_enrichment(self) -> None:
        screener = ScreenerService()
        payload = screener.run(
            query="最近有什么股票值得投入 中线",
            source_payloads=[
                {
                    "candidate_universe": [
                        {"symbol": "300363", "name": "博腾股份"},
                        {"symbol": "300347", "name": "泰格医药"},
                    ],
                    "investment_cycle": "mid_term",
                }
            ],
            snapshot_payloads=[
                {
                    "snapshot_type": "market",
                    "symbol": "300363",
                    "facts": {"market_overview": {"spot": {"最新价": 12.3, "涨跌幅": 2.1, "成交额": 1800000000}}},
                },
                {
                    "snapshot_type": "fundamental",
                    "symbol": "300363",
                    "facts": {"financial_abstract": {"营业总收入": 100}},
                },
                {
                    "snapshot_type": "notice",
                    "symbol": "300363",
                    "facts": {"notice_count": 3},
                },
            ],
            constraints={"top_k": 2, "skip_enrichment": True, "min_score": 0.0},
        )
        self.assertTrue(payload["screened_candidates"][0]["deep_checked"])
        self.assertEqual(payload["scorecard"]["deep_checked_count"], 1)
        self.assertEqual(payload["scorecard"]["enrichment_target_count"], 0)

    def test_screener_returns_watchlist_candidates_below_core_threshold(self) -> None:
        screener = ScreenerService()
        payload = screener.run(
            query="最近有什么股票值得投入 长期",
            source_payloads=[
                {
                    "candidate_universe": [
                        {"symbol": "000725", "name": "京东方Ａ", "latest_price": 7.79, "latest_pct_change": 3.59, "latest_amount": 35676682214.0},
                        {"symbol": "603986", "name": "兆易创新", "latest_price": 770.0, "latest_pct_change": -0.672, "latest_amount": 37418302893.0},
                        {"symbol": "300308", "name": "中际旭创", "latest_price": 1253.89, "latest_pct_change": -5.252, "latest_amount": 44480805076.0},
                    ],
                    "investment_cycle": "long_term",
                }
            ],
            snapshot_payloads=[],
            constraints={"top_k": 5, "min_score": 6.0, "watchlist_top_k": 2, "watchlist_min_score": 0.0},
        )
        self.assertEqual(payload["screened_candidates"], [])
        self.assertEqual([item["symbol"] for item in payload["watchlist_candidates"]], ["000725", "603986"])
        self.assertEqual(payload["scorecard"]["watchlist_candidate_count"], 2)
        self.assertEqual(payload["scorecard"]["top_candidate_count"], 0)

    def test_screener_filters_candidates_below_min_score(self) -> None:
        screener = ScreenerService()
        payload = screener.run(
            query="最近有什么股票值得投入 中线",
            source_payloads=[
                {
                    "candidate_universe": [
                        {"symbol": "300363", "name": "博腾股份"},
                        {"symbol": "300347", "name": "泰格医药"},
                    ],
                    "investment_cycle": "mid_term",
                }
            ],
            snapshot_payloads=[
                {
                    "snapshot_type": "market",
                    "symbol": "300363",
                    "facts": {"market_overview": {"spot": {"最新价": 12.3, "涨跌幅": 3.2, "成交额": 1800000000}}},
                },
                {
                    "snapshot_type": "market",
                    "symbol": "300347",
                    "facts": {"market_overview": {"spot": {"最新价": 45.6, "涨跌幅": -5.5, "成交额": 1100000000}}},
                },
            ],
            constraints={"top_k": 5, "min_score": 3.0, "watchlist_min_score": 3.0},
        )
        self.assertEqual(payload["screened_candidates"], [])
        self.assertEqual(payload["scorecard"]["eligible_candidate_count"], 0)
        self.assertIn("未筛出满足最小证据要求的候选", payload["scorecard"]["summary"])

    def test_screener_allows_override_min_score(self) -> None:
        screener = ScreenerService()
        payload = screener.run(
            query="最近有什么股票值得投入 中线",
            source_payloads=[
                {
                    "candidate_universe": [
                        {"symbol": "300363", "name": "博腾股份"},
                        {"symbol": "300347", "name": "泰格医药"},
                    ],
                    "investment_cycle": "mid_term",
                }
            ],
            snapshot_payloads=[
                {
                    "snapshot_type": "market",
                    "symbol": "300363",
                    "facts": {"market_overview": {"spot": {"最新价": 12.3, "涨跌幅": 3.2, "成交额": 1800000000}}},
                },
                {
                    "snapshot_type": "fundamental",
                    "symbol": "300363",
                    "facts": {"financial_abstract": {"营业总收入": 100}},
                },
                {
                    "snapshot_type": "notice",
                    "symbol": "300363",
                    "facts": {"notice_count": 3},
                },
            ],
            constraints={"top_k": 5, "min_score": 0.0},
        )
        self.assertEqual([item["symbol"] for item in payload["screened_candidates"]], ["300363"])
        self.assertEqual(payload["scorecard"]["eligible_candidate_count"], 1)

    def test_orchestrator_runs_stock_screen_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                orchestrator.retriever.qdrant_available = False
                orchestrator.retriever.qdrant_error = "disabled_for_test"
                orchestrator.retriever.registry._subagents["blackboard_evidence_indexer"].qdrant = None
                orchestrator.retriever.registry._subagents["qdrant_evidence_searcher"].qdrant = None
                stub_runtime = _SkillRuntimeStub()
                orchestrator.collector.skill_runtime = stub_runtime
                with patch.object(orchestrator.collector.registry._subagents["stock_snapshot_collector"], "skill_runtime", stub_runtime), \
                    patch.object(orchestrator.collector.registry._subagents["sector_snapshot_collector"], "skill_runtime", stub_runtime), \
                    patch.object(orchestrator.collector.registry._subagents["candidate_universe_builder"], "skill_runtime", stub_runtime), \
                    patch("research_system.subagents.collector.call_with_retry", return_value=self._spot_table()):
                    response = orchestrator.run(RequestEnvelope(user_query="最近有什么股票值得投入 中线"))
                self.assertEqual(response.intent, "stock_screen")
                self.assertEqual(response.status, "completed")
                result = response.result or {}
                report = result.get("report") or {}
                self.assertIn("conclusion", report)
                self.assertIsInstance(report.get("review_summary"), dict)
                self.assertIn("overall_verdict", report["review_summary"])
            finally:
                orchestrator.close()

    def test_orchestrator_runs_generic_stock_screen_without_clarification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                orchestrator.retriever.qdrant_available = False
                orchestrator.retriever.qdrant_error = "disabled_for_test"
                orchestrator.retriever.registry._subagents["blackboard_evidence_indexer"].qdrant = None
                orchestrator.retriever.registry._subagents["qdrant_evidence_searcher"].qdrant = None
                stub_runtime = _SkillRuntimeStub()
                orchestrator.collector.skill_runtime = stub_runtime
                with patch.object(orchestrator.collector.registry._subagents["stock_snapshot_collector"], "skill_runtime", stub_runtime), \
                    patch.object(orchestrator.collector.registry._subagents["sector_snapshot_collector"], "skill_runtime", stub_runtime), \
                    patch.object(orchestrator.collector.registry._subagents["candidate_universe_builder"], "skill_runtime", stub_runtime), \
                    patch("research_system.subagents.collector.call_with_retry", return_value=self._spot_table()):
                    response = orchestrator.run(RequestEnvelope(user_query="最近有什么股票值得投入"))
                self.assertEqual(response.intent, "stock_screen")
                self.assertEqual(response.status, "completed")
                report = (response.result or {}).get("report") or {}
                scorecard = report.get("screening_scorecard") or {}
                self.assertEqual(scorecard.get("investment_cycle"), "mid_term")
                self.assertEqual(scorecard.get("investment_cycle_assumption"), "default_mid_term")
            finally:
                orchestrator.close()

    def _spot_table(self):
        import pandas as pd

        return pd.DataFrame(
            [
                {"代码": "sz300363", "名称": "博腾股份", "最新价": 12.3, "涨跌幅": 2.1, "成交额": 1800000000},
                {"代码": "sz300347", "名称": "泰格医药", "最新价": 45.6, "涨跌幅": 0.8, "成交额": 1100000000},
                {"代码": "sh600000", "名称": "浦发银行", "最新价": 10.2, "涨跌幅": -0.2, "成交额": 900000000},
            ]
        )


if __name__ == "__main__":
    unittest.main()
