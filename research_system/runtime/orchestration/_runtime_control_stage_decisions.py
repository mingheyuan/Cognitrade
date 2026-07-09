from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from research_system.schemas.subagents import (
    SubagentCatalogEntry,
    SubagentDiscoveryCandidateRecommendation,
    SubagentRuntimeRegistration,
    SubagentToolDiscoveryRequest,
)


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


@dataclass(frozen=True)
class SubagentDiscoveryProtocolStageDecision:
    request: SubagentToolDiscoveryRequest
    preferred_subagent_type: str | None
    candidate_protocols: list[str]
    rule_preferred_protocols: list[str]
    selected_protocols: list[str]
    policy_source: Literal["rule", "llm", "hybrid"]
    reason: str
    shadow_reason: str | None = None
    confidence: float | None = None
    notes: list[str] = field(default_factory=list)

    event_type: Literal["subagent_tool_discovery_protocol_decided"] = "subagent_tool_discovery_protocol_decided"
    control_domain: Literal["subagent"] = "subagent"
    control_stage: Literal["protocol_selection"] = "protocol_selection"
    decision_stage: Literal["protocol_selection"] = "protocol_selection"

    def event_payload(self) -> dict[str, object]:
        return {
            "control_domain": self.control_domain,
            "control_stage": self.control_stage,
            "decision_stage": self.decision_stage,
            "intent": self.request.intent,
            "stage": self.request.stage,
            "capability": self.request.capability,
            "preferred_subagent_type": self.preferred_subagent_type,
            "policy_source": self.policy_source,
            "candidate_protocols": list(self.candidate_protocols),
            "rule_preferred_protocols": list(self.rule_preferred_protocols),
            "selected_protocols": list(self.selected_protocols),
            "reason": self.reason,
            "shadow_reason": self.shadow_reason,
            "confidence": self.confidence,
            "notes": list(self.notes),
        }

    def message(self) -> str:
        return f"{self.event_type} resolved discovery protocol_selection for capability {self.request.capability}"


@dataclass(frozen=True)
class SubagentDiscoveryCandidateStageDecision:
    ticket_id: str
    intent: str | None
    stage: str | None
    capability: str
    preferred_subagent_type: str | None
    recommendation: SubagentDiscoveryCandidateRecommendation | None
    shadow_recommendation: SubagentDiscoveryCandidateRecommendation | None
    candidate_subagent_types: list[str]
    candidate_protocols: list[str]
    reason: str
    confidence: float | None = None
    notes: list[str] = field(default_factory=list)

    event_type: Literal["subagent_tool_discovery_candidate_decided"] = "subagent_tool_discovery_candidate_decided"
    control_domain: Literal["subagent"] = "subagent"
    control_stage: Literal["candidate_selection"] = "candidate_selection"
    decision_stage: Literal["candidate_selection"] = "candidate_selection"

    @property
    def policy_source(self) -> str:
        if self.recommendation is None:
            return "rule"
        return str(self.recommendation.policy_source or "rule")

    def event_payload(self) -> dict[str, object]:
        return {
            "control_domain": self.control_domain,
            "control_stage": self.control_stage,
            "decision_stage": self.decision_stage,
            "ticket_id": self.ticket_id,
            "intent": self.intent,
            "stage": self.stage,
            "capability": self.capability,
            "preferred_subagent_type": self.preferred_subagent_type,
            "policy_source": self.policy_source,
            "candidate_subagent_types": list(self.candidate_subagent_types),
            "candidate_protocols": list(self.candidate_protocols),
            "selected_candidate_subagent_type": (
                self.recommendation.candidate_subagent_type
                if self.recommendation is not None
                else None
            ),
            "selected_protocol": (
                self.recommendation.source_protocol
                if self.recommendation is not None
                else None
            ),
            "shadow_candidate_subagent_type": (
                self.shadow_recommendation.candidate_subagent_type
                if self.shadow_recommendation is not None
                else None
            ),
            "shadow_protocol": (
                self.shadow_recommendation.source_protocol
                if self.shadow_recommendation is not None
                else None
            ),
            "reason": self.reason,
            "confidence": self.confidence,
            "notes": list(self.notes),
        }

    def message(self) -> str:
        return f"{self.event_type} resolved discovery candidate_selection for capability {self.capability}"


