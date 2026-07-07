from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.adapters.storage.local_memory_adapter import LocalMemoryAdapter
from research_system.agents.collector.service import CollectorService
from research_system.agents.reporter.service import ReporterService
from research_system.agents.retriever.service import RetrieverService
from research_system.agents.reviewer.service import ReviewerService
from research_system.runtime.storage.jsonl import append_jsonl, write_json
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.runtime.providers.provider_registry import LLMProviderError
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import BlackboardEntry
from research_system.services.memory import MemoryService


class ReviewSkillRuntimeStub:
    def run(self, skill_name: str, **kwargs) -> dict:
        if skill_name == "sector_snapshot_skill":
            return {
                "snapshot_type": "sector",
                "query": kwargs.get("query"),
                "sector": "innovation_drug",
                "theme": "concept",
                "snapshot_date": "2026-06-28",
                "facts": {
                    "latest_hist": {"pct_change": -3.85},
                    "benchmark": {
                        "benchmark_code": "000300",
                        "benchmark_name": "HS300",
                        "latest_hist": {"pct_change": -3.03},
                    },
                    "relative_strength": {"latest_excess_pct_change": -0.82},
                    "signal_scorecard": {"setup_label": "weak_tape_but_catalysts_alive"},
                    "policy_signals": [{"title": "innovation drug policy support", "policy_priority": 5}],
                    "regulatory_signals": [{"title": "approval review acceleration", "signal_priority": 5}],
                    "theme_news": [{"title": "innovation drug catalyst watch"}],
                    "catalyst_digest": {
                        "top_policy_titles": ["innovation drug policy support"],
                        "top_regulatory_titles": ["approval review acceleration"],
                    },
                },
                "source": {"skill": "sector_snapshot_skill"},
            }
        if skill_name == "market_snapshot_skill":
            return {
                "snapshot_type": "market",
                "symbol": kwargs.get("symbol"),
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-28",
                "facts": {
                    "latest_bar": {"pct_change": -1.25},
                },
                "source": {"skill": "market_snapshot_skill"},
            }
        if skill_name == "fundamental_snapshot_skill":
            return {
                "snapshot_type": "fundamental",
                "symbol": kwargs.get("symbol"),
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-28",
                "facts": {
                    "latest_report_date": "2026-03-31",
                },
                "source": {"skill": "fundamental_snapshot_skill"},
            }
        if skill_name == "notice_snapshot_skill":
            return {
                "snapshot_type": "notice",
                "symbol": kwargs.get("symbol"),
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-28",
                "facts": {
                    "recent_notices": [
                        {"title": "innovation drug clinical milestone notice"},
                        {"title": "innovation drug regulatory filing update"},
                    ],
                },
                "source": {"skill": "notice_snapshot_skill"},
            }
        raise AssertionError(f"unexpected skill: {skill_name}")


class SectorDynamicSkillRuntimeStub:
    def __init__(self) -> None:
        self.sector_calls = 0

    def run(self, skill_name: str, **kwargs) -> dict:
        if skill_name != "sector_snapshot_skill":
            raise AssertionError(f"unexpected skill: {skill_name}")
        self.sector_calls += 1
        if self.sector_calls == 1:
            return {
                "snapshot_type": "sector",
                "query": kwargs.get("query"),
                "sector": "innovation_drug",
                "theme": "concept",
                "snapshot_date": "2026-06-29",
                "facts": {
                    "latest_hist": {"pct_change": -1.2},
                    "signal_scorecard": {"setup_label": "incomplete_sector_evidence"},
                },
                "source": {"skill": "sector_snapshot_skill"},
                "degraded": False,
            }
        return {
            "snapshot_type": "sector",
            "query": kwargs.get("query"),
            "sector": "innovation_drug",
            "theme": "concept",
            "snapshot_date": "2026-06-29",
            "facts": {
                "latest_hist": {"pct_change": -1.2},
                "benchmark": {
                    "benchmark_code": "000300",
                    "benchmark_name": "HS300",
                    "latest_hist": {"pct_change": -0.8},
                },
                "relative_strength": {"latest_excess_pct_change": -0.4},
                "policy_signals": [{"title": "innovation drug policy window", "policy_priority": 4}],
                "regulatory_signals": [{"title": "approval milestone update", "signal_priority": 4}],
                "signal_scorecard": {
                    "setup_label": "sector_evidence_replenished",
                    "benchmark_view": "underperform",
                },
            },
            "source": {"skill": "sector_snapshot_skill"},
            "degraded": False,
        }


