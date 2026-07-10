from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from research_system.runtime.execution.handlers.dataflow.data import run_retriever
from research_system.runtime.execution.handlers.distillation.distillation import run_critic, run_distiller
from research_system.runtime.execution.handlers.reporting import run_reporter
from research_system.runtime.orchestration.orchestrator_research_debug import OrchestratorDebugSupport
from research_system.runtime.orchestration.orchestrator_context import build_runtime_context
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.runtime.storage.runs.run_store import RunStore
from research_system.schemas.planning import ActionPlan, ActionSpec
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import RunStateSnapshot


def _build_runtime():
    temp_dir = tempfile.TemporaryDirectory()
    settings = Settings.from_env(Path(temp_dir.name))
    ensure_workspace_layout(settings)
    run_store = RunStore(settings.runs_root)
    request = RequestEnvelope(user_query="test runtime execution decisions")
    run_id, paths, snapshot = run_store.create_run(request, "stock_check")
    context = build_runtime_context(
        run_id=run_id,
        paths=paths,
        state=snapshot,
        run_timeout_seconds=settings.run_timeout_seconds,
        max_llm_calls_per_turn=settings.max_llm_calls_per_turn,
        max_subagent_calls_per_turn=settings.max_subagent_calls_per_turn,
        max_child_threads_per_turn=settings.max_child_threads_per_turn,
    )
    return temp_dir, settings, context, request