@dataclass(frozen=True)
class SubagentDiscoveryCandidateStageResult:
    candidate_entries: list[SubagentCatalogEntry]
    recommendation: SubagentDiscoveryCandidateRecommendation | None
    shadow_recommendation: SubagentDiscoveryCandidateRecommendation | None
    preview_notes: list[str]
    decision: SubagentDiscoveryCandidateStageDecision


@dataclass(frozen=True)
class SubagentDiscoveryActivationTarget:
    candidate_entry: SubagentCatalogEntry
    delegate_subagent_type: str | None
    runtime_registration: SubagentRuntimeRegistration | None


@dataclass(frozen=True)
class SubagentDiscoveryReviewStageDecision:
    ticket_id: str
    intent: str | None
    stage: str | None
    capability: str
    policy_source: Literal["rule", "explicit"] = "rule"
    decision: Literal["pending", "approved", "rejected"] = "pending"
    approved: bool = False
    reviewer: str = "system_auto_gate"
    source: str = "auto_discovery_policy"
    selected_candidate_subagent_type: str | None = None
    selected_protocol: str | None = None
    reason: str = "pending_manual_review_discovery_preview"
    notes: list[str] = field(default_factory=list)

    event_type: Literal["subagent_tool_discovery_review_decided"] = "subagent_tool_discovery_review_decided"
    control_domain: Literal["subagent"] = "subagent"
    control_stage: Literal["approval"] = "approval"
    decision_stage: Literal["approval"] = "approval"

    def event_payload(self) -> dict[str, object]:
        return {
            "control_domain": self.control_domain,
            "control_stage": self.control_stage,
            "decision_stage": self.decision_stage,
            "ticket_id": self.ticket_id,
            "intent": self.intent,
            "stage": self.stage,
            "capability": self.capability,
            "policy_source": self.policy_source,
            "decision": self.decision,
            "approved": self.approved,
            "reviewer": self.reviewer,
            "source": self.source,
            "selected_candidate_subagent_type": self.selected_candidate_subagent_type,
            "selected_protocol": self.selected_protocol,
            "reason": self.reason,
            "notes": list(self.notes),
        }

    def message(self) -> str:
        return f"{self.event_type} resolved discovery approval for capability {self.capability}"


@dataclass(frozen=True)
class SubagentDiscoveryActivationStageDecision:
    ticket_id: str
    intent: str | None
    stage: str | None
    capability: str
    policy_source: Literal["rule", "explicit"] = "rule"
    activate: bool = False
    candidate_subagent_type: str | None = None
    registration_template_id: str | None = None
    delegate_subagent_type: str | None = None
    runtime_registration: SubagentRuntimeRegistration | None = None
    activated_by: str | None = None
    reason: str = "discovery_auto_activation_disabled"
    notes: list[str] = field(default_factory=list)

    event_type: Literal["subagent_tool_discovery_activation_decided"] = "subagent_tool_discovery_activation_decided"
    control_domain: Literal["subagent"] = "subagent"
    control_stage: Literal["activation"] = "activation"
    decision_stage: Literal["activation"] = "activation"

    def event_payload(self, *, activation_outcome: Literal["skipped", "failed", "activated"]) -> dict[str, object]:
        return {
            "control_domain": self.control_domain,
            "control_stage": self.control_stage,
            "decision_stage": self.decision_stage,
            "ticket_id": self.ticket_id,
            "intent": self.intent,
            "stage": self.stage,
            "capability": self.capability,
            "policy_source": self.policy_source,
            "activate": self.activate,
            "activation_outcome": activation_outcome,
            "candidate_subagent_type": self.candidate_subagent_type,
            "registration_template_id": self.registration_template_id,
            "delegate_subagent_type": self.delegate_subagent_type,
            "activated_by": self.activated_by,
            "reason": self.reason,
            "notes": list(self.notes),
        }

    def message(self, *, activation_outcome: Literal["skipped", "failed", "activated"]) -> str:
        return (
            f"{self.event_type} resolved discovery activation={activation_outcome} "
            f"for capability {self.capability}"
        )


__all__ = [
    "ActionPhaseControlStageDecision",
    "SubagentCatalogStageDecision",
    "SubagentCapabilityGapEscalationControlStageDecision",
    "SubagentDiscoveryActivationStageDecision",
    "SubagentDiscoveryActivationTarget",
    "SubagentDiscoveryCandidateStageDecision",
    "SubagentDiscoveryCandidateStageResult",
    "SubagentDiscoveryProtocolStageDecision",
    "SubagentDiscoveryReviewStageDecision",
    "SubagentPhaseExecutionControlStageDecision",
]
