from __future__ import annotations

from typing import Any

from research_system.runtime.orchestration._runtime_control_stage_decisions import (
    RuntimeControlSubagentPhaseStageDecision,
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

    def execute_patch_cycle(
        self,
        *,
        phase_execution_controller,
        runtime_binding: dict[str, Any] | None,
        plan,
        dag_id: str,
        base_max_workers: int | None,
        final_max_workers: int | None,
        build_invocation_factory,
        patch_builder,
        replan_builder=None,
        allow_current_plan_variant: bool = False,
        candidate_plan_variants_builder=None,
    ):
        resolution = phase_execution_controller.execute_patch_cycle(
            plan=plan,
            dag_id=dag_id,
            base_max_workers=base_max_workers,
            final_max_workers=final_max_workers,
            build_invocation_factory=build_invocation_factory,
            patch_builder=patch_builder,
            replan_builder=replan_builder,
            allow_current_plan_variant=allow_current_plan_variant,
            candidate_plan_variants_builder=candidate_plan_variants_builder,
        )
        stage_decision = self._build_phase_stage_decision(
            dag_id=dag_id,
            plan=plan,
            resolution=resolution,
        )
        self._record_stage_decision(
            runtime_binding=runtime_binding,
            stage_decision=stage_decision,
            phase=str(getattr(plan, "phase", None) or "").strip() or None,
            agent=str((runtime_binding or {}).get("agent") or "runtime_control").strip() or "runtime_control",
        )
        return resolution

    def _record_stage_decision(
        self,
        *,
        runtime_binding: dict[str, Any] | None,
        stage_decision: RuntimeControlSubagentStageDecision | RuntimeControlSubagentPhaseStageDecision,
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

    @staticmethod
    def _build_phase_stage_decision(
        *,
        dag_id: str,
        plan,
        resolution,
    ) -> RuntimeControlSubagentPhaseStageDecision:
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
        return RuntimeControlSubagentPhaseStageDecision(
            dag_id=dag_id,
            intent=getattr(plan, "intent", None),
            phase=getattr(plan, "phase", None),
            plan_id=getattr(plan, "plan_id", None),
            plan_strategy=getattr(plan, "strategy", None),
            decision_type=decision_type,
            policy_source=str(getattr(final_patch, "policy_source", None) or "rule"),
            rule_plan_variant=resolution.rule_variant_id,
            final_plan_variant=resolution.final_plan_variant,
            override_rule_variant=resolution.rule_variant_id != resolution.final_plan_variant,
            candidate_plan_variants=list(resolution.candidate_plan_variants or []),
            final_request_replan=final_request_replan,
            base_node_ids=[node.node_id for node in list(getattr(plan, "nodes", None) or [])],
            final_node_ids=[
                node.node_id for node in list(getattr(getattr(resolution, "final_plan", None), "nodes", None) or [])
            ],
            patch_count=len(list(resolution.final_patch_rows or [])),
        )


__all__ = [
    "OrchestratorRuntimeSubagentControlController",
]
