from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_system.agents.collector.service import CollectorService
from research_system.agents.screen_reviewer.service import ScreenReviewerService
from research_system.agents.screener.service import ScreenerService
from research_system.runtime.storage.jsonl import read_json, read_jsonl
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.schemas.protocol import RequestEnvelope
from research_system.services.routing.router.task_router import TaskRouter


class SkillRuntimeStub:
    def run(self, skill_name: str, **kwargs):
        if skill_name == "sector_snapshot_skill":
            return {
                "snapshot_type": "sector",
                "sector": "AI",
                "theme": "concept",
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-28",
                "facts": {
                    "top_constituents": [
                        {"code": "300363", "name": "Alpha"},
                        {"code": "300347", "name": "Beta"},
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
                            "latest_price": 12.3 if symbol == "300363" else 45.6,
                            "pct_change": 2.1 if symbol == "300363" else 0.8,
                            "amount": 1800000000 if symbol == "300363" else 1100000000,
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
                "facts": {"financial_abstract": {"revenue": 100}},
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


def spot_table():
    class Table:
        def to_dict(self, orient):
            return [
                {"代码": "300363", "名称": "Alpha", "最新价": 12.3, "涨跌幅": 2.1, "成交额": 1800000000},
                {"代码": "300347", "名称": "Beta", "最新价": 45.6, "涨跌幅": 0.8, "成交额": 1100000000},
                {"代码": "000001", "名称": "ST Skip", "最新价": 1.0, "涨跌幅": 0.1, "成交额": 10},
            ]

    return Table()


class TaskRouterStockScreenTests(unittest.TestCase):
    def test_router_identifies_stock_screen_for_screening_query(self) -> None:
        decision = TaskRouter().identify(RequestEnvelope(user_query="screen growth stocks"))
        self.assertEqual(decision.intent, "stock_screen")
        self.assertIsNone(decision.clarification)

    def test_router_identifies_stock_screen_with_cycle_hint(self) -> None:
        decision = TaskRouter().identify(RequestEnvelope(user_query="screen growth stocks for mid term"))
        self.assertEqual(decision.intent, "stock_screen")
        self.assertEqual((decision.trace or {}).get("source"), "route_table")


class StockScreenFlowTests(unittest.TestCase):
    def test_collector_skill_runtime_setter_updates_subagents(self) -> None:
        collector = CollectorService(SkillRuntimeStub())
        replacement_runtime = SkillRuntimeStub()

        collector.skill_runtime = replacement_runtime

        self.assertIs(collector.skill_runtime, replacement_runtime)
        self.assertIs(collector.registry._subagents["stock_snapshot_collector"].skill_runtime, replacement_runtime)
        self.assertIs(collector.registry._subagents["sector_snapshot_collector"].skill_runtime, replacement_runtime)
        self.assertIs(collector.registry._subagents["candidate_universe_builder"].skill_runtime, replacement_runtime)

    def test_collector_builds_stock_screen_request_and_default_cycle_assumption(self) -> None:
        collector = CollectorService(SkillRuntimeStub())

        with patch("research_system.subagents.collector.call_with_retry", return_value=spot_table()):
            payload = collector.run(intent="stock_screen", query="screen growth stocks")

        source = payload["sources"][0]
        self.assertEqual(source["source_type"], "screen_request")
        self.assertEqual(source["investment_cycle"], "mid_term")
        self.assertEqual(source["investment_cycle_assumption"], "default_mid_term")
        self.assertIn("candidate_universe", source)
        self.assertEqual(payload["snapshots"], [])

    def test_screener_ranks_candidates_and_attaches_comps_methodology(self) -> None:
        screener = ScreenerService()

        payload = screener.run(
            query="screen growth stocks",
            source_payloads=[
                {
                    "candidate_universe": [
                        {"symbol": "300363", "name": "Alpha", "sector": "AI", "theme": "concept"},
                        {"symbol": "300347", "name": "Beta", "sector": "AI", "theme": "concept"},
                    ],
                    "sector_context": {"sector": "AI", "theme": "concept"},
                    "investment_cycle": "mid_term",
                }
            ],
            snapshot_payloads=[
                {
                    "snapshot_type": "market",
                    "symbol": "300363",
                    "facts": {"market_overview": {"spot": {"latest_price": 12.3, "pct_change": 2.1, "amount": 1800000000}}},
                },
                {
                    "snapshot_type": "fundamental",
                    "symbol": "300363",
                    "facts": {"financial_abstract": {"revenue": 100}},
                },
                {
                    "snapshot_type": "notice",
                    "symbol": "300363",
                    "facts": {"notice_count": 3},
                },
                {
                    "snapshot_type": "market",
                    "symbol": "300347",
                    "facts": {"market_overview": {"spot": {"latest_price": 45.6, "pct_change": 0.8, "amount": 1100000000}}},
                },
                {
                    "snapshot_type": "fundamental",
                    "symbol": "300347",
                    "facts": {"financial_abstract": {"revenue": 80}},
                },
                {
                    "snapshot_type": "notice",
                    "symbol": "300347",
                    "facts": {"notice_count": 1},
                },
            ],
            constraints={"top_k": 2},
        )

        self.assertEqual([item["symbol"] for item in payload["screened_candidates"]], ["300363", "300347"])
        self.assertEqual(payload["scorecard"]["deep_checked_count"], 2)
        self.assertEqual(payload["scorecard"]["preferred_methodology"], "comps")
        self.assertIn("comps", payload["screened_candidates"][0]["methodology"])
        self.assertEqual(payload["enrichment_targets"], [])

    def test_screener_uses_planning_hints_for_cycle_and_methodology_source(self) -> None:
        screener = ScreenerService()

        payload = screener.run(
            query="continue the previous watchlist discussion",
            source_payloads=[
                {
                    "candidate_universe": [
                        {"symbol": "300363", "name": "Alpha"},
                        {"symbol": "300347", "name": "Beta"},
                    ],
                    "planning_context_hints": {
                        "recommended_next_step": "compare same-basis cashflow and short-term sentiment",
                        "thread_topic": "screen short-term candidates with cashflow resilience",
                    },
                }
            ],
            snapshot_payloads=[
                {
                    "snapshot_type": "market",
                    "symbol": "300363",
                    "facts": {"market_overview": {"spot": {"latest_price": 12.3, "pct_change": 2.1, "amount": 1800000000}}},
                },
                {
                    "snapshot_type": "market",
                    "symbol": "300347",
                    "facts": {"market_overview": {"spot": {"latest_price": 45.6, "pct_change": 0.8, "amount": 1100000000}}},
                },
            ],
        )

        self.assertEqual(payload["scorecard"]["investment_cycle"], "short_term")
        self.assertEqual(payload["scorecard"]["investment_cycle_assumption"], "planning_context")
        self.assertEqual(payload["scorecard"]["preferred_methodology"], "comps")
        self.assertEqual(payload["scorecard"]["screening_strategy"]["methodology_source"], "planning_context")

    def test_screen_reviewer_uses_methodology_memory_focus_hint(self) -> None:
        payload = ScreenReviewerService().run(
            source_run_id="01SCREEN",
            candidates=[
                {
                    "symbol": "300363",
                    "name": "Alpha",
                    "candidate_tier": "core",
                    "score": 2.1,
                    "deep_checked": False,
                    "missing_evidence": ["fundamental_snapshot"],
                    "main_risk": "need fresher fundamental evidence",
                }
            ],
            scorecard={"summary": "continue the previous watchlist discussion"},
            planning_context_hints={"methodology_benchmark_scope": "defensive_compounder"},
        )

        self.assertIn("screen_review_focus=cashflow_resilience", payload["summary"]["notes"])
        self.assertIn("screen_review_focus_source=methodology_memory", payload["summary"]["notes"])
        self.assertEqual(payload["findings"][0]["verdict"], "inconclusive")
        subagent_plans = list(payload.get("subagent_plans") or [])
        self.assertEqual(len(subagent_plans), 1)
        review_node_types = {
            str(node.get("node_id") or ""): str(node.get("subagent_type") or "")
            for node in list(subagent_plans[0].get("nodes") or [])
        }
        self.assertEqual(review_node_types.get("screen_review_strategy_resolver"), "screen_review_strategy_resolver")
        self.assertEqual(review_node_types.get("screen_review_candidate_1"), "screen_review_candidate")
        self.assertEqual(review_node_types.get("screen_review_summary_builder"), "screen_review_summary_builder")
        patch_op_types = [
            str(operation.get("op_type") or "")
            for patch in list(payload.get("subagent_plan_patches") or [])
            for operation in list((patch or {}).get("operations") or [])
        ]
        self.assertIn("insert_subagent", patch_op_types)

    def test_screener_local_run_scope_records_agent_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            screener = ScreenerService()
            screener.runs_root = Path(temp_dir)

            payload = screener.run(
                query="screen growth stocks",
                source_payloads=[
                    {
                        "candidate_universe": [
                            {"symbol": "300363", "name": "Alpha", "sector": "AI", "theme": "concept"},
                        ],
                        "sector_context": {"sector": "AI", "theme": "concept"},
                        "investment_cycle": "mid_term",
                    }
                ],
                snapshot_payloads=[
                    {
                        "snapshot_type": "market",
                        "symbol": "300363",
                        "facts": {"market_overview": {"spot": {"latest_price": 12.3, "pct_change": 2.1, "amount": 1800000000}}},
                    },
                    {
                        "snapshot_type": "fundamental",
                        "symbol": "300363",
                        "facts": {"financial_abstract": {"revenue": 100}},
                    },
                    {
                        "snapshot_type": "notice",
                        "symbol": "300363",
                        "facts": {"notice_count": 3},
                    },
                ],
            )

            run_dirs = list(Path(temp_dir).iterdir())
            self.assertEqual(len(run_dirs), 1)
            items = read_jsonl(run_dirs[0] / "items.jsonl")
            agent_items = [row for row in items if row.get("item_type") == "agent"]
            self.assertTrue(agent_items)
            final_agent_item = agent_items[-1]
            self.assertEqual(final_agent_item["title"], "screener")
            self.assertEqual(final_agent_item["status"], "completed")
            self.assertTrue(payload["screened_candidates"])

    def test_screen_reviewer_local_run_scope_records_agent_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reviewer = ScreenReviewerService()
            reviewer.runs_root = Path(temp_dir)

            payload = reviewer.run(
                source_run_id="01SCREEN",
                candidates=[
                    {
                        "symbol": "300363",
                        "name": "Alpha",
                        "candidate_tier": "core",
                        "score": 2.1,
                        "deep_checked": False,
                        "missing_evidence": ["fundamental_snapshot"],
                        "main_risk": "need fresher fundamental evidence",
                    }
                ],
                scorecard={"summary": "continue the previous watchlist discussion"},
                planning_context_hints={"methodology_benchmark_scope": "defensive_compounder"},
            )

            run_dirs = list(Path(temp_dir).iterdir())
            self.assertEqual(len(run_dirs), 1)
            items = read_jsonl(run_dirs[0] / "items.jsonl")
            agent_items = [row for row in items if row.get("item_type") == "agent"]
            self.assertTrue(agent_items)
            final_agent_item = agent_items[-1]
            self.assertEqual(final_agent_item["title"], "screen_reviewer")
            self.assertEqual(final_agent_item["status"], "completed")
            self.assertTrue(payload["findings"])
            self.assertTrue(payload.get("subagent_trace"))
            self.assertTrue(payload.get("subagent_plans"))

    def test_orchestrator_runs_stock_screen_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                orchestrator.retriever.disable_qdrant_for_test()
                orchestrator.collector.skill_runtime = SkillRuntimeStub()
                with patch("research_system.subagents.collector.call_with_retry", return_value=spot_table()):
                    response = orchestrator.run(RequestEnvelope(user_query="screen growth stocks"))
                self.assertEqual(response.intent, "stock_screen")
                self.assertEqual(response.status, "completed")
                report = (response.result or {}).get("report") or {}
                self.assertIn("conclusion", report)
                if report.get("screening_scorecard") is not None:
                    self.assertIsInstance(report.get("screening_scorecard"), dict)
                if report.get("candidates") is not None:
                    self.assertIsInstance(report.get("candidates"), list)
                self.assertEqual(report.get("degraded_note"), "llm_unavailable")
                debug = report.get("debug") or {}
                self.assertEqual(debug.get("intent"), "stock_screen")
                self.assertTrue(debug.get("subagent_plans"))
                self.assertTrue(debug.get("subagent_plan_patches"))
                self.assertTrue(debug.get("action_plan_patches"))
                self.assertTrue(
                    {"collect", "retrieve", "review", "report"}.issubset(
                        {str(item.get("phase") or "") for item in list(debug.get("subagent_plans") or [])}
                    )
                )
            finally:
                orchestrator.close()

    def test_dynamic_screen_review_is_persisted_into_dag_and_can_rerun_from_node(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                orchestrator.retriever.disable_qdrant_for_test()
                runtime = SkillRuntimeStub()
                runtime.calls = []
                original_run = runtime.run

                def _tracking_run(skill_name: str, **kwargs):
                    runtime.calls.append((skill_name, kwargs))
                    return original_run(skill_name, **kwargs)

                runtime.run = _tracking_run
                orchestrator.collector.skill_runtime = runtime

                with patch("research_system.subagents.collector.call_with_retry", return_value=spot_table()):
                    response = orchestrator.run(RequestEnvelope(user_query="screen growth stocks"))

                first_run_id = str(response.run_id or "")
                first_call_count = len(runtime.calls)
                task_plan_payload = read_json(settings.runs_root / first_run_id / "task_plan.json") or {}
                task_plan_nodes = [str(item.get("node_id") or "") for item in list(task_plan_payload.get("actions") or [])]
                self.assertIn("screen_enrich", task_plan_nodes)
                self.assertIn("screen_review", task_plan_nodes)

                steps_payload = orchestrator.steps(first_run_id)
                step_by_node = {
                    str(item.get("node_id") or ""): item
                    for item in list(steps_payload.get("steps") or [])
                }
                self.assertEqual(step_by_node["screen_enrich"]["status"], "completed")
                self.assertEqual(step_by_node["screen_review"]["status"], "completed")

                with patch("research_system.subagents.collector.call_with_retry", return_value=spot_table()):
                    rerun_response = orchestrator.rerun_from(first_run_id, from_node="screen_review")

                self.assertEqual(rerun_response.status, "completed")
                self.assertEqual(len(runtime.calls), first_call_count)
                rerun_run_id = str(rerun_response.run_id or "")
                replay_payload = orchestrator.replay(rerun_run_id)
                self.assertTrue(
                    any(str(row.get("event_type") or "") == "dag_partial_rerun_requested" for row in replay_payload["events"])
                )
                cache_hit_row = next(
                    (
                        row for row in replay_payload["events"]
                        if str(row.get("event_type") or "") == "dag_cache_hit"
                    ),
                    None,
                )
                self.assertIsNotNone(cache_hit_row)
                self.assertIn("screen", list(((cache_hit_row or {}).get("payload") or {}).get("cached_nodes") or []))
            finally:
                orchestrator.close()

    def test_dynamic_screen_enrich_is_persisted_and_can_rerun_from_node(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                orchestrator.retriever.disable_qdrant_for_test()
                runtime = SkillRuntimeStub()
                runtime.calls = []
                original_run = runtime.run

                def _tracking_run(skill_name: str, **kwargs):
                    runtime.calls.append((skill_name, kwargs))
                    return original_run(skill_name, **kwargs)

                runtime.run = _tracking_run
                orchestrator.collector.skill_runtime = runtime

                with patch("research_system.subagents.collector.call_with_retry", return_value=spot_table()):
                    response = orchestrator.run(RequestEnvelope(user_query="screen growth stocks"))

                first_run_id = str(response.run_id or "")
                first_call_count = len(runtime.calls)
                task_plan_payload = read_json(settings.runs_root / first_run_id / "task_plan.json") or {}
                task_plan_nodes = [str(item.get("node_id") or "") for item in list(task_plan_payload.get("actions") or [])]
                self.assertIn("screen_enrich", task_plan_nodes)

                rerun_response = orchestrator.rerun_from(first_run_id, from_node="screen_enrich")

                self.assertEqual(rerun_response.status, "completed")
                self.assertEqual(len(runtime.calls), first_call_count)
                rerun_run_id = str(rerun_response.run_id or "")
                replay_payload = orchestrator.replay(rerun_run_id)
                self.assertTrue(
                    any(
                        str(row.get("event_type") or "") == "dag_partial_rerun_requested"
                        and str(((row.get("payload") or {}).get("rerun_from_node") or "")) == "screen_enrich"
                        for row in replay_payload["events"]
                    )
                )
                cache_hit_row = next(
                    (
                        row for row in replay_payload["events"]
                        if str(row.get("event_type") or "") == "dag_cache_hit"
                    ),
                    None,
                )
                self.assertIsNotNone(cache_hit_row)
                cached_nodes = list(((cache_hit_row or {}).get("payload") or {}).get("cached_nodes") or [])
                self.assertIn("collect", cached_nodes)
                self.assertIn("screen", cached_nodes)
                self.assertTrue(
                    any(str(row.get("event_type") or "") == "subagent_dag_cache_hit" for row in replay_payload["events"])
                )
                step_by_node = {
                    str(item.get("node_id") or ""): item
                    for item in list(orchestrator.steps(rerun_run_id).get("steps") or [])
                }
                self.assertEqual(step_by_node["screen_enrich"]["status"], "completed")
            finally:
                orchestrator.close()


if __name__ == "__main__":
    unittest.main()
