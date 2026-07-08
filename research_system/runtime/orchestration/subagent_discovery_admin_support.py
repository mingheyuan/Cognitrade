from __future__ import annotations

from research_system.agents._subagent_discovery_decision_source import (
    SubagentDiscoveryDecisionSource,
)
from research_system.agents._subagent_discovery_policy import (
    SubagentDiscoveryPolicyService,
)
from research_system.agents._subagent_discovery_policy_source import (
    SubagentDiscoveryLLMPolicySource,
)
from research_system.agents._subagent_discovery_shadow_policy import (
    SubagentDiscoveryLLMShadowPolicy,
)
from research_system.runtime.orchestration._subagent_discovery_activation_policy import (
    SubagentDiscoveryActivationPolicyService,
)
from research_system.runtime.orchestration._subagent_discovery_approval_policy import (
    SubagentDiscoveryApprovalPolicyService,
)
from research_system.runtime.orchestration._subagent_discovery_candidate_policy import (
    SubagentDiscoveryCandidatePolicyService,
)
from research_system.runtime.orchestration.subagent_discovery_candidate_sources import (
    SubagentDiscoveryCandidateSupport,
)
from research_system.runtime.orchestration.subagent_runtime_packaging import SubagentRuntimePackagingSupport


class OrchestratorSubagentDiscoveryAdminSupport:
    def __init__(self, host) -> None:
        self.host = host
        self.discovery_candidates = SubagentDiscoveryCandidateSupport(host)
        self.candidate_policy = SubagentDiscoveryCandidatePolicyService(
            llm_provider=getattr(host, "llm_provider", None),
            settings=getattr(host, "settings", None),
            runtime_binding_getter=getattr(host, "runtime_binding_getter", None),
            emit_event=getattr(host, "emit_event", None),
            service_name="subagent_discovery_admin",
        )
        self.discovery_decision_source = SubagentDiscoveryDecisionSource(
            discovery_policy=SubagentDiscoveryPolicyService(settings=getattr(host, "settings", None)),
            discovery_llm_policy_source=SubagentDiscoveryLLMPolicySource(
                shadow_policy=SubagentDiscoveryLLMShadowPolicy(
                    llm_provider=getattr(host, "llm_provider", None),
                    settings=getattr(host, "settings", None),
                    runtime_binding_getter=getattr(host, "runtime_binding_getter", None),
                    emit_event=getattr(host, "emit_event", None),
                    service_name="subagent_discovery_admin",
                ),
                settings=getattr(host, "settings", None),
                runtime_binding_getter=getattr(host, "runtime_binding_getter", None),
                emit_event=getattr(host, "emit_event", None),
            ),
            discovery_candidate_policy=self.candidate_policy,
            discovery_approval_policy=SubagentDiscoveryApprovalPolicyService(settings=getattr(host, "settings", None)),
            discovery_activation_policy=SubagentDiscoveryActivationPolicyService(settings=getattr(host, "settings", None)),
            discovery_candidate_support=self.discovery_candidates,
        )
        self.runtime_packaging = SubagentRuntimePackagingSupport(host)

    def list_requests(self, *, status: str | None = None, limit: int = 50) -> list[dict]:
        store = self.host.require_subagent_discovery_store()
        if status:
            tickets = store.list(status)
            tickets.sort(key=lambda item: item.updated_at, reverse=True)
        else:
            tickets = store.list_all()
        return [self._summary_row(ticket) for ticket in tickets[:limit]]

    def show_request(self, ticket_id: str) -> dict:
        located = self.host.require_subagent_discovery_store().find(ticket_id)
        if located is None:
            raise self.host.orchestrator_error_cls(f"subagent discovery ticket not found: {ticket_id}")
        ticket, stored_status = located
        payload = ticket.model_dump(mode="json")
        payload["stored_status"] = stored_status
        return payload

    def review_request(
        self,
        ticket_id: str,
        *,
        decision: str,
        reviewer: str = "manual_cli_review",
        reason: str | None = None,
        candidate_catalog_entries: list[dict] | None = None,
    ) -> dict:
        normalized_decision = str(decision or "").strip().lower()
        if normalized_decision not in {"approve", "reject"}:
            raise self.host.orchestrator_error_cls(f"unsupported discovery review decision: {decision}")
        located = self.host.require_subagent_discovery_store().find(ticket_id)
        if located is None:
            raise self.host.orchestrator_error_cls(f"subagent discovery ticket not found: {ticket_id}")
        ticket, previous_status = located
        if ticket.status in {"approved", "rejected"}:
            current = self.show_request(ticket.ticket_id)
            return {
                "ticket_id": ticket.ticket_id,
                "status": ticket.status,
                "message": f"discovery ticket already {ticket.status}",
                "result": current,
            }
        previous_status = str(ticket.status or "pending").strip() or "pending"
        if normalized_decision == "approve":
            candidate_result = self.discovery_decision_source.select_candidate(
                ticket=ticket,
                candidate_entries=candidate_catalog_entries,
            )
            self.discovery_decision_source.apply_candidate_selection(
                ticket=ticket,
                candidate_result=candidate_result,
            )
        review_decision = self.discovery_decision_source.build_manual_review(
            ticket=ticket,
            decision=normalized_decision,
            reviewer=reviewer,
            reason=str(reason or ""),
            source="manual_cli_review",
        )
        previous_status = self.discovery_decision_source.apply_review(
            ticket=ticket,
            review_decision=review_decision,
        )
        self.host.require_subagent_discovery_store().save(ticket, previous_status=previous_status)
        return {
            "ticket_id": ticket.ticket_id,
            "status": ticket.status,
            "message": f"discovery ticket {ticket.status}",
            "result": self.show_request(ticket.ticket_id),
        }

    def activate_request(
        self,
        ticket_id: str,
        *,
        delegate_subagent_type: str | None = None,
        candidate_subagent_type: str | None = None,
        registration_template_id: str | None = None,
        activated_by: str = "manual_cli_activation",
        reason: str | None = None,
        registration_type: str | None = None,
        skill_name: str | None = None,
        snapshot_type: str | None = None,
        source_id: str | None = None,
        tool_name: str | None = None,
        endpoint_path: str | None = None,
        http_method: str | None = None,
        openapi_spec_uri: str | None = None,
        openapi_operation_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict:
        located = self.host.require_subagent_discovery_store().find(ticket_id)
        if located is None:
            raise self.host.orchestrator_error_cls(f"subagent discovery ticket not found: {ticket_id}")
        ticket, previous_status = located
        if ticket.status != "approved":
            raise self.host.orchestrator_error_cls(
                f"subagent discovery ticket must be approved before activation: {ticket.status}"
            )
        activation_target = self.discovery_decision_source.resolve_activation_target(
            ticket=ticket,
            candidate_subagent_type=candidate_subagent_type,
            delegate_subagent_type=delegate_subagent_type,
            registration_template_id=registration_template_id,
            registration_type=registration_type,
            skill_name=skill_name,
            snapshot_type=snapshot_type,
            source_id=source_id,
            tool_name=tool_name,
            endpoint_path=endpoint_path,
            http_method=http_method,
            openapi_spec_uri=openapi_spec_uri,
            openapi_operation_id=openapi_operation_id,
            timeout_seconds=timeout_seconds,
            runtime_packaging=self.runtime_packaging,
            error_factory=self.host.orchestrator_error_cls,
        )
        activation_decision = self.discovery_decision_source.build_manual_activation(
            ticket=ticket,
            activation_target=activation_target,
            activated_by=activated_by,
            reason=reason,
        )
        _activation_entry, created = self.discovery_decision_source.apply_activation(
            ticket=ticket,
            activation_decision=activation_decision,
            candidate_entry=activation_target.candidate_entry,
        )
        if not created:
            current = self.show_request(ticket.ticket_id)
            return {
                "ticket_id": ticket.ticket_id,
                "status": ticket.status,
                "message": f"activation already exists for {activation_target.candidate_entry.subagent_type}",
                "result": current,
            }
        self.host.require_subagent_discovery_store().save(ticket, previous_status=previous_status)
        activation_message = (
            f"activated discovery runtime alias {activation_target.candidate_entry.subagent_type}"
            if activation_target.runtime_registration is not None
            else f"activated discovery alias {activation_target.candidate_entry.subagent_type}"
        )
        return {
            "ticket_id": ticket.ticket_id,
            "status": ticket.status,
            "message": activation_message,
            "result": self.show_request(ticket.ticket_id),
        }

    @staticmethod
    def _summary_row(ticket) -> dict:
        return {
            "ticket_id": ticket.ticket_id,
            "status": ticket.status,
            "intent": ticket.capability_gap.intent,
            "stage": ticket.capability_gap.stage,
            "capability": ticket.capability_gap.capability,
            "search_query": ticket.tool_discovery_request.search_query,
            "candidate_entry_count": len(ticket.candidate_catalog_entries),
            "activation_entry_count": len(ticket.activated_entries),
            "recommended_candidate_subagent_type": (
                ticket.candidate_recommendation.candidate_subagent_type
                if ticket.candidate_recommendation is not None
                else None
            ),
            "updated_at": ticket.updated_at.isoformat(),
        }

__all__ = ["OrchestratorSubagentDiscoveryAdminSupport"]
