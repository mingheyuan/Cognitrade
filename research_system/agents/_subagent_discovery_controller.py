from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from research_system.runtime.orchestration.subagent_discovery_admin_support import (
    OrchestratorSubagentDiscoveryAdminSupport,
)
from research_system.schemas.subagents import (
    SubagentCapabilityGap,
    SubagentDiscoveryReviewEvent,
    SubagentToolDiscoveryRequest,
)


@dataclass(frozen=True)
class SubagentDiscoveryResolution:
    tool_discovery_request: SubagentToolDiscoveryRequest
    ticket: Any | None = None


class SubagentDiscoveryController:
    def __init__(
        self,
        *,
        discovery_policy,
        discovery_llm_policy_source,
        discovery_candidate_support,
        discovery_candidate_policy,
        discovery_approval_policy,
        discovery_activation_policy,
        activation_host,
    ) -> None:
        self.discovery_policy = discovery_policy
        self.discovery_llm_policy_source = discovery_llm_policy_source
        self.discovery_candidate_support = discovery_candidate_support
        self.discovery_candidate_policy = discovery_candidate_policy
        self.discovery_approval_policy = discovery_approval_policy
        self.discovery_activation_policy = discovery_activation_policy
        self.activation_host = activation_host

    def resolve_runtime_gap(
        self,
        *,
        gap: SubagentCapabilityGap,
        store=None,
    ) -> SubagentDiscoveryResolution:
        request = self.build_request(gap=gap)
        ticket = self.persist_runtime_request(gap=gap, request=request, store=store)
        return SubagentDiscoveryResolution(
            tool_discovery_request=request,
            ticket=ticket,
        )

    def build_request(
        self,
        *,
        gap: SubagentCapabilityGap,
    ) -> SubagentToolDiscoveryRequest:
        search_terms = [
            str(gap.intent or "").strip(),
            str(gap.stage or "").strip(),
            str(gap.capability or "").strip(),
            str(gap.preferred_subagent_type or "").strip(),
        ]
        preferred_protocols, policy_notes = self.discovery_policy.resolve(
            intent=gap.intent,
            stage=gap.stage,
            capability=gap.capability,
            preferred_subagent_type=gap.preferred_subagent_type,
            requires_llm=gap.requires_llm,
        )
        preferred_protocols, policy_notes, _ = self.discovery_llm_policy_source.apply(
            gap=gap,
            candidate_protocols=list(self.discovery_policy.default_preferred_protocols),
            rule_preferred_protocols=preferred_protocols,
            rule_notes=policy_notes,
        )
        return SubagentToolDiscoveryRequest(
            intent=gap.intent,
            stage=gap.stage,
            capability=gap.capability,
            search_query=" ".join(token for token in search_terms if token),
            preferred_protocols=preferred_protocols,
            reason=gap.reason,
            policy_source=gap.policy_source,
            notes=[
                "tool_discovery_requested_from_capability_gap",
                f"gap_id={gap.gap_id}",
                *policy_notes,
            ],
        )

    def persist_runtime_request(
        self,
        *,
        gap: SubagentCapabilityGap,
        request: SubagentToolDiscoveryRequest,
        store,
    ):
        create_request = getattr(store, "create_request", None)
        if not callable(create_request):
            return None
        try:
            ticket = create_request(
                gap=gap,
                request=request,
            )
        except Exception:
            return None
        return self.preview_ticket(ticket=ticket, store=store)

    def preview_ticket(self, *, ticket, store):
        try:
            candidate_entries = self.discovery_candidate_support.candidate_entries(ticket)
        except Exception:
            return ticket
        if not candidate_entries:
            return ticket
        ticket.candidate_catalog_entries = list(candidate_entries)
        recommendation, shadow_recommendation = self.discovery_candidate_policy.recommend(
            ticket=ticket,
            candidate_entries=ticket.candidate_catalog_entries,
        )
        ticket.candidate_recommendation = recommendation
        ticket.candidate_shadow_recommendation = shadow_recommendation
        preview_notes = list(ticket.notes or [])
        if "candidate_preview_generated_from_runtime_discovery_request" not in preview_notes:
            preview_notes.append("candidate_preview_generated_from_runtime_discovery_request")
        if recommendation is not None:
            recommendation_note = f"recommended_candidate={recommendation.candidate_subagent_type}"
            if recommendation_note not in preview_notes:
                preview_notes.append(recommendation_note)
        ticket.notes = preview_notes
        approval_decision = self.discovery_approval_policy.evaluate(ticket=ticket)
        self.apply_review_decision(
            ticket=ticket,
            decision=str(approval_decision.get("decision") or "pending"),
            reviewer=str(approval_decision.get("reviewer") or "system_auto_gate"),
            reason=str(approval_decision.get("reason") or "pending_manual_review_discovery_preview"),
            source=str(approval_decision.get("source") or "auto_discovery_policy"),
            extra_notes=[
                str(item).strip()
                for item in list(approval_decision.get("notes") or [])
                if str(item).strip()
            ],
        )
        save = getattr(store, "save", None)
        if callable(save):
            try:
                save(ticket, previous_status="pending")
            except Exception:
                return ticket
        if str(ticket.status or "").strip().lower() == "approved":
            return self.maybe_auto_activate_ticket(ticket=ticket, store=store)
        return ticket

    def maybe_auto_activate_ticket(self, *, ticket, store):
        activation_decision = self.discovery_activation_policy.evaluate(ticket=ticket)
        if not bool(activation_decision.get("activate")):
            return ticket
        try:
            OrchestratorSubagentDiscoveryAdminSupport(self.activation_host).activate_request(
                ticket.ticket_id,
                candidate_subagent_type=str(
                    activation_decision.get("candidate_subagent_type") or ""
                ).strip()
                or None,
                registration_template_id=str(
                    activation_decision.get("registration_template_id") or ""
                ).strip()
                or None,
                activated_by="system_auto_activate",
                reason=str(
                    activation_decision.get("reason")
                    or "auto_activate_discovery_candidate_preview"
                ),
            )
        except Exception:
            return ticket
        find = getattr(store, "find", None)
        if not callable(find):
            return ticket
        located = find(ticket.ticket_id)
        if isinstance(located, tuple) and located and located[0] is not None:
            return located[0]
        return ticket

    @staticmethod
    def apply_review_decision(
        *,
        ticket,
        decision: str,
        reviewer: str,
        reason: str,
        source: str,
        extra_notes: list[str] | None = None,
    ) -> None:
        previous_status = str(ticket.status or "pending").strip() or "pending"
        reviewed_at = datetime.now(timezone.utc)
        normalized_decision = str(decision or "").strip().lower()
        if normalized_decision == "approved":
            ticket.status = "approved"
        elif normalized_decision == "rejected":
            ticket.status = "rejected"
        else:
            ticket.status = "pending"
        ticket.reviewer = reviewer
        ticket.review_reason = reason
        ticket.reviewed_at = reviewed_at
        ticket.updated_at = reviewed_at
        review_history = list(ticket.review_history or [])
        review_history.append(
            SubagentDiscoveryReviewEvent(
                decision=ticket.status,  # type: ignore[arg-type]
                reviewer=reviewer,
                reason=reason,
                reviewed_at=reviewed_at,
                previous_status=previous_status,  # type: ignore[arg-type]
                source=source,
            )
        )
        ticket.review_history = review_history
        notes = list(ticket.notes or [])
        for note in list(extra_notes or []):
            if note and note not in notes:
                notes.append(note)
        ticket.notes = notes


__all__ = ["SubagentDiscoveryController", "SubagentDiscoveryResolution"]
