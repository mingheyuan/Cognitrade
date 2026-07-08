from __future__ import annotations

from datetime import datetime, timezone

from research_system.adapters.vendors.data_source_registry import DataSourceRegistry
from research_system.schemas.subagents import (
    SubagentActivationEntry,
    SubagentCatalogEntry,
    SubagentRuntimeRegistration,
)


class OrchestratorSubagentDiscoveryAdminSupport:
    def __init__(self, host) -> None:
        self.host = host

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
        ticket.status = "approved" if normalized_decision == "approve" else "rejected"
        ticket.reviewer = reviewer
        ticket.review_reason = str(reason or f"manual_{ticket.status}_via_cli")
        ticket.reviewed_at = reviewed_at
        ticket.updated_at = reviewed_at
        if ticket.status == "approved":
            ticket.candidate_catalog_entries = self._resolve_candidate_catalog_entries(
                ticket,
                candidate_catalog_entries=candidate_catalog_entries,
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
        activated_by: str = "manual_cli_activation",
        reason: str | None = None,
        registration_type: str | None = None,
        skill_name: str | None = None,
        snapshot_type: str | None = None,
        source_id: str | None = None,
        endpoint_path: str | None = None,
        http_method: str | None = None,
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
            registration_type=registration_type,
            skill_name=skill_name,
            snapshot_type=snapshot_type,
            source_id=source_id,
            endpoint_path=endpoint_path,
            http_method=http_method,
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
            "updated_at": ticket.updated_at.isoformat(),
        }

    @staticmethod
    def _resolve_candidate_catalog_entries(
        ticket,
        *,
        candidate_catalog_entries: list[dict] | None,
    ) -> list[SubagentCatalogEntry]:
        if candidate_catalog_entries:
            return [
                SubagentCatalogEntry.model_validate(item)
                for item in candidate_catalog_entries
                if isinstance(item, dict)
            ]
        gap = ticket.capability_gap
        candidate_type = str(gap.preferred_subagent_type or "").strip()
        if not candidate_type:
            stage = str(gap.stage or "phase").strip() or "phase"
            capability = str(gap.capability or "capability").strip() or "capability"
            candidate_type = f"candidate_{stage}_{capability}".replace("-", "_").replace(" ", "_")
        summary = f"Approved discovery candidate for capability {gap.capability}"
        suitable_intents = [str(gap.intent).strip()] if str(gap.intent or "").strip() else []
        return [
            SubagentCatalogEntry(
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
            )
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
        raise self.host.orchestrator_error_cls(
            f"multiple candidate catalog entries available; candidate_subagent_type is required for {ticket.ticket_id}"
        )

    def _resolve_runtime_registration(
        self,
        *,
        candidate_entry: SubagentCatalogEntry,
        registration_type: str | None,
        skill_name: str | None,
        snapshot_type: str | None,
        source_id: str | None,
        endpoint_path: str | None,
        http_method: str | None,
        timeout_seconds: float | None,
    ) -> SubagentRuntimeRegistration | None:
        normalized_type = str(registration_type or "").strip()
        if not normalized_type:
            return None
        if normalized_type not in {"snapshot_skill_runner", "skill_runner", "http_json_runner"}:
            raise self.host.orchestrator_error_cls(f"unsupported runtime registration type: {registration_type}")
        normalized_skill_name = str(skill_name or "").strip()
        normalized_snapshot_type = str(snapshot_type or "").strip()
        normalized_source_id = str(source_id or "").strip()
        normalized_endpoint_path = str(endpoint_path or "").strip()
        normalized_http_method = str(http_method or "").strip().upper()
        normalized_stage = str(candidate_entry.stage or "").strip().lower()
        if normalized_type == "snapshot_skill_runner" and normalized_stage not in {"", "collect"}:
            raise self.host.orchestrator_error_cls(
                f"snapshot_skill_runner activation is only supported for collect-stage candidates: {candidate_entry.subagent_type}"
            )
        if normalized_type in {"snapshot_skill_runner", "skill_runner"} and not normalized_skill_name:
            raise self.host.orchestrator_error_cls(f"skill_name is required for {normalized_type} activation")
        if normalized_type == "snapshot_skill_runner" and not normalized_snapshot_type:
            raise self.host.orchestrator_error_cls("snapshot_type is required for snapshot_skill_runner activation")
        if normalized_type == "skill_runner":
            self._validate_runtime_contract(candidate_entry)
        if normalized_type == "http_json_runner":
            self._validate_http_runtime_registration(
                candidate_entry,
                source_id=normalized_source_id,
                endpoint_path=normalized_endpoint_path,
                http_method=normalized_http_method,
            )
        else:
            self._validate_skill_name(normalized_skill_name)
        return SubagentRuntimeRegistration(
            registration_type=normalized_type,
            skill_name=normalized_skill_name or None,
            snapshot_type=normalized_snapshot_type or None,
            source_id=normalized_source_id or None,
            endpoint_path=normalized_endpoint_path or None,
            http_method=normalized_http_method or None,
            timeout_seconds=float(timeout_seconds) if timeout_seconds is not None else None,
            notes=[
                *(
                    ["validated_via_skill_runtime_registry"]
                    if normalized_type in {"snapshot_skill_runner", "skill_runner"}
                    else []
                ),
                *(
                    ["validated_via_data_source_registry", "validated_http_json_endpoint"]
                    if normalized_type == "http_json_runner"
                    else []
                ),
                *(
                    ["validated_runtime_contract_schema"]
                    if normalized_type in {"skill_runner", "http_json_runner"}
                    else []
                ),
            ],
        )

    def _validate_skill_name(self, skill_name: str) -> None:
        runtime = self.host.require_skill_runtime()
        registry = getattr(getattr(runtime, "backend", None), "registry", None)
        if not isinstance(registry, dict):
            raise self.host.orchestrator_error_cls("skill_runtime registry is not available for validation")
        if skill_name not in registry:
            raise self.host.orchestrator_error_cls(f"unknown skill_name for runtime registration: {skill_name}")

    def _validate_runtime_contract(self, candidate_entry: SubagentCatalogEntry) -> None:
        input_schema = candidate_entry.input_json_schema
        output_schema = candidate_entry.output_json_schema
        if not self._is_object_contract_schema(input_schema):
            raise self.host.orchestrator_error_cls(
                f"skill_runner activation requires input_json_schema on candidate: {candidate_entry.subagent_type}"
            )
        if not self._is_object_contract_schema(output_schema):
            raise self.host.orchestrator_error_cls(
                f"skill_runner activation requires output_json_schema on candidate: {candidate_entry.subagent_type}"
            )

    @staticmethod
    def _is_object_contract_schema(schema: object) -> bool:
        if not isinstance(schema, dict):
            return False
        normalized_type = str(schema.get("type") or "").strip().lower()
        properties = schema.get("properties")
        if normalized_type and normalized_type != "object":
            return False
        return isinstance(properties, dict)

    def _validate_http_runtime_registration(
        self,
        candidate_entry: SubagentCatalogEntry,
        *,
        source_id: str,
        endpoint_path: str,
        http_method: str,
    ) -> None:
        if candidate_entry.side_effect_level not in {"none", "read_only"}:
            raise self.host.orchestrator_error_cls(
                f"http_json_runner activation only supports read-only candidates: {candidate_entry.subagent_type}"
            )
        if not source_id:
            raise self.host.orchestrator_error_cls("source_id is required for http_json_runner activation")
        source = DataSourceRegistry().get(source_id)
        if source is None:
            raise self.host.orchestrator_error_cls(f"unknown source_id for http_json_runner activation: {source_id}")
        if str(source.access).strip().lower() != "http":
            raise self.host.orchestrator_error_cls(
                f"http_json_runner activation requires an http-access data source: {source_id}"
            )
        if bool(source.requires_key):
            raise self.host.orchestrator_error_cls(
                f"http_json_runner activation does not support keyed data sources yet: {source_id}"
            )
        if not endpoint_path:
            raise self.host.orchestrator_error_cls("endpoint_path is required for http_json_runner activation")
        if endpoint_path.startswith(("http://", "https://")):
            raise self.host.orchestrator_error_cls("endpoint_path must be relative to the approved data source base_url")
        if not endpoint_path.startswith("/"):
            raise self.host.orchestrator_error_cls("endpoint_path must start with '/' for http_json_runner activation")
        if http_method not in {"GET", "POST"}:
            raise self.host.orchestrator_error_cls("http_method must be GET or POST for http_json_runner activation")
        self._validate_runtime_contract(candidate_entry)


__all__ = ["OrchestratorSubagentDiscoveryAdminSupport"]
