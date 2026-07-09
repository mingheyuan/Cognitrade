from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research_system.runtime.execution.core.runtime_protocol_decision_source import (
    OrchestratorRuntimeProtocolDecisionSource,
)
from research_system.runtime.orchestration._runtime_control_stage_decisions import (
    RuntimeControlProtocolStageDecision,
)
from research_system.runtime.orchestration._runtime_control_stage_recorder import (
    RuntimeControlStageRecorder,
)
from research_system.schemas.planning import ActionPlan, ActionSpec, SupervisorDecision


@dataclass(frozen=True)
class RuntimeControlProtocolResolution:
    decision: SupervisorDecision
    plan_patch_rows: list[dict[str, Any]]


class OrchestratorRuntimeControlProtocolController:
    def __init__(
        self,
        host: Any,
        *,
        supervisor,
        transition_controller,
        decision_source=None,
    ) -> None:
        self.host = host
        self.supervisor = supervisor
        self.transition_controller = transition_controller
        self.decision_source = decision_source or OrchestratorRuntimeProtocolDecisionSource()
        self._runtime_binding: dict[str, Any] | None = None
        self.control_stage_recorder = RuntimeControlStageRecorder(
            runtime_binding_getter=lambda: dict(self._runtime_binding or {}),
            emit_event=self._emit_runtime_event,
        )

    def resolve_supervisor_cycle(
        self,
        *,
        context,
        request,
        plan: ActionPlan,
        ready_actions: list[ActionSpec],
        pending_actions: list[ActionSpec],
        completed_node_ids: list[str],
        skipped_node_ids: list[str],
    ) -> RuntimeControlProtocolResolution:
        decision = self.supervisor.decide(
            plan=plan,
            completed_actions=completed_node_ids[:] + skipped_node_ids[:],
            pending_actions=ready_actions[:],
            blackboard_entries=context.blackboard.list_entries(thread_id=context.thread.thread_id),
        )
        plan_patch_rows: list[dict[str, Any]] = []
        if decision.plan_patch is not None:
            plan_patch_rows = self.transition_controller.handle_supervisor_plan_patch(
                context=context,
                request=request,
                plan=plan,
                pending_actions=pending_actions,
                completed_node_ids=completed_node_ids,
                skipped_node_ids=skipped_node_ids,
                plan_patch=decision.plan_patch,
                next_actions=decision.next_actions,
            )
            stage_decision = self.decision_source.build_action_supervisor_protocol(
                plan=plan,
                decision=decision,
                ready_actions=ready_actions,
                completed_node_ids=completed_node_ids,
                skipped_node_ids=skipped_node_ids,
            )
            self._record_stage_decision(
                context=context,
                request=request,
                plan=plan,
                stage_decision=stage_decision,
            )
        return RuntimeControlProtocolResolution(
            decision=decision,
            plan_patch_rows=plan_patch_rows,
        )

    def handle_subagent_escalation(
        self,
        *,
        context,
        request,
        plan: ActionPlan,
        pending_actions: list[ActionSpec],
        completed_node_ids: list[str],
        skipped_node_ids: list[str],
        action: ActionSpec,
        escalation_error,
    ) -> list[dict[str, Any]]:
        plan_patch_rows = self.transition_controller.handle_subagent_escalation(
            context=context,
            request=request,
            plan=plan,
            pending_actions=pending_actions,
            completed_node_ids=completed_node_ids,
            skipped_node_ids=skipped_node_ids,
            action=action,
            escalation_error=escalation_error,
        )
        if escalation_error.plan_patch is not None:
            stage_decision = self.decision_source.build_action_subagent_escalation_protocol(
                plan=plan,
                action=action,
                escalation_error=escalation_error,
                completed_node_ids=completed_node_ids,
                skipped_node_ids=skipped_node_ids,
            )
            self._record_stage_decision(
                context=context,
                request=request,
                plan=plan,
                stage_decision=stage_decision,
            )
        return plan_patch_rows

    def _record_stage_decision(
        self,
        *,
        context,
        request,
        plan: ActionPlan,
        stage_decision: RuntimeControlProtocolStageDecision,
    ) -> None:
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


__all__ = [
    "OrchestratorRuntimeControlProtocolController",
    "RuntimeControlProtocolResolution",
]