class ReporterUnavailableStub:
    available = False

    def parse(self, **kwargs) -> dict:
        raise LLMProviderError("simulated llm unavailability")


def _blackboard_entry(**kwargs) -> BlackboardEntry:
    data = dict(kwargs)
    data.setdefault("thread_id", str(data.get("run_id") or ""))
    return BlackboardEntry(**data)


def _write_sector_review_target(settings: Settings, run_id: str) -> None:
    run_root = settings.runs_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    write_json(
        run_root / "result.json",
        {
            "conclusion": "sector may underperform benchmark but catalysts remain active",
            "risk": "if catalyst fades, thesis weakens",
            "follow_up": "watch short term window",
        },
    )
    blackboard_path = run_root / "blackboard.jsonl"
    append_jsonl(
        blackboard_path,
        _blackboard_entry(
            run_id=run_id,
            request_id="req-target",
            kind="report_fragment",
            agent="reporter",
            phase="report",
            payload={
                "conclusion": "sector may underperform benchmark but catalysts remain active",
                "risk": "if catalyst fades, thesis weakens",
                "follow_up": "watch short term window",
            },
            source_refs=[{"type": "retrieval", "run_id": run_id}],
        ).model_dump(mode="json"),
    )
    append_jsonl(
        blackboard_path,
        _blackboard_entry(
            run_id=run_id,
            request_id="req-target",
            kind="source_record",
            agent="collector",
            phase="collect",
            payload={
                "title": "sector_research input query",
                "content": "innovation drug sector",
                "source_type": "user_query",
                "sector": "innovation_drug",
            },
            source_refs=[{"type": "request", "run_id": run_id}],
        ).model_dump(mode="json"),
    )


def _write_stock_review_target(settings: Settings, run_id: str) -> None:
    run_root = settings.runs_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    write_json(
        run_root / "result.json",
        {
            "conclusion": "000001 may remain range-bound without stronger earnings revisions",
            "risk": "if asset quality worsens, valuation support weakens",
        },
    )
    blackboard_path = run_root / "blackboard.jsonl"
    append_jsonl(
        blackboard_path,
        _blackboard_entry(
            run_id=run_id,
            request_id="req-stock",
            kind="report_fragment",
            agent="reporter",
            phase="report",
            payload={
                "conclusion": "000001 may remain range-bound without stronger earnings revisions",
                "risk": "if asset quality worsens, valuation support weakens",
            },
            source_refs=[{"type": "retrieval", "run_id": run_id}],
        ).model_dump(mode="json"),
    )
    append_jsonl(
        blackboard_path,
        _blackboard_entry(
            run_id=run_id,
            request_id="req-stock",
            kind="source_record",
            agent="collector",
            phase="collect",
            payload={
                "title": "stock_check input query",
                "content": "analyze 000001",
                "source_type": "user_query",
                "symbol": "000001",
            },
            source_refs=[{"type": "request", "run_id": run_id}],
        ).model_dump(mode="json"),
    )


