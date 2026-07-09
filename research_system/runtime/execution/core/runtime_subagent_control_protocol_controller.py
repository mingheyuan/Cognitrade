from __future__ import annotations

from typing import Any

from research_system.runtime.orchestration._runtime_control_stage_decisions import (
    RuntimeControlSubagentProtocolStageDecision,
)
from research_system.runtime.orchestration._runtime_control_stage_recorder import (
    RuntimeControlStageRecorder,
)


class OrchestratorRuntimeSubagentControlProtocolController:
    def __init__(
        self,
        host: Any,
        *,
        subagent_controller,
        transition_controller,
    ) -> None:
        self.host = host
        self.subagent_controller = subagent_controller
        self.transition_controller = transition_controller
        self._runtime_binding: dict[str, Any] | None = None
        self.control_stage_recorder = RuntimeControlStageRecorder(
            runtime_binding_getter=lambda: dict(self._runtime_binding or {}),
            emit_event=self._emit_runtime_event,
        )

    def resolve_catalog_entry(
        self,
        **kwargs,
    ):
        resolution = self.subagent_controller.resolve_catalog_entry(**kwargs)
        runtime_binding = dict(kwargs.get("runtime_binding") or {})
        self.transition_controller.handle_catalog_resolution(
            runtime_binding=runtime_binding,
            stage=str(kwargs.get("stage") or "").strip() or None,
            intent=str(kwargs.get("intent") or "").strip() or None,
            capability=str(kwargs.get("capability") or "").strip() or None,
            preferred_subagent_type=str(kwargs.get("preferred_subagent_type") or "").strip() or None,
            requires_llm=kwargs.get("requires_llm"),
            resolution=resolution,
        )
        stage_decision = self._build_catalog_stage_decision(
            stage=str(kwargs.get("stage") or "").strip() or None,
            intent=str(kwargs.get("intent") or "").strip() or None,
            capability=str(kwargs.get("capability") or "").strip() or None,
            preferred_subagent_type=str(kwargs.get("preferred_subagent_type") or "").strip() or None,
            requires_llm=kwargs.get("requires_llm"),
            resolution=resolution,
        )
        self._record_stage_decision(
            runtime_binding=runtime_binding,
            stage_decision=stage_decision,
            phase=str(kwargs.get("stage") or "").strip() or None,
            agent=str(runtime_binding.get("agent") or "runtime_control").strip() or "runtime_control",
        )
        return resolution

    def select_catalog_entry(
        self,
        **kwargs,
    ):
        resolution = self.resolve_catalog_entry(**kwargs)
        if resolution.selected_entry is not None:
            return resolution.selected_entry
        catalog_controller = kwargs["catalog_controller"]
        raise catalog_controller.capability_gap_controller.build_unresolved_gap_error(
            resolution.capability_gap_resolution
        )

    def resolve_capability_gap_escalation(
        self,
        *,
        escalation_controller,
        runtime_binding: dict[str, Any] | None,
        gap,
        preferred_subagent_type: str | None,
        requires_llm: bool | None,
    ):
        resolve_local = getattr(escalation_controller, "resolve_local", None)
        if callable(resolve_local):
            decision = resolve_local(
                gap=gap,
                preferred_subagent_type=preferred_subagent_type,
                requires_llm=requires_llm,
            )
        else:
            decision = escalation_controller.resolve(
                gap=gap,
                preferred_subagent_type=preferred_subagent_type,
                requires_llm=requires_llm,
            )
        self.transition_controller.handle_capability_gap_escalation(
            runtime_binding=dict(runtime_binding or {}),
            gap=gap,
            preferred_subagent_type=preferred_subagent_type,
            requires_llm=requires_llm,
            escalation_decision=decision,
        )
        stage_decision = self._build_capability_gap_stage_decision(
            gap=gap,
            preferred_subagent_type=preferred_subagent_type,
            requires_llm=requires_llm,
            escalation_decision=decision,
        )
        self._record_stage_decision(
            runtime_binding=dict(runtime_binding or {}),
            stage_decision=stage_decision,
            phase=str(getattr(gap, "stage", None) or "").strip() or None,
            agent=str((runtime_binding or {}).get("agent") or "runtime_control").strip() or "runtime_control",
        )
        return decision

    def execute_patch_cycle(
        self,
        **kwargs,
    ):
        resolution = self.subagent_controller.execute_patch_cycle(**kwargs)
        runtime_binding = dict(kwargs.get("runtime_binding") or {})
        self.transition_controller.handle_phase_resolution(
            runtime_binding=runtime_binding,
            plan=kwargs.get("plan"),
            dag_id=str(kwargs.get("dag_id") or "").strip() or None,
            resolution=resolution,
        )
        stage_decision = self._build_phase_stage_decision(
            plan=kwargs.get("plan"),
            dag_id=str(kwargs.get("dag_id") or "").strip() or None,
            resolution=resolution,
        )
        self._record_stage_decision(
            runtime_binding=runtime_binding,
            stage_decision=stage_decision,
            phase=str(getattr(kwargs.get("plan"), "phase", None) or "").strip() or None,
            agent=str(runtime_binding.get("agent") or "runtime_control").strip() or "runtime_control",
        )
        return resolution

    def _record_stage_decision(
        self,
        *,
        runtime_binding: dict[str, Any] | None,
        stage_decision: RuntimeControlSubagentProtocolStageDecision,
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

    @staticmethod
    def _build_catalog_stage_decision(
        *,
        stage: str | None,
        intent: str | None,
        capability: str | None,
        preferred_subagent_type: str | None,
        requires_llm: bool | None,
        resolution,
    ) -> RuntimeControlSubagentProtocolStageDecision:
        capability_gap_resolution = resolution.capability_gap_resolution
        escalation_decision = (
            getattr(capability_gap_resolution, "escalation_decision", None)
            if capability_gap_resolution is not None
            else None
        )
        ticket = getattr(capability_gap_resolution, "ticket", None) if capability_gap_resolution is not None else None
        notes = list(getattr(escalation_decision, "notes", None) or [])
        if resolution.selected_entry is not None and capability_gap_resolution is not None:
            notes = [*notes, "runtime_control_subagent_recovered_via_discovery"]
        return RuntimeControlSubagentProtocolStageDecision(
            intent=intent,
            resolution_source="catalog_selection",
            decision_type="selected" if resolution.selected_entry is not None else "unresolved",
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
            notes=notes,
        )

    @staticmethod
    def _build_phase_stage_decision(
        *,
        plan,
        dag_id: str | None,
        resolution,
    ) -> RuntimeControlSubagentProtocolStageDecision:
        final_patch = resolution.final_patch
        final_request_replan = bool(
            getattr(final_patch, "decision_type", None) == "replan"
            or any(
                str(getattr(operation, "op_type", None) or "") == "request_replan"
                for operation in list(getattr(final_patch, "operations", None) or [])
            )
        ) if final_patch is not None else False
        if resolution.final_plan_variant != resolution.rule_variant_id:
            decision_type = "override_rule_variant"
        elif final_request_replan:
            decision_type = "apply_rule_replan"
        elif resolution.final_patch_rows:
            decision_type = "apply_rule_patch"
        else:
            decision_type = "keep_current"
        return RuntimeControlSubagentProtocolStageDecision(
            intent=str(getattr(plan, "intent", None) or "").strip() or None,
            resolution_source="phase_patch_cycle",
            decision_type=decision_type,
            phase=str(getattr(plan, "phase", None) or "").strip() or None,
            dag_id=dag_id,
            plan_id=str(getattr(plan, "plan_id", None) or "").strip() or None,
            plan_strategy=str(getattr(plan, "strategy", None) or "").strip() or None,
            rule_plan_variant=str(getattr(resolution, "rule_variant_id", None) or "").strip() or None,
            final_plan_variant=str(getattr(resolution, "final_plan_variant", None) or "").strip() or None,
            final_request_replan=final_request_replan,
            patch_count=len(list(getattr(resolution, "final_patch_rows", None) or [])),
            policy_source=str(getattr(final_patch, "policy_source", None) or "rule"),
            notes=list(getattr(final_patch, "notes", None) or []),
        )

    @staticmethod
    def _build_capability_gap_stage_decision(
        *,
        gap,
        preferred_subagent_type: str | None,
        requires_llm: bool | None,
        escalation_decision,
    ) -> RuntimeControlSubagentProtocolStageDecision:
        return RuntimeControlSubagentProtocolStageDecision(
            intent=str(getattr(gap, "intent", None) or "").strip() or None,
            resolution_source="capability_gap_escalation",
            decision_type="request_action_replan"
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


__all__ = ["OrchestratorRuntimeSubagentControlProtocolController"]
