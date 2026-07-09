from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research_system.runtime.orchestration._runtime_control_stage_decisions import (
    RuntimeControlProtocolStageDecision,
)
from research_system.runtime.orchestration._runtime_control_stage_recorder import (
    RuntimeControlStageRecorder,
)
from research_system.schemas.planning import ActionPlan, ActionPlanPatch, ActionSpec, SupervisorDecision


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
    ) -> None:
        self.host = host
        self.supervisor = supervisor
        self.transition_controller = transition_controller
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
            stage_decision = self._build_supervisor_stage_decision(
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
            stage_decision = self._build_subagent_escalation_stage_decision(
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

    @staticmethod
    def _build_supervisor_stage_decision(
        *,
        plan: ActionPlan,
        decision: SupervisorDecision,
        ready_actions: list[ActionSpec],
        completed_node_ids: list[str],
        skipped_node_ids: list[str],
    ) -> RuntimeControlProtocolStageDecision:
        plan_patch = decision.plan_patch
        if plan_patch is None:
            raise ValueError("supervisor stage decision requires a plan patch")
        return RuntimeControlProtocolStageDecision(
            intent=plan.intent,
            plan_id=plan.plan_id,
            resolution_source="supervisor_cycle",
            decision_type=decision.decision_type,
            transition_type=OrchestratorRuntimeControlProtocolController._transition_type(plan_patch),
            transition_source_domain="action_supervisor",
            final_action_node_id=decision.next_actions[0].node_id if decision.next_actions else None,
            policy_source=OrchestratorRuntimeControlProtocolController._patch_policy_source(plan_patch),
            patch_id=str(getattr(plan_patch, "patch_id", None) or "").strip() or None,
            completed_action_node_ids=list(completed_node_ids),
            skipped_action_node_ids=list(skipped_node_ids),
            ready_action_node_ids=[action.node_id for action in ready_actions],
            next_action_node_ids=[action.node_id for action in decision.next_actions],
            notes=list(decision.notes or []),
        )

    @staticmethod
    def _build_subagent_escalation_stage_decision(
        *,
        plan: ActionPlan,
        action: ActionSpec,
        escalation_error,
        completed_node_ids: list[str],
        skipped_node_ids: list[str],
    ) -> RuntimeControlProtocolStageDecision:
        plan_patch = escalation_error.plan_patch
        if plan_patch is None:
            raise ValueError("subagent escalation stage decision requires a plan patch")
        return RuntimeControlProtocolStageDecision(
            intent=plan.intent,
            plan_id=plan.plan_id,
            resolution_source="subagent_escalation",
            decision_type="escalation",
            transition_type=OrchestratorRuntimeControlProtocolController._transition_type(plan_patch),
            transition_source_domain="subagent_capability_gap",
            trigger_action_node_id=action.node_id,
            capability=str(getattr(escalation_error.gap, "capability", None) or "").strip() or None,
            policy_source=OrchestratorRuntimeControlProtocolController._patch_policy_source(plan_patch),
            patch_id=str(getattr(plan_patch, "patch_id", None) or "").strip() or None,
            completed_action_node_ids=list(completed_node_ids),
            skipped_action_node_ids=list(skipped_node_ids),
            notes=list(getattr(escalation_error.escalation_decision, "notes", None) or []),
        )

    @staticmethod
    def _transition_type(plan_patch: ActionPlanPatch) -> str:
        if plan_patch.decision_type == "replan":
            return "request_action_replan"
        if any(operation.op_type == "request_replan" for operation in list(plan_patch.operations or [])):
            return "request_action_replan"
        return "apply_plan_patch"

    @staticmethod
    def _patch_policy_source(plan_patch: ActionPlanPatch) -> str:
        return str(getattr(plan_patch, "policy_source", None) or "rule")


__all__ = [
    "OrchestratorRuntimeControlProtocolController",
    "RuntimeControlProtocolResolution",
]
