from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class ActionPhaseControlStageDecision:
    intent: str | None
    plan_id: str | None
    decision_type: str
    policy_source: str = "rule"
    rule_action_node_id: str | None = None
    final_action_node_id: str | None = None
    rule_request_replan: bool = False
    final_request_replan: bool = False
    override_rule_action: bool = False
    override_replan: bool = False
    completed_actions: list[str] = field(default_factory=list)
    pending_action_node_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    event_type: Literal["action_phase_decided"] = "action_phase_decided"
    control_domain: Literal["action"] = "action"
    control_stage: Literal["action_phase"] = "action_phase"
    decision_stage: Literal["action_phase"] = "action_phase"

    def event_payload(self) -> dict[str, object]:
        return {
            "control_domain": self.control_domain,
            "control_stage": self.control_stage,
            "decision_stage": self.decision_stage,
            "intent": self.intent,
            "plan_id": self.plan_id,
            "policy_source": self.policy_source,
            "decision_type": self.decision_type,
            "rule_action_node_id": self.rule_action_node_id,
            "final_action_node_id": self.final_action_node_id,
            "rule_request_replan": self.rule_request_replan,
            "final_request_replan": self.final_request_replan,
            "override_rule_action": self.override_rule_action,
            "override_replan": self.override_replan,
            "completed_actions": list(self.completed_actions),
            "pending_action_node_ids": list(self.pending_action_node_ids),
            "notes": list(self.notes),
        }

    def message(self) -> str:
        return f"{self.event_type} resolved {self.decision_type} for intent {self.intent}"


@dataclass(frozen=True)
class SubagentCatalogStageDecision:
    intent: str | None
    stage: str | None
    capability: str
    preferred_subagent_type: str | None
    requires_llm: bool | None
    decision_type: Literal["selected", "escalate_capability_gap"]
    policy_source: Literal["rule"] = "rule"
    candidate_subagent_types: list[str] = field(default_factory=list)
    selected_subagent_type: str | None = None
    selection_source: Literal["catalog", "capability_gap_discovery"] | None = None
    reason: str = "select_catalog_candidate"
    notes: list[str] = field(default_factory=list)

    event_type: Literal["subagent_catalog_selection_decided"] = "subagent_catalog_selection_decided"
    control_domain: Literal["subagent"] = "subagent"
    control_stage: Literal["catalog_selection"] = "catalog_selection"
    decision_stage: Literal["catalog_selection"] = "catalog_selection"

    def event_payload(self) -> dict[str, object]:
        return {
            "control_domain": self.control_domain,
            "control_stage": self.control_stage,
            "decision_stage": self.decision_stage,
            "intent": self.intent,
            "stage": self.stage,
            "capability": self.capability,
            "preferred_subagent_type": self.preferred_subagent_type,
            "requires_llm": self.requires_llm,
            "policy_source": self.policy_source,
            "decision_type": self.decision_type,
            "candidate_subagent_types": list(self.candidate_subagent_types),
            "selected_subagent_type": self.selected_subagent_type,
            "selection_source": self.selection_source,
            "reason": self.reason,
            "notes": list(self.notes),
        }

    def message(self) -> str:
        return f"{self.event_type} resolved {self.decision_type} for capability {self.capability}"


@dataclass(frozen=True)
class SubagentPhaseExecutionControlStageDecision:
    dag_id: str
    intent: str | None
    phase: str | None
    plan_id: str | None
    plan_strategy: str | None
    policy_source: str
    decision_type: str
    rule_plan_variant: str
    final_plan_variant: str
    override_rule_variant: bool
    candidate_plan_variants: list[str] = field(default_factory=list)
    final_request_replan: bool = False
    base_node_ids: list[str] = field(default_factory=list)
    final_node_ids: list[str] = field(default_factory=list)
    patch_count: int = 0

    event_type: Literal["subagent_phase_execution_decided"] = "subagent_phase_execution_decided"
    control_domain: Literal["subagent"] = "subagent"
    control_stage: Literal["phase_patch_cycle"] = "phase_patch_cycle"
    decision_stage: Literal["phase_execution"] = "phase_execution"

    def event_payload(self) -> dict[str, object]:
        return {
            "control_domain": self.control_domain,
            "control_stage": self.control_stage,
            "decision_stage": self.decision_stage,
            "dag_id": self.dag_id,
            "intent": self.intent,
            "phase": self.phase,
            "plan_id": self.plan_id,
            "plan_strategy": self.plan_strategy,
            "policy_source": self.policy_source,
            "decision_type": self.decision_type,
            "rule_plan_variant": self.rule_plan_variant,
            "final_plan_variant": self.final_plan_variant,
            "override_rule_variant": self.override_rule_variant,
            "candidate_plan_variants": list(self.candidate_plan_variants),
            "final_request_replan": self.final_request_replan,
            "base_node_ids": list(self.base_node_ids),
            "final_node_ids": list(self.final_node_ids),
            "patch_count": self.patch_count,
        }

    def message(self) -> str:
        return f"{self.event_type} resolved {self.decision_type} for DAG {self.dag_id}"


@dataclass(frozen=True)
class SubagentCapabilityGapEscalationControlStageDecision:
    intent: str | None
    stage: str | None
    capability: str
    preferred_subagent_type: str | None
    requires_llm: bool | None
    decision_type: Literal["request_action_replan", "continue_discovery"]
    escalation_strategy: str
    policy_source: str
    rule_id: str | None = None
    reason: str = ""
    notes: list[str] = field(default_factory=list)

    event_type: Literal["subagent_capability_gap_escalated"] = "subagent_capability_gap_escalated"
    control_domain: Literal["subagent"] = "subagent"
    control_stage: Literal["capability_gap_escalation"] = "capability_gap_escalation"
    decision_stage: Literal["capability_gap_escalation"] = "capability_gap_escalation"

    def event_payload(self) -> dict[str, object]:
        return {
            "control_domain": self.control_domain,
            "control_stage": self.control_stage,
            "decision_stage": self.decision_stage,
            "intent": self.intent,
            "stage": self.stage,
            "capability": self.capability,
            "preferred_subagent_type": self.preferred_subagent_type,
            "requires_llm": self.requires_llm,
            "decision_type": self.decision_type,
            "escalation_strategy": self.escalation_strategy,
            "policy_source": self.policy_source,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "notes": list(self.notes),
        }

    def message(self) -> str:
        if self.escalation_strategy == "action_replan":
            return "subagent capability gap escalated to action replan"
        return "subagent capability gap continued through discovery"


__all__ = [
    "ActionPhaseControlStageDecision",
    "SubagentCatalogStageDecision",
    "SubagentCapabilityGapEscalationControlStageDecision",
    "SubagentPhaseExecutionControlStageDecision",
]
