from __future__ import annotations

from datetime import datetime, timezone
from research_system.runtime.orchestration._subagent_discovery_candidate_policy import (
    SubagentDiscoveryCandidatePolicyService,
)
from research_system.schemas.subagents import (
    SubagentActivationEntry,
    SubagentCatalogEntry,
    SubagentDiscoveryReviewEvent,
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
        reviewed_at = datetime.now(timezone.utc)
        previous_status = str(ticket.status or "pending").strip() or "pending"
        ticket.status = "approved" if normalized_decision == "approve" else "rejected"
        ticket.reviewer = reviewer
        ticket.review_reason = str(reason or f"manual_{ticket.status}_via_cli")
        ticket.reviewed_at = reviewed_at
        ticket.updated_at = reviewed_at
        history = list(ticket.review_history or [])
        history.append(
            SubagentDiscoveryReviewEvent(
                decision=ticket.status,
                reviewer=reviewer,
                reason=ticket.review_reason,
                reviewed_at=reviewed_at,
                previous_status=previous_status,  # type: ignore[arg-type]
                source="manual_cli_review",
            )
        )
        ticket.review_history = history
        if ticket.status == "approved":
            ticket.candidate_catalog_entries = self._resolve_candidate_catalog_entries(
                ticket,
                candidate_catalog_entries=candidate_catalog_entries,
            )
            (
                ticket.candidate_recommendation,
                ticket.candidate_shadow_recommendation,
            ) = self.candidate_policy.recommend(
                ticket=ticket,
                candidate_entries=ticket.candidate_catalog_entries,
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
        candidate_entry = self._select_candidate_catalog_entry(
            ticket,
            candidate_subagent_type=candidate_subagent_type,
        )
        runtime_registration = self._resolve_runtime_registration(
            candidate_entry=candidate_entry,
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
        )
        normalized_delegate_subagent_type = str(delegate_subagent_type or "").strip() or None
        if bool(normalized_delegate_subagent_type) == bool(runtime_registration):
            raise self.host.orchestrator_error_cls(
                "activation requires exactly one of delegate_subagent_type or runtime registration"
            )
        for existing in ticket.activated_entries:
            if str(existing.subagent_type).strip() == str(candidate_entry.subagent_type).strip():
                current = self.show_request(ticket.ticket_id)
                return {
                    "ticket_id": ticket.ticket_id,
                    "status": ticket.status,
                    "message": f"activation already exists for {candidate_entry.subagent_type}",
                    "result": current,
                }
        ticket.activated_entries.append(
            SubagentActivationEntry(
                ticket_id=ticket.ticket_id,
                subagent_type=candidate_entry.subagent_type,
                delegate_subagent_type=normalized_delegate_subagent_type,
                runtime_registration=runtime_registration,
                catalog_entry=candidate_entry,
                activated_by=activated_by,
                activation_reason=str(reason or f"activate_{candidate_entry.subagent_type}_via_cli"),
                notes=[
                    "activated_from_approved_discovery_ticket",
                    *( [f"delegate_subagent_type={normalized_delegate_subagent_type}"] if normalized_delegate_subagent_type else [] ),
                    *( [f"runtime_registration_type={runtime_registration.registration_type}"] if runtime_registration else [] ),
                    *( [f"runtime_registration_template_id={runtime_registration.registration_template_id}"] if runtime_registration and runtime_registration.registration_template_id else [] ),
                ],
            )
        )
        ticket.updated_at = datetime.now(timezone.utc)
        self.host.require_subagent_discovery_store().save(ticket, previous_status=previous_status)
        activation_message = (
            f"activated discovery runtime alias {candidate_entry.subagent_type}"
            if runtime_registration is not None
            else f"activated discovery alias {candidate_entry.subagent_type}"
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

    def _resolve_candidate_catalog_entries(
        self,
        ticket,
        *,
        candidate_catalog_entries: list[dict] | None,
    ) -> list[SubagentCatalogEntry]:
        if candidate_catalog_entries:
            return [
                self._normalize_runtime_registration_templates(SubagentCatalogEntry.model_validate(item))
                for item in candidate_catalog_entries
                if isinstance(item, dict)
            ]
        if ticket.candidate_catalog_entries:
            return [
                self._normalize_runtime_registration_templates(entry)
                for entry in list(ticket.candidate_catalog_entries or [])
                if isinstance(entry, SubagentCatalogEntry)
            ]
        generated_entries = self.discovery_candidates.candidate_entries(ticket)
        if generated_entries:
            return generated_entries
        gap = ticket.capability_gap
        candidate_type = str(gap.preferred_subagent_type or "").strip()
        if not candidate_type:
            stage = str(gap.stage or "phase").strip() or "phase"
            capability = str(gap.capability or "capability").strip() or "capability"
            candidate_type = f"candidate_{stage}_{capability}".replace("-", "_").replace(" ", "_")
        summary = f"Approved discovery candidate for capability {gap.capability}"
        suitable_intents = [str(gap.intent).strip()] if str(gap.intent or "").strip() else []
        return [
            self._normalize_runtime_registration_templates(SubagentCatalogEntry(
                subagent_type=candidate_type,
                summary=summary,
                capabilities=[str(gap.capability).strip()],
                stage=str(gap.stage or "").strip() or None,
                cost_class="medium",
                latency_class="medium",
                side_effect_level="none",
                suitable_intents=suitable_intents,
                tags=["approved_discovery_candidate", "pending_runtime_registration"],
                requires_llm=bool(gap.requires_llm),
                allow_parallel=False,
                max_concurrency=None,
                fallback_subagent_type=None,
                error_fallback_subagent_type=None,
                input_schema_name=None,
                output_schema_name=None,
                input_json_schema=None,
                output_json_schema=None,
            ))
        ]

    def _select_candidate_catalog_entry(
        self,
        ticket,
        *,
        candidate_subagent_type: str | None,
    ) -> SubagentCatalogEntry:
        entries = list(ticket.candidate_catalog_entries or [])
        if not entries:
            raise self.host.orchestrator_error_cls(
                f"subagent discovery ticket has no candidate catalog entries: {ticket.ticket_id}"
            )
        requested = str(candidate_subagent_type or "").strip()
        if requested:
            for entry in entries:
                if str(entry.subagent_type).strip() == requested:
                    return entry
            raise self.host.orchestrator_error_cls(
                f"candidate catalog entry not found for activation: {requested}"
            )
        if len(entries) == 1:
            return entries[0]
        recommended = ticket.candidate_recommendation
        if recommended is not None:
            recommended_entry = self._find_candidate_entry(
                entries,
                recommended.candidate_subagent_type,
            )
            if recommended_entry is not None:
                return recommended_entry
        raise self.host.orchestrator_error_cls(
            f"multiple candidate catalog entries available; candidate_subagent_type is required for {ticket.ticket_id}"
        )

    def _resolve_runtime_registration(
        self,
        *,
        candidate_entry: SubagentCatalogEntry,
        registration_template_id: str | None,
        registration_type: str | None,
        skill_name: str | None,
        snapshot_type: str | None,
        source_id: str | None,
        tool_name: str | None,
        endpoint_path: str | None,
        http_method: str | None,
        openapi_spec_uri: str | None,
        openapi_operation_id: str | None,
        timeout_seconds: float | None,
    ):
        return self.runtime_packaging.resolve_runtime_registration(
            candidate_entry=candidate_entry,
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
        )

    def _select_runtime_registration_template(
        self,
        candidate_entry: SubagentCatalogEntry,
        *,
        template_id: str | None,
    ):
        return self.runtime_packaging.select_template(candidate_entry, template_id=template_id)

    @staticmethod
    def _candidate_runtime_registration_templates(
        candidate_entry: SubagentCatalogEntry,
    ):
        support = SubagentRuntimePackagingSupport()
        return support.candidate_templates(candidate_entry)

    @staticmethod
    def _normalize_runtime_registration_templates(
        candidate_entry: SubagentCatalogEntry,
    ) -> SubagentCatalogEntry:
        support = SubagentRuntimePackagingSupport()
        return support.normalize_candidate_entry(candidate_entry)

    @staticmethod
    def _find_candidate_entry(
        entries: list[SubagentCatalogEntry],
        candidate_subagent_type: str,
    ) -> SubagentCatalogEntry | None:
        requested = str(candidate_subagent_type or "").strip()
        for entry in entries:
            if str(entry.subagent_type).strip() == requested:
                return entry
        return None


__all__ = ["OrchestratorSubagentDiscoveryAdminSupport"]
