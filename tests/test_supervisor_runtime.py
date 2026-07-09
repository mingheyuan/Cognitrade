from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.runtime.local_runtime import bind_local_runtime
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.runtime.supervision import RuntimeSupervisor
from research_system.runtime.supervision._action_phase_controller import ActionPhaseController
from research_system.schemas.planning import ActionPlan, ActionSpec
from research_system.schemas.protocol import RequestEnvelope
from research_system.runtime.supervision.review_thesis_support import (
    SupervisorReviewThesisSupport,
)
from research_system.runtime.supervision.sector_research_support import (
    SupervisorSectorResearchSupport,
)
from research_system.runtime.supervision.stock_check_support import SupervisorStockCheckSupport
from research_system.runtime.supervision.stock_screen_support import (
    SupervisorStockScreenSupport,
)
from research_system.schemas.runtime import BlackboardEntry


class _ShadowProviderStub:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        schema = kwargs["schema"]
        return schema.model_validate(
            {
                "selected_action_node_id": "screen_review",
                "request_replan": True,
                "reason": "screen review should run before retrieval",
                "confidence": 0.74,
                "notes": ["shadow_selected_screen_review"],
            }
        )


class _HybridProviderStub:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        schema = kwargs["schema"]
        return schema.model_validate(
            {
                "selected_action_node_id": "review",
                "request_replan": True,
                "reason": "review should run before retrieval in this frontier",
                "confidence": 0.91,
                "notes": ["hybrid_selected_review"],
            }
        )


class _LLMReplanSuppressProviderStub:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        schema = kwargs["schema"]
        return schema.model_validate(
            {
                "selected_action_node_id": "retrieve",
                "request_replan": False,
                "reason": "retrieval can proceed without inserting review",
                "confidence": 0.93,
                "notes": ["llm_suppress_screen_review_replan"],
            }
        )


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


