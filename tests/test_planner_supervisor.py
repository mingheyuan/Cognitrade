from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.planner_action_factory import PlannerActionFactory
from research_system.runtime.planner_context_hints import PlannerContextHints
from research_system.runtime.planner import RuntimePlanner
from research_system.runtime.settings import Settings, ensure_workspace_layout
from research_system.runtime.supervisor_blackboard_view import SupervisorBlackboardView
from research_system.runtime.supervisor_decision_factory import SupervisorDecisionFactory
from research_system.runtime.supervisor import RuntimeSupervisor
from research_system.schemas.planning import ActionSpec
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import BlackboardEntry


class PlannerSupervisorTests(unittest.TestCase):
    def test_planner_wires_context_hints_and_action_factory(self) -> None:
        planner = RuntimePlanner()
        self.assertIsInstance(planner.context_hints, PlannerContextHints)
        self.assertIsInstance(planner.action_factory, PlannerActionFactory)

    def test_supervisor_wires_blackboard_view_and_decision_factory(self) -> None:
        supervisor = RuntimeSupervisor()
        self.assertIsInstance(supervisor.blackboard_view, SupervisorBlackboardView)
        self.assertIsInstance(supervisor.decision_factory, SupervisorDecisionFactory)

    def test_stock_check_plan_carries_explicit_intent_dispatch_trace(self) -> None:
        planner = RuntimePlanner()
        plan = planner.build_initial_plan(
            intent="stock_check",
            request=RequestEnvelope(intent="stock_check", user_query="check 000001"),
        )
        self.assertEqual(plan.entry_strategy, "explicit_intent")
        self.assertTrue(plan.subagent_required)
        self.assertEqual(plan.dispatch_trace["intent"], "stock_check")
        self.assertEqual(plan.dispatch_trace["source"], "explicit_intent")
        self.assertEqual(plan.dispatch_trace["context_hints"], {})

    def test_stock_check_plan_includes_thread_profile_context_hints(self) -> None:
        planner = RuntimePlanner()
        plan = planner.build_initial_plan(
            intent="stock_check",
            request=RequestEnvelope(
                user_query="continue the previous conclusion",
                conversation_context=[
                    {
                        "type": "chat_thread_profile",
                        "topic_title": "compare maotai and byd",
                        "topic_intent": "stock_compare",
                        "last_result_summary": "comparison is still cautious",
                        "recommended_next_step": "supplement same-basis cashflow and valuation assumptions",
                        "open_question": "Do you want medium-term or long-term comparison?",
                        "thread_status": "clarification_pending",
                    }
                ],
            ),
        )
        self.assertIn(plan.entry_strategy, {"rule", "llm"})
        self.assertEqual(plan.dispatch_trace["context_hints"]["thread_topic"], "compare maotai and byd")
        self.assertEqual(
            plan.dispatch_trace["context_hints"]["recommended_next_step"],
            "supplement same-basis cashflow and valuation assumptions",
        )
        self.assertEqual(
            plan.dispatch_trace["context_hints"]["open_question"],
            "Do you want medium-term or long-term comparison?",
        )
        self.assertIn("thread_next_step=", plan.entry_reason or "")
        self.assertIn("thread_open_question=", plan.entry_reason or "")
        retrieve_action = next(action for action in plan.actions if action.node_id == "retrieve")
        self.assertEqual(
            retrieve_action.inputs["planning_context_hints"]["recommended_next_step"],
            "supplement same-basis cashflow and valuation assumptions",
        )

    def test_stock_check_plan_can_fall_back_to_relevant_thread_profile_context(self) -> None:
        planner = RuntimePlanner()
        plan = planner.build_initial_plan(
            intent="stock_check",
            request=RequestEnvelope(
                user_query="continue the previous conclusion",
                conversation_context=[
                    {
                        "type": "relevant_thread_profile",
                        "topic_title": "cashflow comparison follow-up",
                        "topic_intent": "stock_compare",
                        "last_result_summary": "cashflow confidence is still weak",
                        "recommended_next_step": "validate cashflow resilience and leverage tolerance",
                        "open_question": "Is leverage still acceptable?",
                        "thread_status": "active",
                    }
                ],
            ),
        )
        self.assertEqual(plan.dispatch_trace["context_hints"]["thread_topic"], "cashflow comparison follow-up")
        self.assertEqual(
            plan.dispatch_trace["context_hints"]["recommended_next_step"],
            "validate cashflow resilience and leverage tolerance",
        )

    def test_stock_check_plan_includes_relevant_methodology_memory_hints(self) -> None:
        planner = RuntimePlanner()
        plan = planner.build_initial_plan(
            intent="stock_check",
            request=RequestEnvelope(
                user_query="continue the previous conclusion",
                conversation_context=[
                    {
                        "type": "relevant_methodology_memory",
                        "memory_type": "dimension_experience",
                        "benchmark_scope": "defensive_compounder",
                        "match_score": 3.5,
                    }
                ],
            ),
        )
        self.assertEqual(
            plan.dispatch_trace["context_hints"]["methodology_benchmark_scope"],
            "defensive_compounder",
        )
        retrieve_action = next(action for action in plan.actions if action.node_id == "retrieve")
        self.assertEqual(
            retrieve_action.inputs["planning_context_hints"]["methodology_memory_type"],
            "dimension_experience",
        )

    def test_stock_screen_initial_plan_is_dynamic_and_excludes_screen_review(self) -> None:
        planner = RuntimePlanner()
        plan = planner.build_initial_plan(
            intent="stock_screen",
            request=RequestEnvelope(user_query="recent stock ideas"),
        )
        self.assertEqual(plan.strategy, "dynamic")
        self.assertIn(plan.entry_strategy, {"rule", "llm"})
        self.assertEqual([action.node_id for action in plan.actions], ["collect", "screen", "retrieve", "report"])

    def test_review_thesis_initial_plan_is_dynamic_and_excludes_review(self) -> None:
        planner = RuntimePlanner()
        plan = planner.build_initial_plan(
            intent="review_thesis",
            request=RequestEnvelope(user_query="review thesis for innovation drug"),
        )
        self.assertEqual(plan.strategy, "dynamic")
        self.assertEqual([action.node_id for action in plan.actions], ["collect", "retrieve", "report"])

    def test_review_thesis_plan_carries_thread_profile_context_hints(self) -> None:
        planner = RuntimePlanner()
        plan = planner.build_initial_plan(
            intent="review_thesis",
            request=RequestEnvelope(
                user_query="review prior thesis",
                conversation_context=[
                    {
                        "type": "chat_thread_profile",
                        "topic_title": "cashflow comparison follow-up",
                        "topic_intent": "stock_compare",
                        "last_result_summary": "current evidence still lacks cashflow confidence",
                        "recommended_next_step": "validate cashflow resilience and leverage tolerance",
                        "open_question": "Is the debt profile still acceptable?",
                        "thread_status": "active",
                    }
                ],
            ),
        )
        self.assertEqual(
            next(action for action in plan.actions if action.node_id == "collect").inputs["planning_context_hints"]["recommended_next_step"],
            "validate cashflow resilience and leverage tolerance",
        )
        self.assertEqual(
            next(action for action in plan.actions if action.node_id == "report").inputs["planning_context_hints"]["open_question"],
            "Is the debt profile still acceptable?",
        )

    def test_stock_check_initial_plan_is_dynamic(self) -> None:
        planner = RuntimePlanner()
        plan = planner.build_initial_plan(
            intent="stock_check",
            request=RequestEnvelope(user_query="check 000001"),
        )
        self.assertEqual(plan.strategy, "dynamic")
        self.assertEqual([action.node_id for action in plan.actions], ["collect", "retrieve", "report"])

    def test_sector_research_initial_plan_is_dynamic(self) -> None:
        planner = RuntimePlanner()
        plan = planner.build_initial_plan(
            intent="sector_research",
            request=RequestEnvelope(user_query="innovation drug sector"),
        )
        self.assertEqual(plan.strategy, "dynamic")
        self.assertEqual([action.node_id for action in plan.actions], ["collect", "retrieve", "report"])

    def test_supervisor_inserts_screen_review_after_screening_when_candidates_exist(self) -> None:
        planner = RuntimePlanner()
        supervisor = RuntimeSupervisor()
        plan = planner.build_initial_plan(
            intent="stock_screen",
            request=RequestEnvelope(user_query="recent stock ideas"),
        )
        pending_actions = [action for action in plan.actions if action.node_id in {"retrieve", "report"}]
        scorecard_entry = BlackboardEntry(
            run_id="01TEST",
            request_id="req_1",
            kind="source_record",
            agent="screener",
            phase="screen",
            payload={
                "title": "screening_scorecard",
                "content": "{}",
                "source_type": "screening_scorecard",
                "scorecard": {
                    "top_candidate_count": 1,
                    "watchlist_candidate_count": 0,
                },
            },
        )
        decision = supervisor.decide(
            plan=plan,
            completed_actions=["collect", "screen"],
            pending_actions=pending_actions,
            blackboard_entries=[scorecard_entry],
        )
        self.assertEqual(decision.decision_type, "continue")
        self.assertEqual(len(decision.next_actions), 1)
        self.assertEqual(decision.next_actions[0].node_id, "screen_review")
        self.assertEqual(decision.next_actions[0].agent, "screen_reviewer")
        self.assertEqual(decision.next_actions[0].inputs, {})

    def test_supervisor_passes_context_hints_into_dynamic_screen_review_action(self) -> None:
        planner = RuntimePlanner()
        supervisor = RuntimeSupervisor()
        plan = planner.build_initial_plan(
            intent="stock_screen",
            request=RequestEnvelope(
                user_query="continue the previous watchlist discussion",
                conversation_context=[
                    {
                        "type": "chat_thread_profile",
                        "topic_title": "screen short-term candidates with cashflow resilience",
                        "recommended_next_step": "compare same-basis cashflow and short-term sentiment",
                        "thread_status": "active",
                    }
                ],
            ),
        )
        pending_actions = [action for action in plan.actions if action.node_id in {"retrieve", "report"}]
        scorecard_entry = BlackboardEntry(
            run_id="01TEST",
            request_id="req_1",
            kind="source_record",
            agent="screener",
            phase="screen",
            payload={
                "title": "screening_scorecard",
                "content": "{}",
                "source_type": "screening_scorecard",
                "scorecard": {
                    "top_candidate_count": 1,
                    "watchlist_candidate_count": 0,
                },
            },
        )
        decision = supervisor.decide(
            plan=plan,
            completed_actions=["collect", "screen"],
            pending_actions=pending_actions,
            blackboard_entries=[scorecard_entry],
        )
        self.assertEqual(
            decision.next_actions[0].inputs["planning_context_hints"]["recommended_next_step"],
            "compare same-basis cashflow and short-term sentiment",
        )

    def test_supervisor_skips_screen_review_when_review_summary_already_exists(self) -> None:
        planner = RuntimePlanner()
        supervisor = RuntimeSupervisor()
        plan = planner.build_initial_plan(
            intent="stock_screen",
            request=RequestEnvelope(user_query="recent stock ideas"),
        )
        pending_actions = [
            ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever"),
            ActionSpec(node_id="report", phase="report", agent="reporter"),
        ]
        scorecard_entry = BlackboardEntry(
            run_id="01TEST",
            request_id="req_1",
            kind="source_record",
            agent="screener",
            phase="screen",
            payload={
                "title": "screening_scorecard",
                "content": "{}",
                "source_type": "screening_scorecard",
                "scorecard": {
                    "top_candidate_count": 1,
                    "watchlist_candidate_count": 0,
                },
            },
        )
        review_summary_entry = BlackboardEntry(
            run_id="01TEST",
            request_id="req_1",
            kind="source_record",
            agent="screen_reviewer",
            phase="review",
            payload={
                "title": "review_summary",
                "content": "{}",
                "source_type": "review_summary",
                "summary": {"overall_verdict": "confirmed"},
            },
        )
        decision = supervisor.decide(
            plan=plan,
            completed_actions=["collect", "screen"],
            pending_actions=pending_actions,
            blackboard_entries=[scorecard_entry, review_summary_entry],
        )
        self.assertEqual(decision.next_actions[0].node_id, "retrieve")

    def test_supervisor_inserts_review_for_review_thesis_when_target_and_snapshots_exist(self) -> None:
        planner = RuntimePlanner()
        supervisor = RuntimeSupervisor()
        plan = planner.build_initial_plan(
            intent="review_thesis",
            request=RequestEnvelope(user_query="review thesis for innovation drug"),
        )
        pending_actions = [
            ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever"),
            ActionSpec(node_id="report", phase="report", agent="reporter"),
        ]
        review_request_entry = BlackboardEntry(
            run_id="01TEST",
            request_id="req_1",
            kind="source_record",
            agent="collector",
            phase="collect",
            payload={
                "title": "review_thesis input request",
                "content": "review thesis for innovation drug",
                "source_type": "review_request",
                "review_target_run_id": "01TARGET",
                "review_source_entries": [{"kind": "report_fragment", "payload": {"conclusion": "test"}}],
            },
        )
        sector_snapshot_entry = BlackboardEntry(
            run_id="01TEST",
            request_id="req_1",
            kind="verified_snapshot",
            agent="collector",
            phase="collect",
            payload={
                "snapshot_type": "sector",
                "sector": "innovation_drug",
                "snapshot_date": "2026-06-28",
                "facts": {},
                "source": {"skill": "sector_snapshot_skill"},
            },
        )
        decision = supervisor.decide(
            plan=plan,
            completed_actions=["collect"],
            pending_actions=pending_actions,
            blackboard_entries=[review_request_entry, sector_snapshot_entry],
        )
        self.assertEqual(decision.decision_type, "continue")
        self.assertEqual(decision.next_actions[0].node_id, "review")
        self.assertEqual(decision.next_actions[0].agent, "reviewer")
        self.assertEqual(decision.next_actions[0].inputs, {})

    def test_supervisor_passes_context_hints_into_dynamic_review_action(self) -> None:
        planner = RuntimePlanner()
        supervisor = RuntimeSupervisor()
        plan = planner.build_initial_plan(
            intent="review_thesis",
            request=RequestEnvelope(
                user_query="review thesis for innovation drug",
                conversation_context=[
                    {
                        "type": "chat_thread_profile",
                        "topic_title": "cashflow review",
                        "recommended_next_step": "validate cashflow resilience and leverage tolerance",
                    }
                ],
            ),
        )
        pending_actions = [
            ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever"),
            ActionSpec(node_id="report", phase="report", agent="reporter"),
        ]
        review_request_entry = BlackboardEntry(
            run_id="01TEST",
            request_id="req_1",
            kind="source_record",
            agent="collector",
            phase="collect",
            payload={
                "title": "review_thesis input request",
                "content": "review thesis for innovation drug",
                "source_type": "review_request",
                "review_target_run_id": "01TARGET",
                "review_source_entries": [{"kind": "report_fragment", "payload": {"conclusion": "test"}}],
            },
        )
        sector_snapshot_entry = BlackboardEntry(
            run_id="01TEST",
            request_id="req_1",
            kind="verified_snapshot",
            agent="collector",
            phase="collect",
            payload={
                "snapshot_type": "sector",
                "sector": "innovation_drug",
                "snapshot_date": "2026-06-28",
                "facts": {},
                "source": {"skill": "sector_snapshot_skill"},
            },
        )
        decision = supervisor.decide(
            plan=plan,
            completed_actions=["collect"],
            pending_actions=pending_actions,
            blackboard_entries=[review_request_entry, sector_snapshot_entry],
        )
        self.assertEqual(
            decision.next_actions[0].inputs["planning_context_hints"]["recommended_next_step"],
            "validate cashflow resilience and leverage tolerance",
        )

    def test_supervisor_inserts_stock_check_replenish_when_notice_missing(self) -> None:
        planner = RuntimePlanner()
        supervisor = RuntimeSupervisor()
        plan = planner.build_initial_plan(
            intent="stock_check",
            request=RequestEnvelope(user_query="check 000001"),
        )
        pending_actions = [
            ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever"),
            ActionSpec(node_id="report", phase="report", agent="reporter"),
        ]
        request_entry = BlackboardEntry(
            run_id="01TEST",
            request_id="req_1",
            kind="source_record",
            agent="collector",
            phase="collect",
            payload={
                "title": "stock_check input query",
                "content": "check 000001",
                "source_type": "user_query",
                "symbol": "000001",
            },
        )
        market_snapshot_entry = BlackboardEntry(
            run_id="01TEST",
            request_id="req_1",
            kind="verified_snapshot",
            agent="collector",
            phase="collect",
            payload={
                "snapshot_type": "market",
                "symbol": "000001",
                "snapshot_date": "2026-06-28",
                "facts": {},
                "source": {"skill": "market_snapshot_skill"},
            },
        )
        fundamental_snapshot_entry = BlackboardEntry(
            run_id="01TEST",
            request_id="req_1",
            kind="verified_snapshot",
            agent="collector",
            phase="collect",
            payload={
                "snapshot_type": "fundamental",
                "symbol": "000001",
                "snapshot_date": "2026-06-28",
                "facts": {},
                "source": {"skill": "fundamental_snapshot_skill"},
            },
        )
        decision = supervisor.decide(
            plan=plan,
            completed_actions=["collect"],
            pending_actions=pending_actions,
            blackboard_entries=[request_entry, market_snapshot_entry, fundamental_snapshot_entry],
        )
        self.assertEqual(decision.decision_type, "continue")
        self.assertEqual(decision.next_actions[0].agent, "collector")
        self.assertIn("replenish", decision.next_actions[0].tags)
        self.assertEqual(decision.next_actions[0].inputs["snapshot_types"], ["notice"])

    def test_supervisor_inserts_sector_replenish_when_critical_sector_evidence_missing(self) -> None:
        planner = RuntimePlanner()
        supervisor = RuntimeSupervisor()
        plan = planner.build_initial_plan(
            intent="sector_research",
            request=RequestEnvelope(user_query="innovation drug sector"),
        )
        pending_actions = [
            ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever"),
            ActionSpec(node_id="report", phase="report", agent="reporter"),
        ]
        request_entry = BlackboardEntry(
            run_id="01TEST",
            request_id="req_1",
            kind="source_record",
            agent="collector",
            phase="collect",
            payload={
                "title": "sector_research input query",
                "content": "innovation drug sector",
                "source_type": "user_query",
            },
        )
        sector_snapshot_entry = BlackboardEntry(
            run_id="01TEST",
            request_id="req_1",
            kind="verified_snapshot",
            agent="collector",
            phase="collect",
            payload={
                "snapshot_type": "sector",
                "sector": "innovation_drug",
                "query": "innovation drug sector",
                "snapshot_date": "2026-06-28",
                "facts": {
                    "benchmark": {"benchmark_name": "HS300"},
                },
                "source": {"skill": "sector_snapshot_skill"},
            },
        )
        decision = supervisor.decide(
            plan=plan,
            completed_actions=["collect"],
            pending_actions=pending_actions,
            blackboard_entries=[request_entry, sector_snapshot_entry],
        )
        self.assertEqual(decision.decision_type, "continue")
        self.assertEqual(decision.next_actions[0].agent, "collector")
        self.assertIn("replenish", decision.next_actions[0].tags)
        self.assertEqual(
            decision.next_actions[0].inputs["missing_evidence"],
            ["policy_signals", "regulatory_signals", "relative_strength"],
        )

    def test_orchestrator_emits_dynamic_planning_events_for_stock_screen(self) -> None:
        events: list[str] = []

        class SkillRuntimeStub:
            def run(self, skill_name: str, **kwargs):
                raise AssertionError(f"unexpected skill call: {skill_name}")

        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings, event_listener=lambda event: events.append(event.event_type))
            try:
                orchestrator.collector.skill_runtime = SkillRuntimeStub()
                orchestrator.retriever.disable_qdrant_for_test()

                with patch(
                    "research_system.subagents.collector.call_with_retry",
                    return_value=self._spot_table(),
                ):
                    response = orchestrator.run(
                        RequestEnvelope(
                            user_query="recent stock ideas",
                            constraints={"skip_enrichment": True},
                        )
                    )
                self.assertEqual(response.intent, "stock_screen")
                self.assertEqual(response.status, "completed")
            finally:
                orchestrator.close()

        self.assertIn("plan_created", events)
        self.assertIn("supervisor_decision", events)
        self.assertIn("dynamic_action_inserted", events)

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
