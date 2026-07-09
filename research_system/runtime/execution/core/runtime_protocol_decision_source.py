from __future__ import annotations

from research_system.runtime.execution.core.runtime_transition_decision_source import (
    OrchestratorRuntimeTransitionDecisionSource,
)
from research_system.runtime.orchestration._runtime_control_stage_decisions import (
    RuntimeControlProtocolStageDecision,
    RuntimeControlSubagentProtocolStageDecision,
)


class OrchestratorRuntimeProtocolDecisionSource:
    def __init__(self, *, transition_decision_source=None) -> None:
        self.transition_decision_source = (
            transition_decision_source or OrchestratorRuntimeTransitionDecisionSource()
        )

    def build_action_supervisor_protocol(
        self,
        *,
        plan,
        decision,
        ready_actions,
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
            transition_type=self._action_transition_type(plan_patch),
            transition_source_domain="action_supervisor",
            final_action_node_id=decision.next_actions[0].node_id if decision.next_actions else None,
            policy_source=self._patch_policy_source(plan_patch),
            patch_id=str(getattr(plan_patch, "patch_id", None) or "").strip() or None,
            completed_action_node_ids=list(completed_node_ids),
            skipped_action_node_ids=list(skipped_node_ids),
            ready_action_node_ids=[action.node_id for action in ready_actions],
            next_action_node_ids=[action.node_id for action in decision.next_actions],
            notes=list(decision.notes or []),
        )

    def build_action_subagent_escalation_protocol(
        self,
        *,
        plan,
        action,
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
            transition_type=self._action_transition_type(plan_patch),
            transition_source_domain="subagent_capability_gap",
            trigger_action_node_id=action.node_id,
            capability=str(getattr(escalation_error.gap, "capability", None) or "").strip() or None,
            policy_source=self._patch_policy_source(plan_patch),
            patch_id=str(getattr(plan_patch, "patch_id", None) or "").strip() or None,
            completed_action_node_ids=list(completed_node_ids),
            skipped_action_node_ids=list(skipped_node_ids),
            notes=list(getattr(escalation_error.escalation_decision, "notes", None) or []),
        )

    def build_subagent_catalog_protocol(
        self,
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

    def build_subagent_capability_gap_protocol(
        self,
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

    def build_subagent_phase_protocol(
        self,
        *,
        plan,
        dag_id: str | None,
        resolution,
    ) -> RuntimeControlSubagentProtocolStageDecision:
        final_patch = resolution.final_patch
        final_request_replan = self.transition_decision_source.patch_requests_replan(final_patch)
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

    def _action_transition_type(self, plan_patch) -> str:
        if self.transition_decision_source.patch_requests_replan(plan_patch):
            return "request_action_replan"
        return "apply_plan_patch"

    @staticmethod
    def _patch_policy_source(plan_patch) -> str:
        return str(getattr(plan_patch, "policy_source", None) or "rule")


__all__ = ["OrchestratorRuntimeProtocolDecisionSource"]
