from __future__ import annotations

from typing import Any

from research_system.runtime.execution.core.runtime_protocol_decision_source import (
    OrchestratorRuntimeProtocolDecisionSource,
)
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
        decision_source=None,
    ) -> None:
        self.host = host
        self.subagent_controller = subagent_controller
        self.transition_controller = transition_controller
        self.decision_source = decision_source or OrchestratorRuntimeProtocolDecisionSource()
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
        stage_decision = self.decision_source.build_subagent_catalog_protocol(
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
        stage_decision = self.decision_source.build_subagent_capability_gap_protocol(
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
        stage_decision = self.decision_source.build_subagent_phase_protocol(
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


__all__ = ["OrchestratorRuntimeSubagentControlProtocolController"]
