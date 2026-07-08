from __future__ import annotations

from collections.abc import Callable
from typing import Any

from research_system.agents._subagent_discovery_stage_decisions import (
    SubagentDiscoveryActivationStageDecision,
    SubagentDiscoveryCandidateStageDecision,
    SubagentDiscoveryCandidateStageResult,
    SubagentDiscoveryProtocolStageDecision,
    SubagentDiscoveryReviewStageDecision,
)
from research_system.schemas.subagents import (
    SubagentCatalogEntry,
    SubagentDiscoveryTicket,
    SubagentToolDiscoveryRequest,
)


class SubagentDiscoveryStagePolicySource:
    def __init__(
        self,
        *,
        discovery_policy,
        discovery_llm_policy_source,
        discovery_candidate_policy,
        discovery_approval_policy,
        discovery_activation_policy,
        candidate_entries_resolver: Callable[..., list[SubagentCatalogEntry]],
    ) -> None:
        self.discovery_policy = discovery_policy
        self.discovery_llm_policy_source = discovery_llm_policy_source
        self.discovery_candidate_policy = discovery_candidate_policy
        self.discovery_approval_policy = discovery_approval_policy
        self.discovery_activation_policy = discovery_activation_policy
        self.candidate_entries_resolver = candidate_entries_resolver

    def resolve_protocols(
        self,
        *,
        gap,
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
        candidate_entries: list[SubagentCatalogEntry] | None = None,
    ) -> SubagentDiscoveryCandidateStageResult:
        entries = self.candidate_entries_resolver(
            ticket=ticket,
            candidate_catalog_entries=candidate_entries,
        )
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


__all__ = ["SubagentDiscoveryStagePolicySource"]