class ReviewThesisFlowTests(unittest.TestCase):
    def test_orchestrator_sector_research_result_contains_dynamic_replenish_trace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)

            orchestrator = ResearchOrchestrator(settings)
            try:
                stub_runtime = SectorDynamicSkillRuntimeStub()
                orchestrator.collector.skill_runtime = stub_runtime
                orchestrator.retriever.disable_qdrant_for_test()

                response = orchestrator.run(
                    RequestEnvelope(
                        user_query="innovation drug sector",
                        intent="sector_research",
                    )
                )
            finally:
                orchestrator.close()

            self.assertEqual(response.intent, "sector_research")
            self.assertEqual(response.status, "completed")
            result = response.result or {}
            report = result.get("report") or {}
            debug = report.get("debug") or {}
            self.assertGreaterEqual(stub_runtime.sector_calls, 2)
            self.assertTrue(
                any(
                    item.get("event") == "dynamic_action_inserted"
                    and str(item.get("node_id") or "").startswith("collect_replenish_sector_")
                    for item in debug.get("dynamic_trace") or []
                )
            )
            self.assertTrue(
                any(
                    item.get("subagent_type") == "sector_snapshot_collector"
                    for item in debug.get("subagent_trace") or []
                )
            )

    def test_orchestrator_review_thesis_result_contains_debug_subagent_trace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)

            _write_sector_review_target(settings, "01REVIEWTARGET")

            orchestrator = ResearchOrchestrator(settings)
            try:
                orchestrator.collector.skill_runtime = ReviewSkillRuntimeStub()
                orchestrator.retriever.disable_qdrant_for_test()

                response = orchestrator.run(
                    RequestEnvelope(
                        user_query="review thesis for innovation drug",
                        intent="review_thesis",
                        constraints={"review_target_run_id": "01REVIEWTARGET"},
                    )
                )
            finally:
                orchestrator.close()

            self.assertEqual(response.intent, "review_thesis")
            self.assertEqual(response.status, "completed")
            result = response.result or {}
            report = result.get("report") or {}
            debug = report.get("debug") or {}
            self.assertIn("subagent_trace", debug)
            self.assertIn("dynamic_trace", debug)
            self.assertTrue(any(item.get("subagent_type") == "review_target_loader" for item in debug["subagent_trace"]))
            self.assertTrue(any(item.get("subagent_type") == "review_summary_builder" for item in debug["subagent_trace"]))
            self.assertTrue(any(item.get("agent_path") == "reporter/strategy/report_strategy_resolver" for item in debug["subagent_trace"]))
            self.assertTrue(any(item.get("agent_path") == "reporter/fallback/fallback_report_composer" for item in debug["subagent_trace"]))
            self.assertTrue(any(item.get("event") == "dynamic_action_inserted" and item.get("node_id") == "review" for item in debug["dynamic_trace"]))

    def test_review_thesis_flow_uses_prior_run_and_sector_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)

            _write_sector_review_target(settings, "01REVIEWTARGET")

            collector = CollectorService(ReviewSkillRuntimeStub(), settings.runs_root)
            reviewer = ReviewerService({"benchmark_resolution": "sector", "default_broad_index": "HS300"})
            retriever = object.__new__(RetrieverService)
            reporter = ReporterService(ReporterUnavailableStub())

            collect_payload = collector.run(
                intent="review_thesis",
                query="review thesis for innovation drug",
                constraints={"review_target_run_id": "01REVIEWTARGET"},
            )

            self.assertEqual(collect_payload["sources"][0]["review_target_run_id"], "01REVIEWTARGET")
            self.assertEqual(collect_payload["sources"][0]["sector"], "innovation_drug")
            self.assertEqual(len(collect_payload["snapshots"]), 1)

            reviewer_payload = reviewer.run(
                source_run_id="01REVIEWTARGET",
                query="review thesis for innovation drug",
                source_entries=collect_payload["sources"][0]["review_source_entries"],
                latest_sector_snapshot=collect_payload["snapshots"][0],
            )
            expected_scope_explanations = reviewer._benchmark_scope_explanations({"sector_vs_hs300": 3})
            self.assertEqual(reviewer_payload["summary"]["finding_count"], 3)
            self.assertEqual(reviewer_payload["summary"]["claim_type_counts"]["direction"], 1)
            self.assertEqual(reviewer_payload["summary"]["claim_type_counts"]["catalyst"], 1)
            self.assertEqual(reviewer_payload["summary"]["claim_type_counts"]["time_window"], 1)
            self.assertEqual(reviewer_payload["summary"]["benchmark_scope_counts"], {"sector_vs_hs300": 3})
            self.assertEqual(
                reviewer_payload["summary"]["benchmark_scope_explanations"],
                expected_scope_explanations,
            )
            self.assertEqual(reviewer_payload["summary"]["overall_verdict"], "confirmed")
            self.assertEqual(
                reviewer_payload["summary"]["summary_text"],
                "overall_verdict=confirmed; findings=3; confirmed=3; rejected=0; inconclusive=0; benchmark_scopes=sector_vs_hs300",
            )

            blackboard_entries = [
                _blackboard_entry(
                    run_id="01REVIEWRUN",
                    request_id="req-review",
                    kind="verified_snapshot",
                    agent="collector",
                    phase="collect",
                    payload=collect_payload["snapshots"][0],
                    source_refs=[{"type": "skill", "skill": "sector_snapshot_skill", "run_id": "01REVIEWRUN"}],
                    confidence=1.0,
                ).model_dump(mode="json"),
                *[
                    _blackboard_entry(
                        run_id="01REVIEWRUN",
                        request_id="req-review",
                        kind="review_finding",
                        agent="reviewer",
                        phase="review",
                        payload=finding,
                        source_refs=[{"type": "posterior_review", "review_target_run_id": "01REVIEWTARGET", "run_id": "01REVIEWRUN"}],
                        confidence=1.0,
                    ).model_dump(mode="json")
                    for finding in reviewer_payload["findings"]
                ],
                _blackboard_entry(
                    run_id="01REVIEWRUN",
                    request_id="req-review",
                    kind="source_record",
                    agent="reviewer",
                    phase="review",
                    payload={
                        "title": "review_summary",
                        "content": str(reviewer_payload["summary"]),
                        "source_type": "review_summary",
                        "summary": reviewer_payload["summary"],
                    },
                    source_refs=[{"type": "posterior_review", "review_target_run_id": "01REVIEWTARGET", "run_id": "01REVIEWRUN"}],
                    confidence=1.0,
                ).model_dump(mode="json"),
                _blackboard_entry(
                    run_id="01REVIEWRUN",
                    request_id="req-review",
                    kind="source_record",
                    agent="reviewer",
                    phase="review",
                    payload={
                        "title": "methodology_dcf:300363",
                        "content": "{'valuation_band': {'low': 8.0, 'base': 9.0, 'high': 10.0}}",
                        "source_type": "methodology_dcf",
                        "methodology_type": "dcf",
                        "symbol": "300363",
                        "valuation_band_label": "8.0|9.0|10.0",
                        "methodology_result": {
                            "outputs": {"intrinsic_value_proxy": 9.0},
                            "warnings": [],
                        },
                    },
                    source_refs=[{"type": "posterior_review_methodology", "run_id": "01REVIEWRUN"}],
                    confidence=0.6,
                ).model_dump(mode="json"),
            ]

            evidences = [retriever._evidence_from_blackboard_entry(entry) for entry in blackboard_entries]
            selected = retriever._select_review_evidences(evidences=evidences, top_k=6)
            retrieval_summary = retriever._structured_retrieval_summary(
                intent="review_thesis",
                evidences=selected,
            )
            report = reporter.run(
                intent="review_thesis",
                query="review thesis for innovation drug",
                evidences=selected,
                retrieval_summary=retrieval_summary,
            )

            self.assertIsNotNone(retrieval_summary)
            assert retrieval_summary is not None
            self.assertIn("Posterior review: review_findings=3", retrieval_summary)
            self.assertIn("verdict_counts={'confirmed': 3}", retrieval_summary)
            self.assertIn("claim_type_counts={'direction': 1, 'catalyst': 1, 'time_window': 1}", retrieval_summary)
            self.assertIn("verdict_by_claim_type", retrieval_summary)
            self.assertIn("benchmark_scope_counts={'sector_vs_hs300': 3}", retrieval_summary)
            self.assertIn(f"benchmark_scope_explanations={expected_scope_explanations}", retrieval_summary)
            self.assertIn("overall_verdict=confirmed", retrieval_summary)
            self.assertIn("summary_text=overall_verdict=confirmed; findings=3; confirmed=3; rejected=0; inconclusive=0; benchmark_scopes=sector_vs_hs300", retrieval_summary)
            self.assertIn("'direction': {'confirmed': 1, 'rejected': 0, 'inconclusive': 0}", retrieval_summary)
            self.assertIn("methodology_types=['dcf']", retrieval_summary)
            self.assertIn("overall_verdict=confirmed", report["conclusion"])
            self.assertTrue(report["methodologies"])
            self.assertEqual(
                report["review_summary"]["verdict_by_claim_type"],
                {
                    "direction": {"confirmed": 1, "rejected": 0, "inconclusive": 0},
                    "catalyst": {"confirmed": 1, "rejected": 0, "inconclusive": 0},
                    "time_window": {"confirmed": 1, "rejected": 0, "inconclusive": 0},
                },
            )
            self.assertEqual(
                report["review_summary"]["benchmark_scope_counts"],
                {"sector_vs_hs300": 3},
            )
            self.assertEqual(report["review_summary"]["overall_verdict"], "confirmed")
            self.assertEqual(report["degraded_note"], "llm_unavailable")

    def test_review_thesis_flow_loads_symbol_snapshots_for_stock_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)

            _write_stock_review_target(settings, "01STOCKTARGET")

            collector = CollectorService(ReviewSkillRuntimeStub(), settings.runs_root)
            reviewer = ReviewerService({"benchmark_resolution": "sector", "default_broad_index": "HS300"})

            collect_payload = collector.run(
                intent="review_thesis",
                query="review thesis for 000001",
                constraints={"review_target_run_id": "01STOCKTARGET"},
            )

            self.assertEqual(collect_payload["sources"][0]["symbol"], "000001")
            snapshot_types = [item["snapshot_type"] for item in collect_payload["snapshots"]]
            self.assertEqual(snapshot_types, ["market", "fundamental", "notice"])

            market_snapshot = next(item for item in collect_payload["snapshots"] if item["snapshot_type"] == "market")
            fundamental_snapshot = next(item for item in collect_payload["snapshots"] if item["snapshot_type"] == "fundamental")
            notice_snapshot = next(item for item in collect_payload["snapshots"] if item["snapshot_type"] == "notice")

            reviewer_payload = reviewer.run(
                source_run_id="01STOCKTARGET",
                query="review thesis for 000001",
                source_entries=collect_payload["sources"][0]["review_source_entries"],
                latest_sector_snapshot=None,
                latest_market_snapshot=market_snapshot,
                latest_fundamental_snapshot=fundamental_snapshot,
                latest_notice_snapshot=notice_snapshot,
            )

            self.assertTrue(reviewer_payload["summary"]["review_required"])
            self.assertIn("missing_sector_snapshot", reviewer_payload["summary"]["notes"])

    def test_orchestrator_review_thesis_consumes_methodology_memory_context_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            memory_service = MemoryService(LocalMemoryAdapter(settings))
            memory_service.write_methodology_memory(
                memory_type="dimension_experience",
                content={
                    "title": "Cashflow resilience heuristic",
                    "summary": "Prefer stable free cash flow and lower leverage.",
                    "benchmark_scope": "defensive_compounder",
                },
            )

            _write_stock_review_target(settings, "01REVIEWTARGET")

            orchestrator = ResearchOrchestrator(settings)
            try:
                orchestrator.collector.skill_runtime = ReviewSkillRuntimeStub()
                orchestrator.retriever.disable_qdrant_for_test()
                response = orchestrator.run(
                    RequestEnvelope(
                        user_query="continue the previous review",
                        intent="review_thesis",
                        constraints={"review_target_run_id": "01REVIEWTARGET"},
                        conversation_context=[
                            {
                                "type": "relevant_methodology_memory",
                                "memory_type": "dimension_experience",
                                "benchmark_scope": "defensive_compounder",
                                "match_score": 3.5,
                            }
                        ],
                    )
                )
            finally:
                orchestrator.close()

            self.assertEqual(response.status, "completed")
            report = (response.result or {}).get("report") or {}
            debug = report.get("debug") or {}
            retrieval_summary = debug.get("retrieval_summary") or ""
            self.assertIn("methodology_memory=", retrieval_summary)
            self.assertIn("Cashflow resilience heuristic", retrieval_summary)
            self.assertIn("review_focus=cashflow_resilience", (report.get("review_summary") or {}).get("notes") or [])
            self.assertIn("cashflow resilience", str(report.get("follow_up") or "").lower())


if __name__ == "__main__":
    unittest.main()
