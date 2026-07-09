from __future__ import annotations

from typing import Literal

from research_system.runtime.orchestration._runtime_control_stage_decisions import (
    RuntimeControlSubagentTransitionStageDecision,
    RuntimeControlTransitionStageDecision,
)


class OrchestratorRuntimeTransitionDecisionSource:
    def build_action_transition(
        self,
        *,
        plan,
        plan_patch,
        source_domain: Literal["action_supervisor", "subagent_capability_gap"],
        source_stage: str | None,
        source_action_node_id: str | None,
        capability: str | None,
        reason: str,
    ) -> RuntimeControlTransitionStageDecision:
        request_replan = self.patch_requests_replan(plan_patch)
        patch_operation_types = [
            str(operation.op_type)
            for operation in list(plan_patch.operations or [])
            if str(operation.op_type or "").strip()
        ]
        return RuntimeControlTransitionStageDecision(
            intent=plan.intent,
            plan_id=plan.plan_id,
            transition_type="request_action_replan" if request_replan else "apply_plan_patch",
            source_domain=source_domain,
            source_stage=source_stage,
            source_action_node_id=source_action_node_id,
            capability=capability,
            policy_source=str(getattr(plan_patch, "policy_source", None) or "rule"),
            patch_id=str(getattr(plan_patch, "patch_id", None) or "").strip() or None,
            patch_decision_type=str(getattr(plan_patch, "decision_type", None) or "").strip() or None,
            patch_operation_types=patch_operation_types,
            request_replan=request_replan,
            reason=reason,
            notes=list(getattr(plan_patch, "notes", None) or []),
        )

    def build_subagent_catalog_transition(
        self,
        *,
        stage: str | None,
        intent: str | None,
        capability: str | None,
        preferred_subagent_type: str | None,
        requires_llm: bool | None,
        resolution,
    ) -> RuntimeControlSubagentTransitionStageDecision:
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
        return RuntimeControlSubagentTransitionStageDecision(
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

    def build_subagent_capability_gap_transition(
        self,
        *,
        gap,
        preferred_subagent_type: str | None,
        requires_llm: bool | None,
        escalation_decision,
    ) -> RuntimeControlSubagentTransitionStageDecision:
        return RuntimeControlSubagentTransitionStageDecision(
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

    def build_subagent_phase_transition(
        self,
        *,
        plan,
        dag_id: str | None,
        resolution,
    ) -> RuntimeControlSubagentTransitionStageDecision:
        final_patch = resolution.final_patch
        final_request_replan = self.patch_requests_replan(final_patch)
        if final_request_replan:
            transition_type = "apply_local_replan"
        elif resolution.final_patch_rows:
            transition_type = "apply_local_patch"
        else:
            transition_type = "keep_current"
        return RuntimeControlSubagentTransitionStageDecision(
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

    @staticmethod
    def patch_requests_replan(plan_patch) -> bool:
        if plan_patch is None:
            return False
        return bool(
            getattr(plan_patch, "decision_type", None) == "replan"
            or any(
                str(getattr(operation, "op_type", None) or "") == "request_replan"
                for operation in list(getattr(plan_patch, "operations", None) or [])
            )
        )


__all__ = ["OrchestratorRuntimeTransitionDecisionSource"]
