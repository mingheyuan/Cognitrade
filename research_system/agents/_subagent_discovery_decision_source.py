from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from research_system.schemas.subagents import (
    SubagentCatalogEntry,
    SubagentCapabilityGap,
    SubagentDiscoveryCandidateRecommendation,
    SubagentDiscoveryReviewEvent,
    SubagentDiscoveryTicket,
    SubagentToolDiscoveryRequest,
)


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

    def event_payload(self) -> dict[str, object]:
        return {
            "decision_stage": "protocol_selection",
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

    @property
    def policy_source(self) -> str:
        if self.recommendation is None:
            return "rule"
        return str(self.recommendation.policy_source or "rule")

    def event_payload(self) -> dict[str, object]:
        return {
            "decision_stage": "candidate_selection",
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


@dataclass(frozen=True)
class SubagentDiscoveryCandidateStageResult:
    candidate_entries: list[SubagentCatalogEntry]
    recommendation: SubagentDiscoveryCandidateRecommendation | None
    shadow_recommendation: SubagentDiscoveryCandidateRecommendation | None
    preview_notes: list[str]
    decision: SubagentDiscoveryCandidateStageDecision


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

    def event_payload(self) -> dict[str, object]:
        return {
            "decision_stage": "approval",
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


@dataclass(frozen=True)
class SubagentDiscoveryActivationStageDecision:
    ticket_id: str
    intent: str | None
    stage: str | None
    capability: str
    policy_source: Literal["rule"] = "rule"
    activate: bool = False
    candidate_subagent_type: str | None = None
    registration_template_id: str | None = None
    reason: str = "discovery_auto_activation_disabled"
    notes: list[str] = field(default_factory=list)

    def event_payload(self, *, activation_outcome: Literal["skipped", "failed", "activated"]) -> dict[str, object]:
        return {
            "decision_stage": "activation",
            "ticket_id": self.ticket_id,
            "intent": self.intent,
            "stage": self.stage,
            "capability": self.capability,
            "policy_source": self.policy_source,
            "activate": self.activate,
            "activation_outcome": activation_outcome,
            "candidate_subagent_type": self.candidate_subagent_type,
            "registration_template_id": self.registration_template_id,
            "reason": self.reason,
            "notes": list(self.notes),
        }


class SubagentDiscoveryDecisionSource:
    def __init__(
        self,
        *,
        discovery_policy,
        discovery_llm_policy_source,
        discovery_candidate_policy,
        discovery_approval_policy,
        discovery_activation_policy,
    ) -> None:
        self.discovery_policy = discovery_policy
        self.discovery_llm_policy_source = discovery_llm_policy_source
        self.discovery_candidate_policy = discovery_candidate_policy
        self.discovery_approval_policy = discovery_approval_policy
        self.discovery_activation_policy = discovery_activation_policy

    def resolve_protocols(
        self,
        *,
        gap: SubagentCapabilityGap,
    ) -> SubagentDiscoveryProtocolStageDecision:
        search_terms = [
            str(gap.intent or "").strip(),
            str(gap.stage or "").strip(),
            str(gap.capability or "").strip(),
            str(gap.preferred_subagent_type or "").strip(),
        ]
        rule_preferred_protocols, policy_notes = self.discovery_policy.resolve(
            intent=gap.intent,
            stage=gap.stage,
            capability=gap.capability,
            preferred_subagent_type=gap.preferred_subagent_type,
            requires_llm=gap.requires_llm,
        )
        preferred_protocols, policy_notes, shadow_payload, policy_source = self.discovery_llm_policy_source.apply(
            gap=gap,
            candidate_protocols=list(self.discovery_policy.default_preferred_protocols),
            rule_preferred_protocols=rule_preferred_protocols,
            rule_notes=policy_notes,
        )
        request = SubagentToolDiscoveryRequest(
            intent=gap.intent,
            stage=gap.stage,
            capability=gap.capability,
            search_query=" ".join(token for token in search_terms if token),
            preferred_protocols=preferred_protocols,
            reason=gap.reason,
            policy_source=policy_source,
            notes=[
                "tool_discovery_requested_from_capability_gap",
                f"gap_id={gap.gap_id}",
                *policy_notes,
            ],
        )
        return SubagentDiscoveryProtocolStageDecision(
            request=request,
            preferred_subagent_type=gap.preferred_subagent_type,
            candidate_protocols=list(self.discovery_policy.default_preferred_protocols),
            rule_preferred_protocols=list(rule_preferred_protocols),
            selected_protocols=list(preferred_protocols),
            policy_source=policy_source,
            reason=gap.reason,
            shadow_reason=(
                shadow_payload.get("reason")
                if isinstance(shadow_payload, dict)
                else None
            ),
            confidence=(
                float(shadow_payload.get("confidence"))
                if isinstance(shadow_payload, dict) and shadow_payload.get("confidence") is not None
                else None
            ),
            notes=list(policy_notes or []),
        )

    def select_candidate(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
        candidate_entries: list[SubagentCatalogEntry],
    ) -> SubagentDiscoveryCandidateStageResult:
        entries = [
            entry for entry in list(candidate_entries or [])
            if isinstance(entry, SubagentCatalogEntry)
        ]
        recommendation, shadow_recommendation = self.discovery_candidate_policy.recommend(
            ticket=ticket,
            candidate_entries=entries,
        )
        preview_notes = list(ticket.notes or [])
        if "candidate_preview_generated_from_runtime_discovery_request" not in preview_notes:
            preview_notes.append("candidate_preview_generated_from_runtime_discovery_request")
        if recommendation is not None:
            recommendation_note = f"recommended_candidate={recommendation.candidate_subagent_type}"
            if recommendation_note not in preview_notes:
                preview_notes.append(recommendation_note)
        return SubagentDiscoveryCandidateStageResult(
            candidate_entries=entries,
            recommendation=recommendation,
            shadow_recommendation=shadow_recommendation,
            preview_notes=preview_notes,
            decision=SubagentDiscoveryCandidateStageDecision(
                ticket_id=ticket.ticket_id,
                intent=ticket.capability_gap.intent,
                stage=ticket.capability_gap.stage,
                capability=ticket.capability_gap.capability,
                preferred_subagent_type=ticket.capability_gap.preferred_subagent_type,
                recommendation=recommendation,
                shadow_recommendation=shadow_recommendation,
                candidate_subagent_types=[
                    str(entry.subagent_type).strip()
                    for entry in entries
                    if str(entry.subagent_type or "").strip()
                ],
                candidate_protocols=[self._entry_protocol(entry) for entry in entries],
                reason=(
                    recommendation.reason
                    if recommendation is not None
                    else ticket.capability_gap.reason
                ),
                confidence=(
                    recommendation.confidence
                    if recommendation is not None
                    else None
                ),
                notes=list((recommendation.notes if recommendation is not None else []) or []),
            ),
        )

    def evaluate_review(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
    ) -> SubagentDiscoveryReviewStageDecision:
        approval_decision = self.discovery_approval_policy.evaluate(ticket=ticket)
        return SubagentDiscoveryReviewStageDecision(
            ticket_id=ticket.ticket_id,
            intent=ticket.capability_gap.intent,
            stage=ticket.capability_gap.stage,
            capability=ticket.capability_gap.capability,
            decision=str(approval_decision.get("decision") or "pending"),  # type: ignore[arg-type]
            approved=bool(approval_decision.get("approved")),
            reviewer=str(approval_decision.get("reviewer") or "system_auto_gate"),
            source=str(approval_decision.get("source") or "auto_discovery_policy"),
            selected_candidate_subagent_type=(
                ticket.candidate_recommendation.candidate_subagent_type
                if ticket.candidate_recommendation is not None
                else None
            ),
            selected_protocol=self._selected_protocol(ticket=ticket),
            reason=str(
                approval_decision.get("reason")
                or "pending_manual_review_discovery_preview"
            ),
            notes=[
                str(item).strip()
                for item in list(approval_decision.get("notes") or [])
                if str(item).strip()
            ],
        )

    def build_manual_review(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
        decision: str,
        reviewer: str,
        reason: str | None = None,
        source: str = "manual_cli_review",
        notes: list[str] | None = None,
    ) -> SubagentDiscoveryReviewStageDecision:
        normalized_decision = str(decision or "").strip().lower()
        final_decision: Literal["pending", "approved", "rejected"]
        if normalized_decision == "approve":
            final_decision = "approved"
        elif normalized_decision == "reject":
            final_decision = "rejected"
        elif normalized_decision in {"approved", "rejected", "pending"}:
            final_decision = normalized_decision  # type: ignore[assignment]
        else:
            final_decision = "pending"
        return SubagentDiscoveryReviewStageDecision(
            ticket_id=ticket.ticket_id,
            intent=ticket.capability_gap.intent,
            stage=ticket.capability_gap.stage,
            capability=ticket.capability_gap.capability,
            policy_source="explicit",
            decision=final_decision,
            approved=final_decision == "approved",
            reviewer=str(reviewer or "manual_cli_review").strip() or "manual_cli_review",
            source=str(source or "manual_cli_review").strip() or "manual_cli_review",
            selected_candidate_subagent_type=(
                ticket.candidate_recommendation.candidate_subagent_type
                if ticket.candidate_recommendation is not None
                else None
            ),
            selected_protocol=self._selected_protocol(ticket=ticket),
            reason=str(
                reason
                or f"manual_{final_decision}_discovery_ticket"
            ),
            notes=[
                str(item).strip()
                for item in list(notes or [])
                if str(item).strip()
            ],
        )

    def evaluate_activation(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
    ) -> SubagentDiscoveryActivationStageDecision:
        activation_decision = self.discovery_activation_policy.evaluate(ticket=ticket)
        return SubagentDiscoveryActivationStageDecision(
            ticket_id=ticket.ticket_id,
            intent=ticket.capability_gap.intent,
            stage=ticket.capability_gap.stage,
            capability=ticket.capability_gap.capability,
            activate=bool(activation_decision.get("activate")),
            candidate_subagent_type=str(
                activation_decision.get("candidate_subagent_type") or ""
            ).strip() or None,
            registration_template_id=str(
                activation_decision.get("registration_template_id") or ""
            ).strip() or None,
            reason=str(
                activation_decision.get("reason")
                or "discovery_auto_activation_disabled"
            ),
            notes=[
                str(item).strip()
                for item in list(activation_decision.get("notes") or [])
                if str(item).strip()
            ],
        )

    @staticmethod
    def apply_candidate_selection(
        *,
        ticket: SubagentDiscoveryTicket,
        candidate_result: SubagentDiscoveryCandidateStageResult,
    ) -> None:
        ticket.candidate_catalog_entries = list(candidate_result.candidate_entries)
        ticket.candidate_recommendation = candidate_result.recommendation
        ticket.candidate_shadow_recommendation = candidate_result.shadow_recommendation
        ticket.notes = list(candidate_result.preview_notes)

    @staticmethod
    def apply_review(
        *,
        ticket: SubagentDiscoveryTicket,
        review_decision: SubagentDiscoveryReviewStageDecision,
    ) -> str:
        previous_status = str(ticket.status or "pending").strip() or "pending"
        reviewed_at = datetime.now(timezone.utc)
        ticket.status = review_decision.decision
        ticket.reviewer = review_decision.reviewer
        ticket.review_reason = review_decision.reason
        ticket.reviewed_at = reviewed_at
        ticket.updated_at = reviewed_at
        review_history = list(ticket.review_history or [])
        review_history.append(
            SubagentDiscoveryReviewEvent(
                decision=ticket.status,
                reviewer=review_decision.reviewer,
                reason=review_decision.reason,
                reviewed_at=reviewed_at,
                previous_status=previous_status,  # type: ignore[arg-type]
                source=review_decision.source,
            )
        )
        ticket.review_history = review_history
        notes = list(ticket.notes or [])
        for note in list(review_decision.notes or []):
            if note and note not in notes:
                notes.append(note)
        ticket.notes = notes
        return previous_status

    @classmethod
    def _selected_protocol(cls, *, ticket: SubagentDiscoveryTicket) -> str | None:
        recommendation = ticket.candidate_recommendation
        protocol = str(getattr(recommendation, "source_protocol", "") or "").strip()
        if protocol:
            return protocol
        candidate_entry = cls._recommended_entry(ticket=ticket)
        if candidate_entry is None:
            return None
        return cls._entry_protocol(candidate_entry)

    @staticmethod
    def _recommended_entry(*, ticket: SubagentDiscoveryTicket) -> SubagentCatalogEntry | None:
        recommendation = ticket.candidate_recommendation
        requested = str(getattr(recommendation, "candidate_subagent_type", "") or "").strip()
        if requested:
            for entry in list(ticket.candidate_catalog_entries or []):
                if str(entry.subagent_type).strip() == requested:
                    return entry
        entries = list(ticket.candidate_catalog_entries or [])
        if len(entries) == 1:
            return entries[0]
        return None

    @staticmethod
    def _entry_protocol(entry: SubagentCatalogEntry) -> str | None:
        tags = [str(tag).strip() for tag in list(entry.tags or []) if str(tag).strip()]
        for tag in tags:
            if tag.startswith("mcp_source:"):
                return "mcp"
            if tag.startswith("openapi_source:"):
                return "openapi"
            if tag.startswith("skill_registration:"):
                return "skill"
            if tag.startswith("http_json_source:"):
                return "http_json"
        return None


__all__ = [
    "SubagentDiscoveryActivationStageDecision",
    "SubagentDiscoveryCandidateStageDecision",
    "SubagentDiscoveryCandidateStageResult",
    "SubagentDiscoveryDecisionSource",
    "SubagentDiscoveryProtocolStageDecision",
    "SubagentDiscoveryReviewStageDecision",
]
