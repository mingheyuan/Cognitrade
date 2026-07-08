from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from research_system.agents.collector.service import CollectorService
from research_system.runtime.history.session.session_history import SessionHistory
from research_system.runtime.storage.jsonl import read_json, read_jsonl
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.schemas.protocol import RequestEnvelope
from research_system.services.routing.router.task_router import TaskRouter


BANK_COMPARE_QUERY = (
    "\u4ee5 000001 \u5e73\u5b89\u94f6\u884c\u4e3a\u8d77\u70b9\uff0c\u6269\u5230\u94f6\u884c\u80a1\uff1a"
    "\u62db\u5546\u94f6\u884c\u3001\u5b81\u6ce2\u94f6\u884c\u3001\u5174\u4e1a\u94f6\u884c\u3001"
    "\u6c5f\u82cf\u94f6\u884c\u3001\u6210\u90fd\u94f6\u884c\u7b49\uff0c\u6700\u8fd1\u54ea\u51e0\u53ea"
    "\u80a1\u7968\u66f4\u503c\u5f97\u6295\u8d44"
)


class CompareSkillRuntimeStub:
    _price_map = {
        "000001": 10.16,
        "600036": 46.20,
        "002142": 28.70,
        "601166": 21.40,
        "600919": 12.90,
        "601838": 19.60,
    }
    _pct_map = {
        "000001": 1.1,
        "600036": 0.8,
        "002142": 1.6,
        "601166": 0.5,
        "600919": 1.3,
        "601838": 1.4,
    }
    _amount_map = {
        "000001": 604_000_000.0,
        "600036": 2_100_000_000.0,
        "002142": 980_000_000.0,
        "601166": 1_500_000_000.0,
        "600919": 730_000_000.0,
        "601838": 680_000_000.0,
    }
    _pb_map = {
        "000001": 0.42,
        "600036": 0.78,
        "002142": 0.73,
        "601166": 0.61,
        "600919": 0.69,
        "601838": 0.88,
    }
    _pe_map = {
        "000001": 4.58,
        "600036": 7.21,
        "002142": 6.13,
        "601166": 5.37,
        "600919": 6.54,
        "601838": 7.85,
    }
    _market_cap_map = {
        "000001": 197_164_128_891.68,
        "600036": 1_165_000_000_000.0,
        "002142": 324_000_000_000.0,
        "601166": 478_000_000_000.0,
        "600919": 238_000_000_000.0,
        "601838": 121_000_000_000.0,
    }
    _total_shares_map = {
        "000001": 19_405_918_198.0,
        "600036": 25_220_984_560.0,
        "002142": 11_660_485_679.0,
        "601166": 20_774_190_751.0,
        "600919": 18_351_324_463.0,
        "601838": 6_171_748_039.0,
    }
    _bank_metric_map = {
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
            "capital_adequacy_ratio": 0.1280,
            "core_tier1_capital_adequacy_ratio": 0.0900,
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
            "asset_liability_ratio": 0.9090,
            "dividend_payout_ratio": 0.310,
            "pay_dividends_profits_interest_cash": 10_100_000_000.0,
            "revenue_yoy": 0.055,
            "net_profit_yoy": 0.063,
            "interest_margin_proxy": 0.396,
            "net_interest_spread_proxy": 0.030,
            "deposit_growth_proxy": 0.031,
            "loan_to_deposit_proxy": 0.201,
            "provision_coverage_proxy": 2.91,
            "is_bank_like": True,
        },
        "601838": {
            "peer_pb_mrq": 0.88,
            "peer_pb_rank": 7.0,
            "roe": 0.1190,
            "peer_roe_3y_avg": 0.1260,
            "peer_roe_rank": 6.0,
            "peer_revenue_growth_ttm": 0.0490,
            "peer_revenue_growth_ttm_vs_industry_avg": 0.0170,
            "peer_net_profit_growth_ttm": 0.0590,
            "peer_net_profit_growth_ttm_vs_industry_avg": 0.0270,
            "capital_adequacy_ratio": 0.1360,
            "core_tier1_capital_adequacy_ratio": 0.0980,
            "net_interest_spread": 0.0182,
            "nonperforming_loan_ratio": 0.0079,
            "provision_coverage_ratio": 3.2200,
            "asset_liability_ratio": 0.9010,
            "dividend_payout_ratio": 0.295,
            "pay_dividends_profits_interest_cash": 4_700_000_000.0,
            "revenue_yoy": 0.052,
            "net_profit_yoy": 0.066,
            "interest_margin_proxy": 0.404,
            "net_interest_spread_proxy": 0.031,
            "deposit_growth_proxy": 0.027,
            "loan_to_deposit_proxy": 0.196,
            "provision_coverage_proxy": 3.02,
            "is_bank_like": True,
        },
    }

    def run(self, skill_name: str, **kwargs):
        symbol = str(kwargs.get("symbol") or "").strip()
        if skill_name == "market_snapshot_skill":
            return {
                "snapshot_type": "market",
                "symbol": symbol,
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-30",
                "facts": {
                    "market_overview": {
                        "latest_price": self._price_map.get(symbol, 10.0),
                        "pct_change": self._pct_map.get(symbol, 0.0),
                        "amount": self._amount_map.get(symbol, 1_000_000_000.0),
                        "pb": self._pb_map.get(symbol),
                        "pe_ttm": self._pe_map.get(symbol),
                        "market_cap": self._market_cap_map.get(symbol),
                        "total_shares": self._total_shares_map.get(symbol),
                        "spot": {
                            "latest_price": self._price_map.get(symbol, 10.0),
                            "pct_change": self._pct_map.get(symbol, 0.0),
                            "amount": self._amount_map.get(symbol, 1_000_000_000.0),
                        },
                    }
                },
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        if skill_name == "fundamental_snapshot_skill":
            return {
                "snapshot_type": "fundamental",
                "symbol": symbol,
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-30",
                "facts": {
                    "financial_abstract": {
                        "revenue": 100.0,
                        "net_profit": 40.0,
                    },
                    "banking_metrics": dict(self._bank_metric_map.get(symbol, {})),
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


class ConcurrentCompareSkillRuntimeStub(CompareSkillRuntimeStub):
    def __init__(self, *, parties: int) -> None:
        self.barrier = threading.Barrier(parties)
        self.lock = threading.Lock()
        self.market_thread_names: list[str] = []

    def run(self, skill_name: str, **kwargs):
        if skill_name == "market_snapshot_skill":
            with self.lock:
                self.market_thread_names.append(threading.current_thread().name)
            self.barrier.wait(timeout=2.0)
        return super().run(skill_name, **kwargs)


class TaskRouterStockCompareTests(unittest.TestCase):
    def test_router_identifies_stock_compare_for_named_multi_stock_query(self) -> None:
        decision = TaskRouter().identify(RequestEnvelope(user_query=BANK_COMPARE_QUERY))

        self.assertEqual(decision.intent, "stock_compare")
        self.assertIsNone(decision.clarification)


class StockCompareFlowTests(unittest.TestCase):
    def test_collector_builds_compare_request_and_child_threads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            collector = CollectorService(CompareSkillRuntimeStub(), runs_root=Path(temp_dir))

            payload = collector.run(intent="stock_compare", query=BANK_COMPARE_QUERY)

            source = payload["sources"][0]
            self.assertEqual(source["source_type"], "compare_request")
            self.assertGreaterEqual(len(source["symbols"]), 2)
            self.assertEqual(len(source["compare_targets"]), len(source["symbols"]))
            self.assertEqual(len(payload["snapshots"]), len(source["symbols"]) * 3)
            subagent_plans = list(payload.get("subagent_plans") or [])
            self.assertEqual(len(subagent_plans), 1)
            plan_node_ids = [str(node.get("node_id") or "") for node in list(subagent_plans[0].get("nodes") or [])]
            self.assertEqual(plan_node_ids[:2], ["query_normalizer", "collection_strategy_resolver"])
            self.assertTrue(all(node_id.startswith("compare_stock_snapshot_collector:") for node_id in plan_node_ids[2:]))
            patch_op_types = [
                str(operation.get("op_type") or "")
                for patch in list(payload.get("subagent_plan_patches") or [])
                for operation in list((patch or {}).get("operations") or [])
            ]
            self.assertEqual(patch_op_types, ["request_replan"])
            trace_notes = [note for row in payload["subagent_trace"] for note in list(row.get("notes") or [])]
            child_thread_notes = [str(note) for note in trace_notes if str(note).startswith("child_thread=")]
            self.assertEqual(len(child_thread_notes), len(source["symbols"]))
            self.assertEqual(
                [row["subagent_type"] for row in payload["subagent_trace"][:2]],
                ["query_normalizer", "collection_strategy_resolver"],
            )
            self.assertEqual(
                [row["subagent_type"] for row in payload["subagent_trace"][2:]],
                ["stock_snapshot_collector"] * len(source["symbols"]),
            )
            self.assertTrue(source.get("child_thread_ids"))

            run_dirs = list(Path(temp_dir).iterdir())
            self.assertEqual(len(run_dirs), 1)
            run_root = run_dirs[0]
            child_thread_paths = sorted((run_root / "threads").glob("*.thread.json"))
            self.assertEqual(len(child_thread_paths), len(source["symbols"]))
            child_thread_ids = {path.stem.removesuffix(".thread") for path in child_thread_paths}
            self.assertEqual(child_thread_ids, set(source["child_thread_ids"]))
            self.assertTrue(all((read_json(path) or {}).get("status") == "completed" for path in child_thread_paths))
            child_turn_paths = sorted((run_root / "threads").glob("*.turn.*.json"))
            self.assertEqual(len(child_turn_paths), len(source["symbols"]))
            self.assertTrue(all((read_json(path) or {}).get("status") == "completed" for path in child_turn_paths))

            history = SessionHistory(run_root)
            thread_rows = history.thread_states()
            self.assertEqual(len(thread_rows), len(source["symbols"]) + 1)
            root_rows = [row for row in thread_rows if row.get("parent_thread_id") in {None, ""}]
            self.assertEqual(len(root_rows), 1)
            self.assertEqual(root_rows[0]["status"], "completed")
            self.assertEqual(len(history.thread_tree()), 1)
            self.assertEqual(len(history.thread_tree()[0]["children"]), len(source["symbols"]))
            trace_payload = history.trace()
            dag_events = [str(row.get("event") or "") for row in list(trace_payload.get("dynamic_trace") or [])]
            self.assertIn("subagent_dag_initialized", dag_events)
            self.assertIn("subagent_dag_node_completed", dag_events)
            metrics_payload = history.metrics()
            self.assertGreaterEqual(
                metrics_payload["dag_event_counts"].get("subagent_dag_node_completed", 0),
                len(source["symbols"]),
            )

            items = read_jsonl(run_root / "items.jsonl")
            child_thread_items = [row for row in items if row.get("item_type") == "thread"]
            self.assertEqual(len({row.get("item_id") for row in child_thread_items}), len(source["symbols"]))
            self.assertEqual(
                len({row.get("item_id") for row in child_thread_items if row.get("status") == "completed"}),
                len(source["symbols"]),
            )
            child_subagent_items = [
                row
                for row in items
                if row.get("item_type") == "subagent"
                and row.get("thread_id") in child_thread_ids
                and row.get("title") == "stock_snapshot_collector"
            ]
            self.assertEqual(len({row.get("item_id") for row in child_subagent_items}), len(source["symbols"]))
            self.assertTrue(all(str(row.get("turn_id") or "").strip() for row in child_subagent_items))

    def test_collector_runs_compare_target_snapshot_batches_concurrently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = ConcurrentCompareSkillRuntimeStub(parties=6)
            collector = CollectorService(runtime, runs_root=Path(temp_dir))

            payload = collector.run(intent="stock_compare", query=BANK_COMPARE_QUERY)

            symbols = payload["sources"][0]["symbols"]
            self.assertEqual(len(runtime.market_thread_names), len(symbols))

    def test_orchestrator_runs_stock_compare_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                orchestrator.retriever.disable_qdrant_for_test()
                orchestrator.collector.skill_runtime = CompareSkillRuntimeStub()

                response = orchestrator.run(RequestEnvelope(user_query=BANK_COMPARE_QUERY))

                self.assertEqual(response.intent, "stock_compare")
                self.assertEqual(response.status, "completed")
                report = (response.result or {}).get("report") or {}
                conclusion = str(report.get("conclusion") or "")
                self.assertIn("\u7b2c\u4e00\u68af\u961f", conclusion)
                self.assertIn("\u6c5f\u82cf\u94f6\u884c", conclusion)
                self.assertIsNone(report.get("degraded_note"))
                self.assertIsInstance(report.get("methodologies"), list)
                self.assertTrue(
                    any(
                        str(item.get("source_type") or "") == "bank_compare_scorecard"
                        for item in list(report.get("methodologies") or [])
                    )
                )
            finally:
                orchestrator.close()

    def test_orchestrator_can_rerun_from_retrieve_using_cached_collect_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                orchestrator.retriever.disable_qdrant_for_test()
                runtime = CompareSkillRuntimeStub()
                runtime.calls = []
                original_run = runtime.run

                def _tracking_run(skill_name: str, **kwargs):
                    runtime.calls.append((skill_name, kwargs))
                    return original_run(skill_name, **kwargs)

                runtime.run = _tracking_run
                orchestrator.collector.skill_runtime = runtime

                first_response = orchestrator.run(RequestEnvelope(user_query=BANK_COMPARE_QUERY))
                first_run_id = str(first_response.run_id or "")
                first_call_count = len(runtime.calls)

                rerun_response = orchestrator.rerun_from(first_run_id, from_node="retrieve")

                self.assertEqual(rerun_response.intent, "stock_compare")
                self.assertEqual(rerun_response.status, "completed")
                self.assertEqual(len(runtime.calls), first_call_count)

                rerun_run_id = str(rerun_response.run_id or "")
                history = SessionHistory(settings.runs_root / rerun_run_id)
                raw_events = history.replay()["events"]
                trace_rows = [
                    row for row in list(history.trace().get("subagent_trace") or [])
                    if str(row.get("invocation_id") or "").strip()
                ]
                self.assertTrue(
                    any(str(row.get("event_type") or "") == "dag_partial_rerun_requested" for row in raw_events)
                )
                cache_hit_row = next(
                    (
                        row for row in raw_events
                        if str(row.get("event_type") or "") == "dag_cache_hit"
                    ),
                    None,
                )
                self.assertIsNotNone(cache_hit_row)
                self.assertIn("collect", list(((cache_hit_row or {}).get("payload") or {}).get("cached_nodes") or []))
                self.assertGreaterEqual(history.metrics()["event_type_counts"].get("dag_cache_hit", 0), 1)
                self.assertGreaterEqual(history.metrics()["dag_event_counts"].get("subagent_dag_cache_hit", 0), 1)
                self.assertTrue(trace_rows)
                subagent_payload = history.subagent(str(trace_rows[0]["invocation_id"]))
                self.assertEqual(subagent_payload["invocation_id"], trace_rows[0]["invocation_id"])
                self.assertTrue(str(subagent_payload.get("thread_id") or "").strip())
                self.assertTrue(str(subagent_payload.get("turn_id") or "").strip())
                self.assertTrue(str(subagent_payload.get("phase") or "").strip())
                self.assertTrue(str(subagent_payload.get("agent") or "").strip())
                self.assertIsNotNone(subagent_payload.get("duration_ms"))
            finally:
                orchestrator.close()

    def test_orchestrator_can_rerun_retrieve_subagent_dag_branch_using_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                orchestrator.retriever.disable_qdrant_for_test()
                runtime = CompareSkillRuntimeStub()
                runtime.calls = []
                original_run = runtime.run

                def _tracking_run(skill_name: str, **kwargs):
                    runtime.calls.append((skill_name, kwargs))
                    return original_run(skill_name, **kwargs)

                runtime.run = _tracking_run
                orchestrator.collector.skill_runtime = runtime

                first_response = orchestrator.run(RequestEnvelope(user_query=BANK_COMPARE_QUERY))
                first_run_id = str(first_response.run_id or "")
                first_call_count = len(runtime.calls)

                rerun_response = orchestrator.rerun_subagent_from(
                    first_run_id,
                    action_node="retrieve",
                    dag_id="phase_two",
                    from_node="retrieval_decider",
                )

                self.assertEqual(rerun_response.intent, "stock_compare")
                self.assertEqual(rerun_response.status, "completed")
                self.assertEqual(len(runtime.calls), first_call_count)

                rerun_run_id = str(rerun_response.run_id or "")
                history = SessionHistory(settings.runs_root / rerun_run_id)
                raw_events = list(history.replay()["events"])
                self.assertTrue(
                    any(
                        str(row.get("event_type") or "") == "dag_partial_rerun_requested"
                        and str(((row.get("payload") or {}).get("rerun_from_node") or "")) == "retrieve"
                        and str(((row.get("payload") or {}).get("rerun_subagent_dag_id") or "")) == "phase_two"
                        and str(((row.get("payload") or {}).get("rerun_subagent_from_node") or "")) == "retrieval_decider"
                        for row in raw_events
                    )
                )
                self.assertTrue(
                    any(
                        str(row.get("event_type") or "") == "subagent_dag_partial_rerun_requested"
                        and str(((row.get("payload") or {}).get("dag_id") or "")) == "retrieve:phase_two"
                        and str(((row.get("payload") or {}).get("start_from_node_id") or "")) == "retrieval_decider"
                        for row in raw_events
                    )
                )
                self.assertGreaterEqual(
                    history.metrics()["dag_event_counts"].get("subagent_dag_cache_hit", 0),
                    1,
                )
            finally:
                orchestrator.close()


if __name__ == "__main__":
    unittest.main()
