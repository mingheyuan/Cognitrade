from __future__ import annotations

from typing import Any

from research_system.runtime.orchestration._runtime_control_stage_decisions import (
    RuntimeControlSubagentTransitionStageDecision,
)
from research_system.runtime.orchestration._runtime_control_stage_recorder import (
    RuntimeControlStageRecorder,
)


class OrchestratorRuntimeSubagentTransitionController:
    def __init__(self, host: Any) -> None:
        self.host = host
        self._runtime_binding: dict[str, Any] | None = None
        self.control_stage_recorder = RuntimeControlStageRecorder(
            runtime_binding_getter=lambda: dict(self._runtime_binding or {}),
            emit_event=self._emit_runtime_event,
        )

    def handle_catalog_resolution(
        self,
        *,
        runtime_binding: dict[str, Any] | None,
        stage: str | None,
        intent: str | None,
        capability: str | None,
        preferred_subagent_type: str | None,
        requires_llm: bool | None,
        resolution,
    ) -> None:
        capability_gap_resolution = resolution.capability_gap_resolution
        ticket = getattr(capability_gap_resolution, "ticket", None) if capability_gap_resolution is not None else None
        escalation_decision = (
            getattr(capability_gap_resolution, "escalation_decision", None)
            if capability_gap_resolution is not None
            else None
        )
        if resolution.selected_entry is not None and capability_gap_resolution is None:
            transition_type = "continue_catalog"
        elif resolution.selected_entry is not None:
            transition_type = "continue_discovery"
        else:
            transition_type = "unresolved_capability_gap"
        stage_decision = RuntimeControlSubagentTransitionStageDecision(
            intent=intent,
            transition_source="catalog_selection",
            transition_type=transition_type,
            stage=stage,
            capability=capability,
            preferred_subagent_type=preferred_subagent_type,
            requires_llm=requires_llm,
            selected_subagent_type=(
                str(getattr(resolution.selected_entry, "subagent_type", None) or "").strip() or None
            ),
            discovery_ticket_id=str(getattr(ticket, "ticket_id", None) or "").strip() or None,
            escalation_strategy=str(getattr(escalation_decision, "strategy", None) or "").strip() or None,
            policy_source=str(getattr(escalation_decision, "policy_source", None) or "rule"),
            notes=list(getattr(escalation_decision, "notes", None) or []),
        )
        self._record_stage_decision(
            runtime_binding=runtime_binding,
            stage_decision=stage_decision,
            phase=stage,
            agent=str((runtime_binding or {}).get("agent") or "runtime_control").strip() or "runtime_control",
        )

    def handle_capability_gap_escalation(
        self,
        *,
        runtime_binding: dict[str, Any] | None,
        gap,
        preferred_subagent_type: str | None,
        requires_llm: bool | None,
        escalation_decision,
    ) -> None:
        stage_decision = RuntimeControlSubagentTransitionStageDecision(
            intent=str(getattr(gap, "intent", None) or "").strip() or None,
            transition_source="capability_gap_escalation",
            transition_type="request_action_replan"
            if str(getattr(escalation_decision, "strategy", None) or "") == "action_replan"
            else "continue_discovery",
            stage=str(getattr(gap, "stage", None) or "").strip() or None,
            capability=str(getattr(gap, "capability", None) or "").strip() or None,
            preferred_subagent_type=preferred_subagent_type,
            requires_llm=requires_llm,
            escalation_strategy=str(getattr(escalation_decision, "strategy", None) or "").strip() or None,
            policy_source=str(getattr(escalation_decision, "policy_source", None) or "rule"),
            notes=list(getattr(escalation_decision, "notes", None) or []),
        )
        self._record_stage_decision(
            runtime_binding=runtime_binding,
            stage_decision=stage_decision,
            phase=str(getattr(gap, "stage", None) or "").strip() or None,
            agent=str((runtime_binding or {}).get("agent") or "runtime_control").strip() or "runtime_control",
        )

    def handle_phase_resolution(
        self,
        *,
        runtime_binding: dict[str, Any] | None,
        plan,
        dag_id: str | None,
        resolution,
    ) -> None:
        final_patch = resolution.final_patch
        final_request_replan = bool(
            getattr(final_patch, "decision_type", None) == "replan"
            or any(
                str(getattr(operation, "op_type", None) or "") == "request_replan"
                for operation in list(getattr(final_patch, "operations", None) or [])
            )
        ) if final_patch is not None else False
        if final_request_replan:
            transition_type = "apply_local_replan"
        elif resolution.final_patch_rows:
            transition_type = "apply_local_patch"
        else:
            transition_type = "keep_current"
        stage_decision = RuntimeControlSubagentTransitionStageDecision(
            intent=str(getattr(plan, "intent", None) or "").strip() or None,
            transition_source="phase_patch_cycle",
            transition_type=transition_type,
            phase=str(getattr(plan, "phase", None) or "").strip() or None,
            dag_id=dag_id,
            plan_id=str(getattr(plan, "plan_id", None) or "").strip() or None,
            rule_plan_variant=str(getattr(resolution, "rule_variant_id", None) or "").strip() or None,
            final_plan_variant=str(getattr(resolution, "final_plan_variant", None) or "").strip() or None,
            final_request_replan=final_request_replan,
            patch_count=len(list(getattr(resolution, "final_patch_rows", None) or [])),
            policy_source=str(getattr(final_patch, "policy_source", None) or "rule"),
            notes=list(getattr(final_patch, "notes", None) or []),
        )
        self._record_stage_decision(
            runtime_binding=runtime_binding,
            stage_decision=stage_decision,
            phase=str(getattr(plan, "phase", None) or "").strip() or None,
            agent=str((runtime_binding or {}).get("agent") or "runtime_control").strip() or "runtime_control",
        )

    def _record_stage_decision(
        self,
        *,
        runtime_binding: dict[str, Any] | None,
        stage_decision: RuntimeControlSubagentTransitionStageDecision,
        phase: str | None,
        agent: str | None,
    ) -> None:
        self._runtime_binding = dict(runtime_binding or {})
        try:
            self.control_stage_recorder.record(
                event_type=stage_decision.event_type,
                message=stage_decision.message(),
                payload=stage_decision.event_payload(),
                phase=phase,
                agent=agent,
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


__all__ = ["OrchestratorRuntimeSubagentTransitionController"]
