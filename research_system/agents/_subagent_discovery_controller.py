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
        runtime_binding_getter=None,
        emit_event=None,
        service_name: str | None = None,
    ) -> None:
        self.discovery_policy = discovery_policy
        self.discovery_llm_policy_source = discovery_llm_policy_source
        self.discovery_candidate_support = discovery_candidate_support
        self.discovery_candidate_policy = discovery_candidate_policy
        self.discovery_approval_policy = discovery_approval_policy
        self.discovery_activation_policy = discovery_activation_policy
        self.activation_host = activation_host
        self.runtime_binding_getter = runtime_binding_getter
        self.emit_event = emit_event
        self.service_name = service_name or "subagent_discovery"

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
            policy_source=policy_source,  # type: ignore[arg-type]
            notes=[
                "tool_discovery_requested_from_capability_gap",
                f"gap_id={gap.gap_id}",
                *policy_notes,
            ],
        )
        self._record_stage_decision(
            event_name="subagent_tool_discovery_protocol_decided",
            payload={
                "decision_stage": "protocol_selection",
                "intent": gap.intent,
                "stage": gap.stage,
                "capability": gap.capability,
                "preferred_subagent_type": gap.preferred_subagent_type,
                "policy_source": policy_source,
                "candidate_protocols": list(self.discovery_policy.default_preferred_protocols),
                "rule_preferred_protocols": list(rule_preferred_protocols),
                "selected_protocols": list(preferred_protocols),
                "reason": gap.reason,
                "shadow_reason": (
                    shadow_payload.get("reason")
                    if isinstance(shadow_payload, dict)
                    else None
                ),
                "confidence": (
                    shadow_payload.get("confidence")
                    if isinstance(shadow_payload, dict)
                    else None
                ),
                "notes": list(policy_notes or []),
            },
        )
        return request

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
        self._record_stage_decision(
            event_name="subagent_tool_discovery_candidate_decided",
            payload={
                "decision_stage": "candidate_selection",
                "ticket_id": ticket.ticket_id,
                "intent": ticket.capability_gap.intent,
                "stage": ticket.capability_gap.stage,
                "capability": ticket.capability_gap.capability,
                "preferred_subagent_type": ticket.capability_gap.preferred_subagent_type,
                "policy_source": (
                    recommendation.policy_source
                    if recommendation is not None
                    else "rule"
                ),
                "candidate_subagent_types": [
                    str(entry.subagent_type).strip()
                    for entry in list(ticket.candidate_catalog_entries or [])
                    if str(entry.subagent_type or "").strip()
                ],
                "candidate_protocols": [
                    self.discovery_candidate_policy._entry_protocol(entry)  # type: ignore[attr-defined]
                    for entry in list(ticket.candidate_catalog_entries or [])
                ],
                "selected_candidate_subagent_type": (
                    recommendation.candidate_subagent_type
                    if recommendation is not None
                    else None
                ),
                "selected_protocol": (
                    recommendation.source_protocol
                    if recommendation is not None
                    else None
                ),
                "shadow_candidate_subagent_type": (
                    shadow_recommendation.candidate_subagent_type
                    if shadow_recommendation is not None
                    else None
                ),
                "shadow_protocol": (
                    shadow_recommendation.source_protocol
                    if shadow_recommendation is not None
                    else None
                ),
                "reason": (
                    recommendation.reason
                    if recommendation is not None
                    else ticket.capability_gap.reason
                ),
                "confidence": (
                    recommendation.confidence
                    if recommendation is not None
                    else None
                ),
                "notes": list((recommendation.notes if recommendation is not None else []) or []),
            },
        )
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
        self._record_stage_decision(
            event_name="subagent_tool_discovery_review_decided",
            payload={
                "decision_stage": "approval",
                "ticket_id": ticket.ticket_id,
                "intent": ticket.capability_gap.intent,
                "stage": ticket.capability_gap.stage,
                "capability": ticket.capability_gap.capability,
                "policy_source": "rule",
                "decision": str(approval_decision.get("decision") or "pending"),
                "approved": bool(approval_decision.get("approved")),
                "reviewer": str(approval_decision.get("reviewer") or "system_auto_gate"),
                "source": str(approval_decision.get("source") or "auto_discovery_policy"),
                "selected_candidate_subagent_type": (
                    ticket.candidate_recommendation.candidate_subagent_type
                    if ticket.candidate_recommendation is not None
                    else None
                ),
                "selected_protocol": (
                    ticket.candidate_recommendation.source_protocol
                    if ticket.candidate_recommendation is not None
                    else None
                ),
                "reason": str(
                    approval_decision.get("reason")
                    or "pending_manual_review_discovery_preview"
                ),
                "notes": [
                    str(item).strip()
                    for item in list(approval_decision.get("notes") or [])
                    if str(item).strip()
                ],
            },
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
            self._record_stage_decision(
                event_name="subagent_tool_discovery_activation_decided",
                payload={
                    "decision_stage": "activation",
                    "ticket_id": ticket.ticket_id,
                    "intent": ticket.capability_gap.intent,
                    "stage": ticket.capability_gap.stage,
                    "capability": ticket.capability_gap.capability,
                    "policy_source": "rule",
                    "activate": False,
                    "activation_outcome": "skipped",
                    "candidate_subagent_type": None,
                    "registration_template_id": None,
                    "reason": str(
                        activation_decision.get("reason")
                        or "discovery_auto_activation_disabled"
                    ),
                    "notes": [
                        str(item).strip()
                        for item in list(activation_decision.get("notes") or [])
                        if str(item).strip()
                    ],
                },
            )
            return ticket
        candidate_subagent_type = str(
            activation_decision.get("candidate_subagent_type") or ""
        ).strip() or None
        registration_template_id = str(
            activation_decision.get("registration_template_id") or ""
        ).strip() or None
        try:
            OrchestratorSubagentDiscoveryAdminSupport(self.activation_host).activate_request(
                ticket.ticket_id,
                candidate_subagent_type=candidate_subagent_type,
                registration_template_id=registration_template_id,
                activated_by="system_auto_activate",
                reason=str(
                    activation_decision.get("reason")
                    or "auto_activate_discovery_candidate_preview"
                ),
            )
        except Exception:
            self._record_stage_decision(
                event_name="subagent_tool_discovery_activation_decided",
                payload={
                    "decision_stage": "activation",
                    "ticket_id": ticket.ticket_id,
                    "intent": ticket.capability_gap.intent,
                    "stage": ticket.capability_gap.stage,
                    "capability": ticket.capability_gap.capability,
                    "policy_source": "rule",
                    "activate": True,
                    "activation_outcome": "failed",
                    "candidate_subagent_type": candidate_subagent_type,
                    "registration_template_id": registration_template_id,
                    "reason": str(
                        activation_decision.get("reason")
                        or "auto_activate_discovery_candidate_preview"
                    ),
                    "notes": [
                        str(item).strip()
                        for item in list(activation_decision.get("notes") or [])
                        if str(item).strip()
                    ],
                },
            )
            return ticket
        find = getattr(store, "find", None)
        if not callable(find):
            self._record_stage_decision(
                event_name="subagent_tool_discovery_activation_decided",
                payload={
                    "decision_stage": "activation",
                    "ticket_id": ticket.ticket_id,
                    "intent": ticket.capability_gap.intent,
                    "stage": ticket.capability_gap.stage,
                    "capability": ticket.capability_gap.capability,
                    "policy_source": "rule",
                    "activate": True,
                    "activation_outcome": "activated",
                    "candidate_subagent_type": candidate_subagent_type,
                    "registration_template_id": registration_template_id,
                    "reason": str(
                        activation_decision.get("reason")
                        or "auto_activate_discovery_candidate_preview"
                    ),
                    "notes": [
                        str(item).strip()
                        for item in list(activation_decision.get("notes") or [])
                        if str(item).strip()
                    ],
                },
            )
            return ticket
        located = find(ticket.ticket_id)
        self._record_stage_decision(
            event_name="subagent_tool_discovery_activation_decided",
            payload={
                "decision_stage": "activation",
                "ticket_id": ticket.ticket_id,
                "intent": ticket.capability_gap.intent,
                "stage": ticket.capability_gap.stage,
                "capability": ticket.capability_gap.capability,
                "policy_source": "rule",
                "activate": True,
                "activation_outcome": "activated",
                "candidate_subagent_type": candidate_subagent_type,
                "registration_template_id": registration_template_id,
                "reason": str(
                    activation_decision.get("reason")
                    or "auto_activate_discovery_candidate_preview"
                ),
                "notes": [
                    str(item).strip()
                    for item in list(activation_decision.get("notes") or [])
                    if str(item).strip()
                ],
            },
        )
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

    def _record_stage_decision(
        self,
        *,
        event_name: str,
        payload: dict[str, Any],
    ) -> None:
        binding = dict(self.runtime_binding_getter() or {}) if callable(self.runtime_binding_getter) else {}
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        intent = str(payload.get("intent") or "").strip()
        phase = str(payload.get("stage") or "").strip() or None
        agent = str(binding.get("agent") or self.service_name).strip() or self.service_name
        if context is None or support is None or not request_id or not intent:
            return
        event_payload = {"event": event_name, **dict(payload), "agent": agent}
        decision_rows = getattr(context, "subagent_discovery_stage_decisions", None)
        if not isinstance(decision_rows, list):
            try:
                setattr(context, "subagent_discovery_stage_decisions", [])
            except Exception:
                decision_rows = None
            else:
                decision_rows = getattr(context, "subagent_discovery_stage_decisions", None)
        if isinstance(decision_rows, list):
            decision_rows.append(dict(event_payload))
        dynamic_trace = getattr(context, "dynamic_trace", None)
        if isinstance(dynamic_trace, list):
            dynamic_trace.append(dict(event_payload))
        message = (
            f"{event_name} resolved discovery {payload.get('decision_stage')} "
            f"for capability {payload.get('capability')}"
        )
        if callable(self.emit_event):
            self.emit_event(
                event_type=event_name,
                message=message,
                payload=dict(event_payload),
                phase=phase,
                agent=agent,
            )
            return
        support.emit_event(
            context,
            request_id=request_id,
            intent=intent,
            event_type=event_name,
            phase=phase,
            agent=agent,
            message=message,
            payload=dict(event_payload),
        )


__all__ = ["SubagentDiscoveryController", "SubagentDiscoveryResolution"]
