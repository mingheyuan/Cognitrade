from __future__ import annotations

from typing import Any

from research_system.runtime.orchestration._runtime_control_stage_decisions import (
    RuntimeControlTransitionStageDecision,
)
from research_system.runtime.orchestration._runtime_control_stage_recorder import (
    RuntimeControlStageRecorder,
)
from research_system.runtime.execution.core.runtime_transition_decision_source import (
    OrchestratorRuntimeTransitionDecisionSource,
)


class OrchestratorRuntimeControlController:
    def __init__(
        self,
        host: Any,
        *,
        apply_plan_patch,
        request_action_plan_replan,
        decision_source=None,
    ) -> None:
        self.host = host
        self.apply_plan_patch = apply_plan_patch
        self.request_action_plan_replan = request_action_plan_replan
        self.decision_source = decision_source or OrchestratorRuntimeTransitionDecisionSource()
        self._runtime_binding: dict[str, Any] | None = None
        self.control_stage_recorder = RuntimeControlStageRecorder(
            runtime_binding_getter=lambda: dict(self._runtime_binding or {}),
            emit_event=self._emit_runtime_event,
        )

    def handle_supervisor_plan_patch(
        self,
        *,
        context,
        request,
        plan,
        pending_actions,
        completed_node_ids,
        skipped_node_ids,
        plan_patch,
        next_actions,
    ) -> list[dict[str, Any]]:
        if plan_patch is None:
            return []
        stage_decision = self.decision_source.build_action_transition(
            plan=plan,
            plan_patch=plan_patch,
            source_domain="action_supervisor",
            source_stage="action_phase",
            source_action_node_id=next_actions[0].node_id if next_actions else None,
            capability=None,
            reason="supervisor_plan_patch",
        )
        return self._apply_transition(
            context=context,
            request=request,
            plan=plan,
            pending_actions=pending_actions,
            completed_node_ids=completed_node_ids,
            skipped_node_ids=skipped_node_ids,
            plan_patch=plan_patch,
            stage_decision=stage_decision,
        )

    def handle_subagent_escalation(
        self,
        *,
        context,
        request,
        plan,
        pending_actions,
        completed_node_ids,
        skipped_node_ids,
        action,
        escalation_error,
    ) -> list[dict[str, Any]]:
        plan_patch = escalation_error.plan_patch
        if plan_patch is None:
            return []
        stage_decision = self.decision_source.build_action_transition(
            plan=plan,
            plan_patch=plan_patch,
            source_domain="subagent_capability_gap",
            source_stage=str(escalation_error.gap.stage or "").strip() or None,
            source_action_node_id=str(action.node_id or "").strip() or None,
            capability=str(escalation_error.gap.capability or "").strip() or None,
            reason=str(escalation_error.escalation_decision.reason or "").strip()
            or "subagent_capability_gap_action_replan",
        )
        return self._apply_transition(
            context=context,
            request=request,
            plan=plan,
            pending_actions=pending_actions,
            completed_node_ids=completed_node_ids,
            skipped_node_ids=skipped_node_ids,
            plan_patch=plan_patch,
            stage_decision=stage_decision,
        )

    def _apply_transition(
        self,
        *,
        context,
        request,
        plan,
        pending_actions,
        completed_node_ids,
        skipped_node_ids,
        plan_patch,
        stage_decision: RuntimeControlTransitionStageDecision,
    ) -> list[dict[str, Any]]:
        self._runtime_binding = {
            "context": context,
            "request_id": request.request_id,
            "intent": plan.intent,
        }
        try:
            self.control_stage_recorder.record(
                event_type=stage_decision.event_type,
                message=stage_decision.message(),
                payload=stage_decision.event_payload(),
                phase="supervision",
                agent="runtime_control",
            )
            if stage_decision.request_replan:
                return self.request_action_plan_replan(
                    context=context,
                    request=request,
                    plan=plan,
                    pending_actions=pending_actions,
                    completed_node_ids=completed_node_ids,
                    skipped_node_ids=skipped_node_ids,
                    plan_patch=plan_patch,
                )
            return self.apply_plan_patch(
                context=context,
                request=request,
                plan=plan,
                pending_actions=pending_actions,
                plan_patch=plan_patch,
            )
        finally:
            self._runtime_binding = None

    def _emit_runtime_event(
        self,
        *,
        event_type: str,
        message: str,
        payload: dict[str, Any],
        phase: str | None = None,
        agent: str | None = None,
    ) -> None:
        binding = dict(self._runtime_binding or {})
        context = binding.get("context")
        request_id = str(binding.get("request_id") or "").strip()
        intent = str(binding.get("intent") or payload.get("intent") or "").strip()
        if context is None or not request_id or not intent:
            return
        self.host._emit_event(
            context,
            request_id=request_id,
            intent=intent,
            event_type=event_type,
            phase=phase,
            agent=agent,
            message=message,
            payload=dict(payload),
        )

__all__ = ["OrchestratorRuntimeControlController"]