class SupervisorRuntimeTests(unittest.TestCase):
    def test_supervisor_wires_intent_specific_supports(self) -> None:
        supervisor = RuntimeSupervisor()

        self.assertIsInstance(supervisor.stock_screen, SupervisorStockScreenSupport)
        self.assertIsInstance(supervisor.stock_check, SupervisorStockCheckSupport)
        self.assertIsInstance(supervisor.sector_research, SupervisorSectorResearchSupport)
        self.assertIsInstance(supervisor.review_thesis, SupervisorReviewThesisSupport)
        self.assertIsInstance(supervisor.phase_controller, ActionPhaseController)

    def test_supervisor_shadow_policy_records_suggestion_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            settings.supervisor_shadow_llm_enabled = True
            settings.max_llm_calls_per_turn = 2
            provider = _ShadowProviderStub()
            supervisor = RuntimeSupervisor(llm_provider=provider, settings=settings)
            request = RequestEnvelope(user_query="screen growth stocks", request_id="req_shadow")
            plan = ActionPlan(
                intent="stock_screen",
                actions=[
                    ActionSpec(node_id="collect", phase="collect", agent="collector"),
                    ActionSpec(node_id="screen", phase="screen", agent="screener", depends_on=["collect"]),
                    ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever", depends_on=["screen"]),
                    ActionSpec(node_id="report", phase="report", agent="reporter", depends_on=["retrieve"]),
                ],
            )
            scorecard_entry = _blackboard_entry(
                kind="source_record",
                agent="screener",
                phase="screen",
                request_id="req_shadow",
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

            with bind_local_runtime(
                supervisor,
                request=request,
                intent="stock_screen",
                phase="supervision",
                runs_root=settings.runs_root,
                max_llm_calls_per_turn=2,
            ) as runtime:
                decision = supervisor.decide(
                    plan=plan,
                    completed_actions=["collect", "screen"],
                    pending_actions=[plan.actions[2], plan.actions[3]],
                    blackboard_entries=[scorecard_entry],
                )

                self.assertEqual(decision.next_actions[0].node_id, "screen_review")
                self.assertEqual(provider.calls, 1)
                self.assertEqual(len(runtime.context.action_phase_shadow_suggestions), 1)
                suggestion = runtime.context.action_phase_shadow_suggestions[0]
                self.assertEqual(suggestion["selected_action_node_id"], "screen_review")
                self.assertTrue(suggestion["request_replan"])
                self.assertEqual(suggestion["actual_action_node_id"], "screen_review")
                self.assertTrue(suggestion["action_match"])
                self.assertTrue(suggestion["replan_match"])
                self.assertEqual(len(runtime.context.runtime_control_stage_decisions), 1)
                self.assertEqual(
                    runtime.context.runtime_control_stage_decisions[0]["event"],
                    "action_phase_decided",
                )
                self.assertEqual(
                    runtime.context.runtime_control_stage_decisions[0]["final_action_node_id"],
                    "screen_review",
                )

    def test_supervisor_hybrid_policy_can_override_rule_frontier_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            settings.supervisor_shadow_llm_enabled = True
            settings.supervisor_hybrid_llm_enabled = True
            settings.supervisor_hybrid_llm_min_confidence = 0.8
            settings.max_llm_calls_per_turn = 2
            provider = _HybridProviderStub()
            supervisor = RuntimeSupervisor(llm_provider=provider, settings=settings)
            request = RequestEnvelope(user_query="decide next action", request_id="req_hybrid")
            plan = ActionPlan(
                intent="methodology_evolution",
                actions=[
                    ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever"),
                    ActionSpec(node_id="review", phase="review", agent="reviewer"),
                ],
            )

            with bind_local_runtime(
                supervisor,
                request=request,
                intent="methodology_evolution",
                phase="supervision",
                runs_root=settings.runs_root,
                max_llm_calls_per_turn=2,
            ) as runtime:
                decision = supervisor.decide(
                    plan=plan,
                    completed_actions=[],
                    pending_actions=[plan.actions[0], plan.actions[1]],
                    blackboard_entries=[],
                )

                self.assertEqual(provider.calls, 1)
                self.assertEqual(decision.next_actions[0].node_id, "review")
                self.assertIsNotNone(decision.plan_patch)
                self.assertEqual(decision.plan_patch.policy_source, "hybrid")
                self.assertEqual(len(runtime.context.action_phase_shadow_suggestions), 1)
                self.assertEqual(len(runtime.context.action_phase_hybrid_decisions), 1)
                hybrid = runtime.context.action_phase_hybrid_decisions[0]
                self.assertEqual(hybrid["rule_action_node_id"], "retrieve")
                self.assertEqual(hybrid["final_action_node_id"], "review")
                self.assertTrue(hybrid["override_action"])
                self.assertTrue(hybrid["override_replan"])

    def test_supervisor_hybrid_policy_can_run_without_shadow_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            settings.supervisor_shadow_llm_enabled = False
            settings.supervisor_hybrid_llm_enabled = True
            settings.supervisor_hybrid_llm_min_confidence = 0.8
            settings.max_llm_calls_per_turn = 2
            provider = _HybridProviderStub()
            supervisor = RuntimeSupervisor(llm_provider=provider, settings=settings)
            request = RequestEnvelope(user_query="decide next action", request_id="req_hybrid_only")
            plan = ActionPlan(
                intent="methodology_evolution",
                actions=[
                    ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever"),
                    ActionSpec(node_id="review", phase="review", agent="reviewer"),
                ],
            )

            with bind_local_runtime(
                supervisor,
                request=request,
                intent="methodology_evolution",
                phase="supervision",
                runs_root=settings.runs_root,
                max_llm_calls_per_turn=2,
            ) as runtime:
                decision = supervisor.decide(
                    plan=plan,
                    completed_actions=[],
                    pending_actions=[plan.actions[0], plan.actions[1]],
                    blackboard_entries=[],
                )

                self.assertEqual(provider.calls, 1)
                self.assertEqual(decision.next_actions[0].node_id, "review")
                self.assertEqual(len(runtime.context.action_phase_shadow_suggestions), 1)
                self.assertEqual(len(runtime.context.action_phase_hybrid_decisions), 1)

    def test_supervisor_llm_policy_can_override_rule_frontier_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            settings.supervisor_shadow_llm_enabled = False
            settings.supervisor_llm_policy_enabled = True
            settings.supervisor_llm_policy_min_confidence = 0.8
            settings.max_llm_calls_per_turn = 2
            provider = _HybridProviderStub()
            supervisor = RuntimeSupervisor(llm_provider=provider, settings=settings)
            request = RequestEnvelope(user_query="decide next action", request_id="req_llm")
            plan = ActionPlan(
                intent="methodology_evolution",
                actions=[
                    ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever"),
                    ActionSpec(node_id="review", phase="review", agent="reviewer"),
                ],
            )

            with bind_local_runtime(
                supervisor,
                request=request,
                intent="methodology_evolution",
                phase="supervision",
                runs_root=settings.runs_root,
                max_llm_calls_per_turn=2,
            ) as runtime:
                decision = supervisor.decide(
                    plan=plan,
                    completed_actions=[],
                    pending_actions=[plan.actions[0], plan.actions[1]],
                    blackboard_entries=[],
                )

                self.assertEqual(provider.calls, 1)
                self.assertEqual(decision.next_actions[0].node_id, "review")
                self.assertIsNotNone(decision.plan_patch)
                self.assertEqual(decision.plan_patch.policy_source, "llm")
                self.assertEqual(len(runtime.context.action_phase_shadow_suggestions), 1)
                self.assertEqual(len(runtime.context.action_phase_llm_decisions), 1)
                self.assertEqual(len(runtime.context.action_phase_hybrid_decisions), 0)
                llm_row = runtime.context.action_phase_llm_decisions[0]
                self.assertEqual(llm_row["rule_action_node_id"], "retrieve")
                self.assertEqual(llm_row["final_action_node_id"], "review")
                self.assertTrue(llm_row["override_action"])
                self.assertTrue(llm_row["override_replan"])

    def test_supervisor_llm_policy_can_suppress_rule_replan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            settings.supervisor_shadow_llm_enabled = False
            settings.supervisor_llm_policy_enabled = True
            settings.supervisor_llm_policy_min_confidence = 0.8
            settings.max_llm_calls_per_turn = 2
            provider = _LLMReplanSuppressProviderStub()
            supervisor = RuntimeSupervisor(llm_provider=provider, settings=settings)
            request = RequestEnvelope(user_query="screen growth stocks", request_id="req_llm_suppress")
            plan = ActionPlan(
                intent="stock_screen",
                actions=[
                    ActionSpec(node_id="collect", phase="collect", agent="collector"),
                    ActionSpec(node_id="screen", phase="screen", agent="screener", depends_on=["collect"]),
                    ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever", depends_on=["screen"]),
                    ActionSpec(node_id="report", phase="report", agent="reporter", depends_on=["retrieve"]),
                ],
            )
            scorecard_entry = _blackboard_entry(
                kind="source_record",
                agent="screener",
                phase="screen",
                request_id="req_llm_suppress",
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

            with bind_local_runtime(
                supervisor,
                request=request,
                intent="stock_screen",
                phase="supervision",
                runs_root=settings.runs_root,
                max_llm_calls_per_turn=2,
            ) as runtime:
                decision = supervisor.decide(
                    plan=plan,
                    completed_actions=["collect", "screen"],
                    pending_actions=[plan.actions[2], plan.actions[3]],
                    blackboard_entries=[scorecard_entry],
                )

                self.assertEqual(provider.calls, 1)
                self.assertEqual(decision.next_actions[0].node_id, "retrieve")
                self.assertIsNone(decision.plan_patch)
                self.assertEqual(len(runtime.context.action_phase_shadow_suggestions), 1)
                self.assertEqual(len(runtime.context.action_phase_llm_decisions), 1)
                llm_row = runtime.context.action_phase_llm_decisions[0]
                self.assertEqual(llm_row["rule_action_node_id"], "screen_review")
                self.assertEqual(llm_row["final_action_node_id"], "retrieve")
                self.assertFalse(llm_row["final_request_replan"])

    def test_supervisor_shadow_policy_respects_runtime_execution_policy_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            settings.supervisor_shadow_llm_enabled = True
            settings.max_llm_calls_per_turn = 3
            provider = _ShadowProviderStub()
            supervisor = RuntimeSupervisor(llm_provider=provider, settings=settings)
            request = RequestEnvelope(user_query="screen growth stocks", request_id="req_runtime_budget")
            plan = ActionPlan(
                intent="stock_screen",
                actions=[
                    ActionSpec(node_id="collect", phase="collect", agent="collector"),
                    ActionSpec(node_id="screen", phase="screen", agent="screener", depends_on=["collect"]),
                    ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever", depends_on=["screen"]),
                    ActionSpec(node_id="report", phase="report", agent="reporter", depends_on=["retrieve"]),
                ],
            )
            scorecard_entry = _blackboard_entry(
                kind="source_record",
                agent="screener",
                phase="screen",
                request_id="req_runtime_budget",
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

            with bind_local_runtime(
                supervisor,
                request=request,
                intent="stock_screen",
                phase="supervision",
                runs_root=settings.runs_root,
                max_llm_calls_per_turn=1,
            ) as runtime:
                decision = supervisor.decide(
                    plan=plan,
                    completed_actions=["collect", "screen"],
                    pending_actions=[plan.actions[2], plan.actions[3]],
                    blackboard_entries=[scorecard_entry],
                )

                self.assertEqual(decision.next_actions[0].node_id, "screen_review")
                self.assertEqual(provider.calls, 0)
                self.assertEqual(len(runtime.context.action_phase_shadow_suggestions), 0)
                self.assertEqual(len(runtime.context.action_phase_hybrid_decisions), 0)


if __name__ == "__main__":
    unittest.main()