class RuntimeExecutionDecisionTests(unittest.TestCase):
    def test_run_reporter_passes_runtime_execution_mode(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            reporter = Mock()
            reporter.llm_provider = object()
            reporter.run.return_value = {"conclusion": "ok", "evidence": []}
            support = Mock()
            support.resolve_llm_execution.return_value = Mock(can_use_llm=False, degrade_reason="turn_llm_budget_exhausted")
            support.record_payload_degradation.return_value = True
            support.build_debug_payload.return_value = {}
            host = Mock(reporter=reporter)

            run_reporter(
                host,
                support,
                context=context,
                request=request,
                plan=ActionPlan(intent="stock_check", actions=[]),
                intent="stock_check",
                action=ActionSpec(
                    action_id="a1",
                    node_id="n1",
                    phase="report",
                    agent="reporter",
                    objective="report",
                    inputs={},
                ),
                phase="report",
            )

            self.assertEqual(reporter.run.call_args.kwargs["execution_mode"], "fallback")
            self.assertEqual(reporter.run.call_args.kwargs["runtime_degraded_note"], "turn_llm_budget_exhausted")
        finally:
            temp_dir.cleanup()

    def test_run_distiller_passes_runtime_execution_mode(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            distiller = Mock()
            distiller.llm_provider = object()
            distiller.run.return_value = {
                "summary": {"processing_notes": [], "major_risks": []},
                "logic_blocks": [],
                "distilled_dimensions": [],
                "subagent_plans": [{"phase": "distill"}],
                "subagent_plan_patches": [{"patch_id": "p1"}],
            }
            support = Mock()
            support.resolve_llm_execution.return_value = Mock(can_use_llm=False, degrade_reason="turn_llm_budget_exhausted")
            support.record_payload_degradation.return_value = True
            host = Mock(distiller=distiller)

            run_distiller(
                host,
                support,
                context=context,
                request=request,
                plan=ActionPlan(intent="distill_kol_logic", actions=[]),
                intent="distill_kol_logic",
                action=ActionSpec(
                    action_id="a1",
                    node_id="n1",
                    phase="distill",
                    agent="distiller",
                    objective="distill",
                    inputs={},
                ),
                phase="distill",
            )

            self.assertEqual(distiller.run.call_args.kwargs["execution_mode"], "fallback")
            self.assertEqual(distiller.run.call_args.kwargs["runtime_degraded_reason"], "turn_llm_budget_exhausted")
            trace_payload = support.extend_subagent_trace.call_args.kwargs["payload"]
            self.assertEqual(trace_payload["subagent_plans"], [{"phase": "distill"}])
            self.assertEqual(trace_payload["subagent_plan_patches"], [{"patch_id": "p1"}])
        finally:
            temp_dir.cleanup()

    def test_run_retriever_passes_runtime_decisions(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            retriever = Mock()
            retriever.llm_provider = object()
            retriever.run.return_value = {
                "evidences": [],
                "retrieval_summary": "fallback",
            }
            support = Mock()
            support.resolve_llm_execution.return_value = Mock(can_use_llm=False, degrade_reason="turn_llm_budget_exhausted")
            support.record_payload_degradation.return_value = True
            host = Mock(retriever=retriever)

            run_retriever(
                host,
                support,
                context=context,
                request=request,
                plan=ActionPlan(intent="stock_check", actions=[]),
                intent="stock_check",
                action=ActionSpec(
                    action_id="a1",
                    node_id="n1",
                    phase="retrieve",
                    agent="retriever",
                    objective="retrieve",
                    inputs={},
                ),
                phase="retrieve",
            )

            self.assertFalse(retriever.run.call_args.kwargs["allow_llm_decision"])
            self.assertEqual(retriever.run.call_args.kwargs["runtime_degraded_reason"], "turn_llm_budget_exhausted")
            self.assertEqual(retriever.run.call_args.kwargs["search_mode"], "blackboard_only")
        finally:
            temp_dir.cleanup()

    def test_run_critic_passes_subagent_plans_to_trace_support(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            critic = Mock()
            critic.run.return_value = {
                "summary": {"review_required": False, "finding_count": 0, "notes": []},
                "scorecard": {"finding_types": []},
                "findings": [],
                "subagent_plans": [{"phase": "review"}],
                "subagent_plan_patches": [],
            }
            support = Mock()
            host = Mock(critic=critic)

            run_critic(
                host,
                support,
                context=context,
                request=request,
                plan=ActionPlan(intent="distill_kol_logic", actions=[]),
                intent="distill_kol_logic",
                action=ActionSpec(
                    action_id="a1",
                    node_id="n1",
                    phase="review",
                    agent="critic",
                    objective="critic",
                    inputs={},
                ),
                phase="review",
            )

            trace_payload = support.extend_subagent_trace.call_args.kwargs["payload"]
            self.assertEqual(trace_payload["subagent_plans"], [{"phase": "review"}])
            self.assertEqual(trace_payload["subagent_plan_patches"], [])
        finally:
            temp_dir.cleanup()

    def test_debug_payload_includes_action_phase_shadow_suggestions(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            context.action_phase_shadow_suggestions.append(
                {
                    "event": "action_phase_shadow_suggested",
                    "selected_action_node_id": "screen_review",
                    "request_replan": True,
                }
            )

            payload = OrchestratorDebugSupport().build_debug_payload(
                context=context,
                request=request,
                plan=ActionPlan(intent="stock_screen", actions=[]),
                plan_intent="stock_screen",
                retrieval_summary=None,
            )

            self.assertEqual(
                payload["action_phase_shadow_suggestions"],
                [{"event": "action_phase_shadow_suggested", "selected_action_node_id": "screen_review", "request_replan": True}],
            )
        finally:
            temp_dir.cleanup()

    def test_debug_payload_includes_subagent_phase_shadow_suggestions(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            context.subagent_phase_shadow_suggestions.append(
                {
                    "event": "subagent_phase_shadow_suggested",
                    "dag_id": "phase_two",
                    "selected_node_ids": ["retrieval_decider"],
                    "request_replan": True,
                }
            )

            payload = OrchestratorDebugSupport().build_debug_payload(
                context=context,
                request=request,
                plan=ActionPlan(intent="stock_check", actions=[]),
                plan_intent="stock_check",
                retrieval_summary=None,
            )

            self.assertEqual(
                payload["subagent_phase_shadow_suggestions"],
                [
                    {
                        "event": "subagent_phase_shadow_suggested",
                        "dag_id": "phase_two",
                        "selected_node_ids": ["retrieval_decider"],
                        "request_replan": True,
                    }
                ],
            )
        finally:
            temp_dir.cleanup()

    def test_debug_payload_includes_subagent_capability_gap_requests(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            context.subagent_capability_gaps.append(
                {
                    "event": "subagent_capability_gap_detected",
                    "stage": "review",
                    "intent": "review_thesis",
                    "capability": "peer_review",
                    "reason": "no catalog entry for stage='review' intent='review_thesis' capability='peer_review'",
                }
            )
            context.subagent_tool_discovery_requests.append(
                {
                    "event": "subagent_tool_discovery_requested",
                    "stage": "review",
                    "intent": "review_thesis",
                    "capability": "peer_review",
                    "search_query": "review_thesis review peer_review",
                }
            )

            payload = OrchestratorDebugSupport().build_debug_payload(
                context=context,
                request=request,
                plan=ActionPlan(intent="review_thesis", actions=[]),
                plan_intent="review_thesis",
                retrieval_summary=None,
            )

            self.assertEqual(
                payload["subagent_capability_gaps"],
                [
                    {
                        "event": "subagent_capability_gap_detected",
                        "stage": "review",
                        "intent": "review_thesis",
                        "capability": "peer_review",
                        "reason": "no catalog entry for stage='review' intent='review_thesis' capability='peer_review'",
                    }
                ],
            )
            self.assertEqual(
                payload["subagent_tool_discovery_requests"],
                [
                    {
                        "event": "subagent_tool_discovery_requested",
                        "stage": "review",
                        "intent": "review_thesis",
                        "capability": "peer_review",
                        "search_query": "review_thesis review peer_review",
                    }
                ],
            )
        finally:
            temp_dir.cleanup()

    def test_debug_payload_includes_subagent_catalog_stage_decisions(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            context.subagent_catalog_stage_decisions.append(
                {
                    "event": "subagent_catalog_selection_decided",
                    "control_domain": "subagent",
                    "control_stage": "catalog_selection",
                    "decision_stage": "catalog_selection",
                    "stage": "collect",
                    "capability": "market_signal_extension",
                    "decision_type": "escalate_capability_gap",
                    "selection_source": None,
                    "policy_source": "rule",
                }
            )

            payload = OrchestratorDebugSupport().build_debug_payload(
                context=context,
                request=request,
                plan=ActionPlan(intent="stock_check", actions=[]),
                plan_intent="stock_check",
                retrieval_summary=None,
            )

            self.assertEqual(
                payload["subagent_catalog_stage_decisions"],
                [
                    {
                        "event": "subagent_catalog_selection_decided",
                        "control_domain": "subagent",
                        "control_stage": "catalog_selection",
                        "decision_stage": "catalog_selection",
                        "stage": "collect",
                        "capability": "market_signal_extension",
                        "decision_type": "escalate_capability_gap",
                        "selection_source": None,
                        "policy_source": "rule",
                    }
                ],
            )
        finally:
            temp_dir.cleanup()

    def test_debug_payload_includes_subagent_control_stage_decisions(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            context.subagent_control_stage_decisions.append(
                {
                    "event": "subagent_phase_execution_decided",
                    "control_domain": "subagent",
                    "control_stage": "phase_patch_cycle",
                    "decision_stage": "phase_execution",
                    "phase": "review",
                    "dag_id": "review_execution",
                    "decision_type": "keep_current",
                    "final_plan_variant": "current",
                }
            )

            payload = OrchestratorDebugSupport().build_debug_payload(
                context=context,
                request=request,
                plan=ActionPlan(intent="review_thesis", actions=[]),
                plan_intent="review_thesis",
                retrieval_summary=None,
            )

            self.assertEqual(
                payload["subagent_control_stage_decisions"],
                [
                    {
                        "event": "subagent_phase_execution_decided",
                        "control_domain": "subagent",
                        "control_stage": "phase_patch_cycle",
                        "decision_stage": "phase_execution",
                        "phase": "review",
                        "dag_id": "review_execution",
                        "decision_type": "keep_current",
                        "final_plan_variant": "current",
                    }
                ],
            )
        finally:
            temp_dir.cleanup()

    def test_debug_payload_includes_runtime_control_stage_decisions(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            context.runtime_control_stage_decisions.append(
                {
                    "event": "action_phase_decided",
                    "control_domain": "action",
                    "control_stage": "action_phase",
                    "decision_stage": "action_phase",
                    "intent": "stock_screen",
                    "decision_type": "continue",
                    "final_action_node_id": "screen_review",
                }
            )

            payload = OrchestratorDebugSupport().build_debug_payload(
                context=context,
                request=request,
                plan=ActionPlan(intent="stock_screen", actions=[]),
                plan_intent="stock_screen",
                retrieval_summary=None,
            )

            self.assertEqual(
                payload["runtime_control_stage_decisions"],
                [
                    {
                        "event": "action_phase_decided",
                        "control_domain": "action",
                        "control_stage": "action_phase",
                        "decision_stage": "action_phase",
                        "intent": "stock_screen",
                        "decision_type": "continue",
                        "final_action_node_id": "screen_review",
                    }
                ],
            )
        finally:
            temp_dir.cleanup()

    def test_debug_payload_includes_subagent_discovery_stage_decisions(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            context.subagent_discovery_stage_decisions.append(
                {
                    "event": "subagent_tool_discovery_protocol_decided",
                    "control_domain": "subagent",
                    "control_stage": "protocol_selection",
                    "decision_stage": "protocol_selection",
                    "stage": "collect",
                    "capability": "market_signal_extension",
                    "selected_protocols": ["skill", "mcp", "openapi", "http_json"],
                    "policy_source": "rule",
                }
            )

            payload = OrchestratorDebugSupport().build_debug_payload(
                context=context,
                request=request,
                plan=ActionPlan(intent="stock_check", actions=[]),
                plan_intent="stock_check",
                retrieval_summary=None,
            )

            self.assertEqual(
                payload["subagent_discovery_stage_decisions"],
                [
                    {
                        "event": "subagent_tool_discovery_protocol_decided",
                        "control_domain": "subagent",
                        "control_stage": "protocol_selection",
                        "decision_stage": "protocol_selection",
                        "stage": "collect",
                        "capability": "market_signal_extension",
                        "selected_protocols": ["skill", "mcp", "openapi", "http_json"],
                        "policy_source": "rule",
                    }
                ],
            )
        finally:
            temp_dir.cleanup()

    def test_debug_payload_includes_subagent_discovery_shadow_suggestions(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            context.subagent_discovery_shadow_suggestions.append(
                {
                    "event": "subagent_discovery_shadow_suggested",
                    "stage": "review",
                    "capability": "peer_review",
                    "selected_protocols": ["skill", "mcp", "openapi"],
                }
            )

            payload = OrchestratorDebugSupport().build_debug_payload(
                context=context,
                request=request,
                plan=ActionPlan(intent="review_thesis", actions=[]),
                plan_intent="review_thesis",
                retrieval_summary=None,
            )

            self.assertEqual(
                payload["subagent_discovery_shadow_suggestions"],
                [
                    {
                        "event": "subagent_discovery_shadow_suggested",
                        "stage": "review",
                        "capability": "peer_review",
                        "selected_protocols": ["skill", "mcp", "openapi"],
                    }
                ],
            )
        finally:
            temp_dir.cleanup()

    def test_debug_payload_includes_subagent_discovery_llm_decisions(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            context.subagent_discovery_llm_decisions.append(
                {
                    "event": "subagent_discovery_llm_applied",
                    "stage": "review",
                    "capability": "peer_review",
                    "applied_protocols": ["mcp", "skill", "openapi"],
                    "override_rule_protocols": True,
                }
            )

            payload = OrchestratorDebugSupport().build_debug_payload(
                context=context,
                request=request,
                plan=ActionPlan(intent="review_thesis", actions=[]),
                plan_intent="review_thesis",
                retrieval_summary=None,
            )

            self.assertEqual(
                payload["subagent_discovery_llm_decisions"],
                [
                    {
                        "event": "subagent_discovery_llm_applied",
                        "stage": "review",
                        "capability": "peer_review",
                        "applied_protocols": ["mcp", "skill", "openapi"],
                        "override_rule_protocols": True,
                    }
                ],
            )
        finally:
            temp_dir.cleanup()

    def test_debug_payload_includes_subagent_discovery_hybrid_decisions(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            context.subagent_discovery_hybrid_decisions.append(
                {
                    "event": "subagent_discovery_hybrid_applied",
                    "stage": "review",
                    "capability": "peer_review",
                    "applied_protocols": ["mcp", "skill", "openapi"],
                    "override_rule_protocols": True,
                }
            )

            payload = OrchestratorDebugSupport().build_debug_payload(
                context=context,
                request=request,
                plan=ActionPlan(intent="review_thesis", actions=[]),
                plan_intent="review_thesis",
                retrieval_summary=None,
            )

            self.assertEqual(
                payload["subagent_discovery_hybrid_decisions"],
                [
                    {
                        "event": "subagent_discovery_hybrid_applied",
                        "stage": "review",
                        "capability": "peer_review",
                        "applied_protocols": ["mcp", "skill", "openapi"],
                        "override_rule_protocols": True,
                    }
                ],
            )
        finally:
            temp_dir.cleanup()

    def test_debug_payload_includes_subagent_discovery_candidate_shadow_suggestions(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            context.subagent_discovery_candidate_shadow_suggestions.append(
                {
                    "event": "subagent_discovery_candidate_shadow_suggested",
                    "stage": "collect",
                    "capability": "market_signal_extension",
                    "selected_candidate_subagent_type": "candidate_collect_market_snapshot_skill_b",
                }
            )

            payload = OrchestratorDebugSupport().build_debug_payload(
                context=context,
                request=request,
                plan=ActionPlan(intent="stock_check", actions=[]),
                plan_intent="stock_check",
                retrieval_summary=None,
            )

            self.assertEqual(
                payload["subagent_discovery_candidate_shadow_suggestions"],
                [
                    {
                        "event": "subagent_discovery_candidate_shadow_suggested",
                        "stage": "collect",
                        "capability": "market_signal_extension",
                        "selected_candidate_subagent_type": "candidate_collect_market_snapshot_skill_b",
                    }
                ],
            )
        finally:
            temp_dir.cleanup()

    def test_debug_payload_includes_subagent_discovery_candidate_llm_decisions(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            context.subagent_discovery_candidate_llm_decisions.append(
                {
                    "event": "subagent_discovery_candidate_llm_applied",
                    "stage": "collect",
                    "capability": "market_signal_extension",
                    "applied_candidate_subagent_type": "candidate_collect_market_snapshot_skill_b",
                    "override_rule_candidate": True,
                }
            )

            payload = OrchestratorDebugSupport().build_debug_payload(
                context=context,
                request=request,
                plan=ActionPlan(intent="stock_check", actions=[]),
                plan_intent="stock_check",
                retrieval_summary=None,
            )

            self.assertEqual(
                payload["subagent_discovery_candidate_llm_decisions"],
                [
                    {
                        "event": "subagent_discovery_candidate_llm_applied",
                        "stage": "collect",
                        "capability": "market_signal_extension",
                        "applied_candidate_subagent_type": "candidate_collect_market_snapshot_skill_b",
                        "override_rule_candidate": True,
                    }
                ],
            )
        finally:
            temp_dir.cleanup()

    def test_debug_payload_includes_subagent_discovery_candidate_hybrid_decisions(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            context.subagent_discovery_candidate_hybrid_decisions.append(
                {
                    "event": "subagent_discovery_candidate_hybrid_applied",
                    "stage": "collect",
                    "capability": "market_signal_extension",
                    "applied_candidate_subagent_type": "candidate_collect_market_snapshot_skill_b",
                    "override_rule_candidate": True,
                }
            )

            payload = OrchestratorDebugSupport().build_debug_payload(
                context=context,
                request=request,
                plan=ActionPlan(intent="stock_check", actions=[]),
                plan_intent="stock_check",
                retrieval_summary=None,
            )

            self.assertEqual(
                payload["subagent_discovery_candidate_hybrid_decisions"],
                [
                    {
                        "event": "subagent_discovery_candidate_hybrid_applied",
                        "stage": "collect",
                        "capability": "market_signal_extension",
                        "applied_candidate_subagent_type": "candidate_collect_market_snapshot_skill_b",
                        "override_rule_candidate": True,
                    }
                ],
            )
        finally:
            temp_dir.cleanup()

    def test_debug_payload_includes_subagent_phase_hybrid_decisions(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            context.subagent_phase_hybrid_decisions.append(
                {
                    "event": "subagent_phase_hybrid_applied",
                    "dag_id": "phase_one",
                    "applied_plan_variant": "current",
                    "override_rule_variant": True,
                }
            )

            payload = OrchestratorDebugSupport().build_debug_payload(
                context=context,
                request=request,
                plan=ActionPlan(intent="stock_check", actions=[]),
                plan_intent="stock_check",
                retrieval_summary=None,
            )

            self.assertEqual(
                payload["subagent_phase_hybrid_decisions"],
                [
                    {
                        "event": "subagent_phase_hybrid_applied",
                        "dag_id": "phase_one",
                        "applied_plan_variant": "current",
                        "override_rule_variant": True,
                    }
                ],
            )
        finally:
            temp_dir.cleanup()

    def test_debug_payload_includes_subagent_phase_llm_decisions(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            context.subagent_phase_llm_decisions.append(
                {
                    "event": "subagent_phase_llm_applied",
                    "dag_id": "phase_two",
                    "applied_plan_variant": "fallback_retrieval_decider",
                    "override_rule_variant": True,
                }
            )

            payload = OrchestratorDebugSupport().build_debug_payload(
                context=context,
                request=request,
                plan=ActionPlan(intent="stock_check", actions=[]),
                plan_intent="stock_check",
                retrieval_summary=None,
            )

            self.assertEqual(
                payload["subagent_phase_llm_decisions"],
                [
                    {
                        "event": "subagent_phase_llm_applied",
                        "dag_id": "phase_two",
                        "applied_plan_variant": "fallback_retrieval_decider",
                        "override_rule_variant": True,
                    }
                ],
            )
        finally:
            temp_dir.cleanup()

    def test_debug_payload_includes_action_phase_hybrid_decisions(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            context.action_phase_hybrid_decisions.append(
                {
                    "event": "action_phase_hybrid_applied",
                    "final_action_node_id": "review",
                    "override_action": True,
                    "request_replan": True,
                }
            )

            payload = OrchestratorDebugSupport().build_debug_payload(
                context=context,
                request=request,
                plan=ActionPlan(intent="review_thesis", actions=[]),
                plan_intent="review_thesis",
                retrieval_summary=None,
            )

            self.assertEqual(
                payload["action_phase_hybrid_decisions"],
                [
                    {
                        "event": "action_phase_hybrid_applied",
                        "final_action_node_id": "review",
                        "override_action": True,
                        "request_replan": True,
                    }
                ],
            )
        finally:
            temp_dir.cleanup()

    def test_debug_payload_includes_action_phase_llm_decisions(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            context.action_phase_llm_decisions.append(
                {
                    "event": "action_phase_llm_applied",
                    "final_action_node_id": "review",
                    "override_action": True,
                    "request_replan": True,
                }
            )

            payload = OrchestratorDebugSupport().build_debug_payload(
                context=context,
                request=request,
                plan=ActionPlan(intent="review_thesis", actions=[]),
                plan_intent="review_thesis",
                retrieval_summary=None,
            )

            self.assertEqual(
                payload["action_phase_llm_decisions"],
                [
                    {
                        "event": "action_phase_llm_applied",
                        "final_action_node_id": "review",
                        "override_action": True,
                        "request_replan": True,
                    }
                ],
            )
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
