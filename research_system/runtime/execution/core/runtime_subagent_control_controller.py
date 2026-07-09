from __future__ import annotations

from typing import Any

from research_system.runtime.orchestration._runtime_control_stage_decisions import (
    RuntimeControlSubagentStageDecision,
)
from research_system.runtime.orchestration._runtime_control_stage_recorder import (
    RuntimeControlStageRecorder,
)

class OrchestratorRuntimeSubagentControlController:
    def __init__(self, host: Any) -> None:
        self.host = host
        self._runtime_binding: dict[str, Any] | None = None
        self.control_stage_recorder = RuntimeControlStageRecorder(
            runtime_binding_getter=lambda: dict(self._runtime_binding or {}),
            emit_event=self._emit_runtime_event,
        )

    def resolve_catalog_entry(
        self,
        *,
        catalog_controller,
        runtime_binding: dict[str, Any] | None,
        stage: str,
        intent: str,
        capability: str,
        preferred_subagent_type: str | None = None,
        requires_llm: bool | None = None,
    ):
        resolution = catalog_controller.resolve_catalog_entry(
            stage=stage,
            intent=intent,
            capability=capability,
            preferred_subagent_type=preferred_subagent_type,
            requires_llm=requires_llm,
        )
        stage_decision = self._build_stage_decision(
            stage=stage,
            intent=intent,
            capability=capability,
            preferred_subagent_type=preferred_subagent_type,
            requires_llm=requires_llm,
            resolution=resolution,
        )
        self._record_stage_decision(
            runtime_binding=runtime_binding,
            stage_decision=stage_decision,
            phase=str(stage or "").strip() or None,
            agent=str((runtime_binding or {}).get("agent") or "runtime_control").strip() or "runtime_control",
        )
        return resolution

    def select_catalog_entry(
        self,
        *,
        catalog_controller,
        runtime_binding: dict[str, Any] | None,
        stage: str,
        intent: str,
        capability: str,
        preferred_subagent_type: str | None = None,
        requires_llm: bool | None = None,
    ):
        resolution = self.resolve_catalog_entry(
            catalog_controller=catalog_controller,
            runtime_binding=runtime_binding,
            stage=stage,
            intent=intent,
            capability=capability,
            preferred_subagent_type=preferred_subagent_type,
            requires_llm=requires_llm,
        )
        if resolution.selected_entry is not None:
            return resolution.selected_entry
        raise catalog_controller.capability_gap_controller.build_unresolved_gap_error(
            resolution.capability_gap_resolution
        )

    def _record_stage_decision(
        self,
        *,
        runtime_binding: dict[str, Any] | None,
        stage_decision: RuntimeControlSubagentStageDecision,
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
    def _build_stage_decision(
        *,
        stage: str,
        intent: str,
        capability: str,
        preferred_subagent_type: str | None,
        requires_llm: bool | None,
        resolution,
    ) -> RuntimeControlSubagentStageDecision:
        capability_gap_resolution = resolution.capability_gap_resolution
        if resolution.selected_entry is not None and capability_gap_resolution is None:
            return RuntimeControlSubagentStageDecision(
                intent=intent,
                stage=stage,
                capability=capability,
                preferred_subagent_type=preferred_subagent_type,
                requires_llm=requires_llm,
                resolution_source="catalog",
                decision_type="selected",
                selected_subagent_type=resolution.selected_entry.subagent_type,
            )
        if resolution.selected_entry is not None:
            ticket = getattr(capability_gap_resolution, "ticket", None)
            escalation_decision = getattr(capability_gap_resolution, "escalation_decision", None)
            return RuntimeControlSubagentStageDecision(
                intent=intent,
                stage=stage,
                capability=capability,
                preferred_subagent_type=preferred_subagent_type,
                requires_llm=requires_llm,
                resolution_source="capability_gap_discovery",
                decision_type="selected",
                selected_subagent_type=resolution.selected_entry.subagent_type,
                discovery_ticket_id=str(getattr(ticket, "ticket_id", None) or "").strip() or None,
                escalation_strategy=str(getattr(escalation_decision, "strategy", None) or "").strip() or None,
                policy_source=str(getattr(escalation_decision, "policy_source", None) or "rule"),
                notes=["runtime_control_subagent_recovered_via_discovery"],
            )
        ticket = getattr(capability_gap_resolution, "ticket", None) if capability_gap_resolution is not None else None
        escalation_decision = (
            getattr(capability_gap_resolution, "escalation_decision", None)
            if capability_gap_resolution is not None
            else None
        )
        notes: list[str] = []
        if capability_gap_resolution is not None:
            notes.extend(list(getattr(getattr(capability_gap_resolution, "gap", None), "notes", None) or []))
        return RuntimeControlSubagentStageDecision(
            intent=intent,
            stage=stage,
            capability=capability,
            preferred_subagent_type=preferred_subagent_type,
            requires_llm=requires_llm,
            resolution_source="capability_gap_unresolved",
            decision_type="unresolved",
            discovery_ticket_id=str(getattr(ticket, "ticket_id", None) or "").strip() or None,
            escalation_strategy=str(getattr(escalation_decision, "strategy", None) or "").strip() or None,
            policy_source=str(getattr(escalation_decision, "policy_source", None) or "rule"),
            notes=notes,
        )


__all__ = [
    "OrchestratorRuntimeSubagentControlController",
]
