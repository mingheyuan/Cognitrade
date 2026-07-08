from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_system.runtime.planning import RuntimePlanner
from research_system.runtime.planning.action_factory import PlannerActionFactory
from research_system.runtime.planning.context_hints import PlannerContextHints
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.runtime.storage.jsonl import read_json
from research_system.runtime.supervision import RuntimeSupervisor
from research_system.runtime.supervision.blackboard_view import SupervisorBlackboardView
from research_system.runtime.supervision.decision_factory import SupervisorDecisionFactory
from research_system.schemas.planning import ActionPlan, ActionPlanPatch, ActionPlanPatchOperation, ActionSpec
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import BlackboardEntry


def _blackboard_entry(
    *,
    kind: str,
    payload: dict,
    run_id: str = "01TEST",
    thread_id: str = "thread_01",
    request_id: str = "req_1",
    agent: str = "collector",
    phase: str = "collect",
) -> BlackboardEntry:
    return BlackboardEntry(
        run_id=run_id,
        thread_id=thread_id,
        request_id=request_id,
        kind=kind,
        agent=agent,
        phase=phase,
        payload=payload,
    )


class PlannerSupervisorTests(unittest.TestCase):
    def test_planner_wires_context_hints_and_action_factory(self) -> None:
        planner = RuntimePlanner()

        self.assertIsInstance(planner.context_hints, PlannerContextHints)
        self.assertIsInstance(planner.action_factory, PlannerActionFactory)

    def test_planner_auto_binds_local_runtime_when_called_standalone(self) -> None:
        class TrackingPlanner(RuntimePlanner):
            def __init__(self, **kwargs) -> None:
                super().__init__(**kwargs)
                self.bind_calls = 0
                self.clear_calls = 0

            def bind_runtime(self, *, context, support, request_id: str, intent: str) -> None:
                self.bind_calls += 1
                super().bind_runtime(context=context, support=support, request_id=request_id, intent=intent)

            def clear_runtime(self) -> None:
                self.clear_calls += 1
                super().clear_runtime()

        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            planner = TrackingPlanner(settings=settings)

            plan = planner.build_initial_plan(
                intent="stock_check",
                request=RequestEnvelope(user_query="check 000001"),
            )

            self.assertEqual(planner.bind_calls, 1)
            self.assertEqual(planner.clear_calls, 1)
            self.assertEqual(plan.intent, "stock_check")

    def test_planner_local_runtime_uses_settings_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            settings.max_subagent_calls_per_turn = 6
            settings.max_child_threads_per_turn = 3
            planner = RuntimePlanner(settings=settings)

            with patch(
                "research_system.runtime.planning.planner.bind_local_runtime",
                wraps=__import__("research_system.runtime.planning.planner", fromlist=["bind_local_runtime"]).bind_local_runtime,
            ) as bind_runtime:
                plan = planner.build_initial_plan(
                    intent="stock_check",
                    request=RequestEnvelope(user_query="check 000001"),
                )

            self.assertEqual(plan.intent, "stock_check")
            self.assertEqual(bind_runtime.call_args.kwargs["runs_root"], settings.runs_root)
            self.assertEqual(bind_runtime.call_args.kwargs["max_subagent_calls_per_turn"], 6)
            self.assertEqual(bind_runtime.call_args.kwargs["max_child_threads_per_turn"], 3)

    def test_planner_local_runtime_finalizes_thread_and_turn_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            planner = RuntimePlanner(settings=settings)

            plan = planner.build_initial_plan(
                intent="stock_check",
                request=RequestEnvelope(user_query="check 000001"),
            )

            self.assertEqual(plan.intent, "stock_check")
            run_root = next(Path(settings.runs_root).iterdir())
            self.assertEqual((read_json(run_root / "thread_state.json") or {}).get("status"), "completed")
            self.assertEqual((read_json(run_root / "turn_state.json") or {}).get("status"), "completed")

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

    def test_replan_action_plan_preserves_replan_metadata(self) -> None:
        planner = RuntimePlanner()
        current_plan = ActionPlan(
            plan_id="plan_current",
            intent="stock_screen",
            strategy="dynamic",
            actions=[
                ActionSpec(node_id="collect", phase="collect", agent="collector"),
                ActionSpec(node_id="screen", phase="screen", agent="screener", depends_on=["collect"]),
                ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever", depends_on=["screen"]),
                ActionSpec(node_id="report", phase="report", agent="reporter", depends_on=["retrieve"]),
            ],
            dispatch_trace={"source": "route_table"},
        )
        plan_patch = ActionPlanPatch(
            decision_type="replan",
            operations=[
                ActionPlanPatchOperation(
                    op_type="request_replan",
                    reason="screen follow-up should be rebuilt",
                    metadata={"trigger": "manual_test"},
                )
            ],
            notes=["request_action_plan_replan"],
            policy_source="rule",
            termination_reason="screen follow-up should be rebuilt",
        )

        replanned = planner.replan_action_plan(
            intent="stock_screen",
            request=RequestEnvelope(user_query="screen growth stocks"),
            current_plan=current_plan,
            plan_patch=plan_patch,
        )

        self.assertEqual(replanned.intent, "stock_screen")
        self.assertEqual(replanned.dispatch_trace["replanned_from_plan_id"], "plan_current")
        self.assertEqual(replanned.dispatch_trace["replan_patch_id"], plan_patch.patch_id)
        self.assertEqual(replanned.dispatch_trace["replan_policy_source"], "rule")
        self.assertEqual(replanned.dispatch_trace["replan_reason"], "screen follow-up should be rebuilt")
        self.assertIn("request_action_plan_replan", replanned.notes)
        self.assertIn("replanned_from:plan_current", replanned.notes)

    def test_replan_action_plan_uses_runtime_blackboard_to_rebuild_stock_screen_follow_up(self) -> None:
        planner = RuntimePlanner()
        current_plan = ActionPlan(
            plan_id="plan_current",
            intent="stock_screen",
            strategy="dynamic",
            actions=[
                ActionSpec(node_id="collect", phase="collect", agent="collector"),
                ActionSpec(node_id="screen", phase="screen", agent="screener", depends_on=["collect"]),
                ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever", depends_on=["screen"]),
                ActionSpec(node_id="report", phase="report", agent="reporter", depends_on=["retrieve"]),
            ],
            entry_strategy="rule",
            dispatch_trace={"source": "route_table"},
        )
        plan_patch = ActionPlanPatch(
            decision_type="replan",
            operations=[
                ActionPlanPatchOperation(
                    op_type="request_replan",
                    reason="screen follow-up should be rebuilt",
                    metadata={"trigger": "screen_completed"},
                )
            ],
            notes=["request_action_plan_replan"],
            policy_source="rule",
            termination_reason="screen follow-up should be rebuilt",
        )
        scorecard_entry = _blackboard_entry(
            kind="source_record",
            agent="screener",
            phase="screen",
            payload={
                "title": "screening_scorecard",
                "content": "{}",
                "source_type": "screening_scorecard",
                "scorecard": {
                    "top_candidate_count": 2,
                    "watchlist_candidate_count": 1,
                    "enrichment_target_count": 2,
                    "enrichment_pending": True,
                },
            },
        )

        class _Blackboard:
            def __init__(self, entries) -> None:
                self._entries = entries

            def list_entries(self, *, thread_id=None):
                return list(self._entries)

        class _Thread:
            def __init__(self, thread_id: str) -> None:
                self.thread_id = thread_id

        class _Context:
            def __init__(self, entries) -> None:
                self.thread = _Thread("thread_01")
                self.blackboard = _Blackboard(entries)

        planner.bind_runtime(context=_Context([scorecard_entry]), support=None, request_id="req_1", intent="stock_screen")
        try:
            replanned = planner.replan_action_plan(
                intent="stock_screen",
                request=RequestEnvelope(user_query="screen growth stocks"),
                current_plan=current_plan,
                plan_patch=plan_patch,
            )
        finally:
            planner.clear_runtime()

        self.assertEqual(
            [action.node_id for action in replanned.actions],
            ["collect", "screen", "screen_enrich", "screen_review", "retrieve", "report"],
        )
        self.assertEqual(replanned.actions[2].depends_on, ["screen"])
        self.assertEqual(replanned.actions[3].depends_on, ["screen_enrich"])
        self.assertEqual(replanned.actions[4].depends_on, ["screen_review"])

    def test_replan_action_plan_uses_runtime_blackboard_to_insert_review_phase(self) -> None:
        planner = RuntimePlanner()
        current_plan = ActionPlan(
            plan_id="plan_current",
            intent="review_thesis",
            strategy="dynamic",
            actions=[
                ActionSpec(node_id="collect", phase="collect", agent="collector"),
                ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever", depends_on=["collect"]),
                ActionSpec(node_id="report", phase="report", agent="reporter", depends_on=["retrieve"]),
            ],
            entry_strategy="explicit_intent",
            dispatch_trace={"source": "explicit_intent"},
        )
        plan_patch = ActionPlanPatch(
            decision_type="replan",
            operations=[
                ActionPlanPatchOperation(
                    op_type="request_replan",
                    reason="review path should be rebuilt",
                    metadata={"trigger": "collect_completed"},
                )
            ],
            notes=["request_action_plan_replan"],
            policy_source="rule",
            termination_reason="review path should be rebuilt",
        )
        review_request_entry = _blackboard_entry(
            kind="source_record",
            payload={
                "title": "review_thesis input request",
                "content": "review thesis for innovation drug",
                "source_type": "review_request",
                "review_target_run_id": "01TARGET",
                "review_source_entries": [{"kind": "report_fragment", "payload": {"conclusion": "test"}}],
            },
        )
        sector_snapshot_entry = _blackboard_entry(
            kind="verified_snapshot",
            payload={
                "snapshot_type": "sector",
                "sector": "innovation_drug",
                "snapshot_date": "2026-06-28",
                "facts": {},
                "source": {"skill": "sector_snapshot_skill"},
            },
        )

        class _Blackboard:
            def __init__(self, entries) -> None:
                self._entries = entries

            def list_entries(self, *, thread_id=None):
                return list(self._entries)

        class _Thread:
            def __init__(self, thread_id: str) -> None:
                self.thread_id = thread_id

        class _Context:
            def __init__(self, entries) -> None:
                self.thread = _Thread("thread_01")
                self.blackboard = _Blackboard(entries)

        planner.bind_runtime(
            context=_Context([review_request_entry, sector_snapshot_entry]),
            support=None,
            request_id="req_1",
            intent="review_thesis",
        )
        try:
            replanned = planner.replan_action_plan(
                intent="review_thesis",
                request=RequestEnvelope(user_query="review thesis for innovation drug"),
                current_plan=current_plan,
                plan_patch=plan_patch,
            )
        finally:
            planner.clear_runtime()

        self.assertEqual(
            [action.node_id for action in replanned.actions],
            ["collect", "review", "retrieve", "report"],
        )
        self.assertEqual(replanned.actions[1].depends_on, ["collect"])
        self.assertEqual(replanned.actions[2].depends_on, ["review"])

    def test_replan_action_plan_uses_runtime_blackboard_to_insert_stock_check_replenish(self) -> None:
        planner = RuntimePlanner()
        current_plan = ActionPlan(
            plan_id="plan_current",
            intent="stock_check",
            strategy="dynamic",
            actions=[
                ActionSpec(node_id="collect", phase="collect", agent="collector"),
                ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever", depends_on=["collect"]),
                ActionSpec(node_id="report", phase="report", agent="reporter", depends_on=["retrieve"]),
            ],
            entry_strategy="explicit_intent",
            dispatch_trace={"source": "explicit_intent"},
        )
        plan_patch = ActionPlanPatch(
            decision_type="replan",
            operations=[
                ActionPlanPatchOperation(
                    op_type="request_replan",
                    reason="stock snapshots should be replenished before retrieval",
                    metadata={"trigger": "collect_completed"},
                )
            ],
            notes=["request_action_plan_replan"],
            policy_source="rule",
            termination_reason="stock snapshots should be replenished before retrieval",
        )
        request_entry = _blackboard_entry(
            kind="source_record",
            payload={
                "title": "stock_check input query",
                "content": "check 000001",
                "source_type": "user_query",
                "symbol": "000001",
            },
        )
        market_snapshot_entry = _blackboard_entry(
            kind="verified_snapshot",
            payload={
                "snapshot_type": "market",
                "symbol": "000001",
                "snapshot_date": "2026-06-28",
                "facts": {},
                "source": {"skill": "market_snapshot_skill"},
            },
        )
        fundamental_snapshot_entry = _blackboard_entry(
            kind="verified_snapshot",
            payload={
                "snapshot_type": "fundamental",
                "symbol": "000001",
                "snapshot_date": "2026-06-28",
                "facts": {},
                "source": {"skill": "fundamental_snapshot_skill"},
            },
        )

        class _Blackboard:
            def __init__(self, entries) -> None:
                self._entries = entries

            def list_entries(self, *, thread_id=None):
                return list(self._entries)

        class _Thread:
            def __init__(self, thread_id: str) -> None:
                self.thread_id = thread_id

        class _Context:
            def __init__(self, entries) -> None:
                self.thread = _Thread("thread_01")
                self.blackboard = _Blackboard(entries)

        planner.bind_runtime(
            context=_Context([request_entry, market_snapshot_entry, fundamental_snapshot_entry]),
            support=None,
            request_id="req_1",
            intent="stock_check",
        )
        try:
            replanned = planner.replan_action_plan(
                intent="stock_check",
                request=RequestEnvelope(user_query="check 000001"),
                current_plan=current_plan,
                plan_patch=plan_patch,
            )
        finally:
            planner.clear_runtime()

        self.assertEqual(
            [action.node_id for action in replanned.actions],
            ["collect", "collect_replenish_notice", "retrieve", "report"],
        )
        self.assertEqual(replanned.actions[1].depends_on, ["collect"])
        self.assertEqual(replanned.actions[2].depends_on, ["collect_replenish_notice"])

    def test_replan_action_plan_uses_runtime_blackboard_to_insert_sector_replenish(self) -> None:
        planner = RuntimePlanner()
        current_plan = ActionPlan(
            plan_id="plan_current",
            intent="sector_research",
            strategy="dynamic",
            actions=[
                ActionSpec(node_id="collect", phase="collect", agent="collector"),
                ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever", depends_on=["collect"]),
                ActionSpec(node_id="report", phase="report", agent="reporter", depends_on=["retrieve"]),
            ],
            entry_strategy="rule",
            dispatch_trace={"source": "route_table"},
        )
        plan_patch = ActionPlanPatch(
            decision_type="replan",
            operations=[
                ActionPlanPatchOperation(
                    op_type="request_replan",
                    reason="sector evidence should be replenished before retrieval",
                    metadata={"trigger": "collect_completed"},
                )
            ],
            notes=["request_action_plan_replan"],
            policy_source="rule",
            termination_reason="sector evidence should be replenished before retrieval",
        )
        request_entry = _blackboard_entry(
            kind="source_record",
            payload={
                "title": "sector_research input query",
                "content": "innovation drug sector",
                "source_type": "user_query",
            },
        )
        sector_snapshot_entry = _blackboard_entry(
            kind="verified_snapshot",
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

        class _Blackboard:
            def __init__(self, entries) -> None:
                self._entries = entries

            def list_entries(self, *, thread_id=None):
                return list(self._entries)

        class _Thread:
            def __init__(self, thread_id: str) -> None:
                self.thread_id = thread_id

        class _Context:
            def __init__(self, entries) -> None:
                self.thread = _Thread("thread_01")
                self.blackboard = _Blackboard(entries)

        planner.bind_runtime(
            context=_Context([request_entry, sector_snapshot_entry]),
            support=None,
            request_id="req_1",
            intent="sector_research",
        )
        try:
            replanned = planner.replan_action_plan(
                intent="sector_research",
                request=RequestEnvelope(user_query="innovation drug sector"),
                current_plan=current_plan,
                plan_patch=plan_patch,
            )
        finally:
            planner.clear_runtime()

        self.assertEqual(
            [action.node_id for action in replanned.actions],
            [
                "collect",
                "collect_replenish_sector_policy_signals_regulatory_signals_relative_strength",
                "retrieve",
                "report",
            ],
        )
        self.assertEqual(
            replanned.actions[2].depends_on,
            ["collect_replenish_sector_policy_signals_regulatory_signals_relative_strength"],
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
        self.assertEqual(plan.actions[1].depends_on, ["collect"])
        self.assertEqual(plan.actions[2].depends_on, ["screen"])
        self.assertEqual(plan.actions[3].depends_on, ["retrieve"])

    def test_review_thesis_initial_plan_is_dynamic_and_excludes_review(self) -> None:
        planner = RuntimePlanner()

        plan = planner.build_initial_plan(
            intent="review_thesis",
            request=RequestEnvelope(user_query="review thesis for innovation drug"),
        )

        self.assertEqual(plan.strategy, "dynamic")
        self.assertEqual([action.node_id for action in plan.actions], ["collect", "retrieve", "report"])
        self.assertEqual(plan.actions[1].depends_on, ["collect"])
        self.assertEqual(plan.actions[2].depends_on, ["retrieve"])

    def test_distill_plan_uses_explicit_action_dag(self) -> None:
        planner = RuntimePlanner()

        plan = planner.build_initial_plan(
            intent="distill_kol_logic",
            request=RequestEnvelope(user_query="distill recent kol logic"),
        )

        self.assertEqual([action.node_id for action in plan.actions], ["collect", "distill", "critic", "retrieve", "report"])
        self.assertEqual(plan.actions[1].depends_on, ["collect"])
        self.assertEqual(plan.actions[2].depends_on, ["distill"])
        self.assertEqual(plan.actions[3].depends_on, ["critic"])
        self.assertEqual(plan.actions[4].depends_on, ["retrieve"])

    def test_supervisor_inserts_screen_review_after_screening_when_candidates_exist(self) -> None:
        planner = RuntimePlanner()
        supervisor = RuntimeSupervisor()
        plan = planner.build_initial_plan(
            intent="stock_screen",
            request=RequestEnvelope(user_query="recent stock ideas"),
        )
        pending_actions = [action for action in plan.actions if action.node_id in {"retrieve", "report"}]
        scorecard_entry = _blackboard_entry(
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
        self.assertIsNotNone(decision.plan_patch)
        self.assertEqual(
            [operation.op_type for operation in list(decision.plan_patch.operations if decision.plan_patch else [])],
            ["request_replan"],
        )

    def test_supervisor_inserts_screen_enrich_before_review_and_retrieval_when_scorecard_pending(self) -> None:
        planner = RuntimePlanner()
        supervisor = RuntimeSupervisor()
        plan = planner.build_initial_plan(
            intent="stock_screen",
            request=RequestEnvelope(user_query="recent stock ideas"),
        )
        pending_actions = [action for action in plan.actions if action.node_id in {"retrieve", "report"}]
        scorecard_entry = _blackboard_entry(
            kind="source_record",
            agent="screener",
            phase="screen",
            payload={
                "title": "screening_scorecard",
                "content": "{}",
                "source_type": "screening_scorecard",
                "scorecard": {
                    "top_candidate_count": 2,
                    "watchlist_candidate_count": 1,
                    "enrichment_target_count": 2,
                    "enrichment_pending": True,
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
        self.assertEqual(decision.next_actions[0].node_id, "screen_enrich")
        self.assertEqual(decision.next_actions[0].agent, "screener")
        retrieve_action = next(action for action in plan.actions if action.node_id == "retrieve")
        self.assertEqual(retrieve_action.depends_on, ["screen"])
        self.assertIsNotNone(decision.plan_patch)
        self.assertEqual(
            [operation.op_type for operation in list(decision.plan_patch.operations if decision.plan_patch else [])],
            ["request_replan"],
        )
        self.assertEqual(
            str((decision.plan_patch.operations[0].metadata or {}).get("include_screen_review") or ""),
            "true",
        )

    def test_screen_review_action_depends_on_screen_enrich_when_present(self) -> None:
        planner = RuntimePlanner()
        supervisor = RuntimeSupervisor()
        plan = planner.build_initial_plan(
            intent="stock_screen",
            request=RequestEnvelope(user_query="recent stock ideas"),
        )
        plan.actions.append(
            ActionSpec(
                node_id="screen_enrich",
                phase="screen",
                agent="screener",
                depends_on=["screen"],
            )
        )
        pending_actions = [action for action in plan.actions if action.node_id in {"retrieve", "report"}]
        scorecard_entry = _blackboard_entry(
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
                    "enrichment_target_count": 0,
                    "enrichment_pending": False,
                },
            },
        )

        decision = supervisor.decide(
            plan=plan,
            completed_actions=["collect", "screen", "screen_enrich"],
            pending_actions=pending_actions,
            blackboard_entries=[scorecard_entry],
        )

        self.assertEqual(decision.decision_type, "continue")
        self.assertEqual(decision.next_actions[0].node_id, "screen_review")
        self.assertEqual(decision.next_actions[0].depends_on, ["screen_enrich"])
        self.assertIsNotNone(decision.plan_patch)
        self.assertEqual(
            [operation.op_type for operation in list(decision.plan_patch.operations if decision.plan_patch else [])],
            ["request_replan"],
        )

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
        scorecard_entry = _blackboard_entry(
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

    def test_supervisor_inserts_review_action_when_review_request_and_snapshot_exist(self) -> None:
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
        review_request_entry = _blackboard_entry(
            kind="source_record",
            payload={
                "title": "review_thesis input request",
                "content": "review thesis for innovation drug",
                "source_type": "review_request",
                "review_target_run_id": "01TARGET",
                "review_source_entries": [{"kind": "report_fragment", "payload": {"conclusion": "test"}}],
            },
        )
        sector_snapshot_entry = _blackboard_entry(
            kind="verified_snapshot",
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
        self.assertIsNotNone(decision.plan_patch)
        self.assertEqual(
            [operation.op_type for operation in list(decision.plan_patch.operations if decision.plan_patch else [])],
            ["request_replan"],
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
        request_entry = _blackboard_entry(
            kind="source_record",
            payload={
                "title": "stock_check input query",
                "content": "check 000001",
                "source_type": "user_query",
                "symbol": "000001",
            },
        )
        market_snapshot_entry = _blackboard_entry(
            kind="verified_snapshot",
            payload={
                "snapshot_type": "market",
                "symbol": "000001",
                "snapshot_date": "2026-06-28",
                "facts": {},
                "source": {"skill": "market_snapshot_skill"},
            },
        )
        fundamental_snapshot_entry = _blackboard_entry(
            kind="verified_snapshot",
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
        self.assertIsNotNone(decision.plan_patch)
        self.assertEqual(
            [operation.op_type for operation in list(decision.plan_patch.operations if decision.plan_patch else [])],
            ["request_replan"],
        )

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
        request_entry = _blackboard_entry(
            kind="source_record",
            payload={
                "title": "sector_research input query",
                "content": "innovation drug sector",
                "source_type": "user_query",
            },
        )
        sector_snapshot_entry = _blackboard_entry(
            kind="verified_snapshot",
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
        self.assertIsNotNone(decision.plan_patch)
        self.assertEqual(
            [operation.op_type for operation in list(decision.plan_patch.operations if decision.plan_patch else [])],
            ["request_replan"],
        )


if __name__ == "__main__":
    unittest.main()
