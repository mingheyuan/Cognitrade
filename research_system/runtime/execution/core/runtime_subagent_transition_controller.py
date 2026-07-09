from __future__ import annotations

from typing import Any

from research_system.runtime.orchestration._runtime_control_stage_decisions import (
    RuntimeControlSubagentTransitionStageDecision,
)
from research_system.runtime.orchestration._runtime_control_stage_recorder import (
    RuntimeControlStageRecorder,
)
from research_system.runtime.execution.core.runtime_transition_decision_source import (
    OrchestratorRuntimeTransitionDecisionSource,
)


class OrchestratorRuntimeSubagentTransitionController:
    def __init__(self, host: Any, *, decision_source=None) -> None:
        self.host = host
        self.decision_source = decision_source or OrchestratorRuntimeTransitionDecisionSource()
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
        stage_decision = self.decision_source.build_subagent_catalog_transition(
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
        stage_decision = self.decision_source.build_subagent_capability_gap_transition(
            gap=gap,
            preferred_subagent_type=preferred_subagent_type,
            requires_llm=requires_llm,
            escalation_decision=escalation_decision,
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
        stage_decision = self.decision_source.build_subagent_phase_transition(
            plan=plan,
            dag_id=dag_id,
            resolution=resolution,
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
