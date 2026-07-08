from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yaml
from research_system.adapters.vendors.data_source_registry import DataSourceRegistry
from research_system.schemas.subagents import (
    SubagentActivationEntry,
    SubagentCatalogEntry,
    SubagentRuntimeRegistrationTemplate,
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
        registration_template_id: str | None = None,
        activated_by: str = "manual_cli_activation",
        reason: str | None = None,
        registration_type: str | None = None,
        skill_name: str | None = None,
        snapshot_type: str | None = None,
        source_id: str | None = None,
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
                OrchestratorSubagentDiscoveryAdminSupport._normalize_runtime_registration_templates(
                    SubagentCatalogEntry.model_validate(item)
                )
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
            OrchestratorSubagentDiscoveryAdminSupport._normalize_runtime_registration_templates(SubagentCatalogEntry(
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
        endpoint_path: str | None,
        http_method: str | None,
        openapi_spec_uri: str | None,
        openapi_operation_id: str | None,
        timeout_seconds: float | None,
    ) -> SubagentRuntimeRegistration | None:
        template = self._select_runtime_registration_template(
            candidate_entry,
            template_id=registration_template_id,
        )
        template_defaults = dict(template.defaults or {}) if template is not None else {}
        normalized_type = str(registration_type or getattr(template, "registration_type", "") or "").strip()
        if not normalized_type:
            return None
        if normalized_type not in {"snapshot_skill_runner", "skill_runner", "http_json_runner", "openapi_http_runner"}:
            raise self.host.orchestrator_error_cls(f"unsupported runtime registration type: {registration_type}")
        if template is not None and str(template.registration_type).strip() != normalized_type:
            raise self.host.orchestrator_error_cls(
                f"runtime registration template type mismatch: {template.template_id}"
            )
        normalized_skill_name = str(skill_name or template_defaults.get("skill_name") or "").strip()
        normalized_snapshot_type = str(snapshot_type or template_defaults.get("snapshot_type") or "").strip()
        normalized_source_id = str(source_id or template_defaults.get("source_id") or "").strip()
        normalized_endpoint_path = str(endpoint_path or template_defaults.get("endpoint_path") or "").strip()
        normalized_http_method = str(http_method or template_defaults.get("http_method") or "").strip().upper()
        normalized_openapi_spec_uri = str(openapi_spec_uri or template_defaults.get("openapi_spec_uri") or "").strip()
        normalized_openapi_operation_id = str(
            openapi_operation_id or template_defaults.get("openapi_operation_id") or ""
        ).strip()
        resolved_timeout_seconds = timeout_seconds if timeout_seconds is not None else template_defaults.get("timeout_seconds")
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
            self._validate_runtime_contract(candidate_entry, registration_type="skill_runner")
        if normalized_type == "http_json_runner":
            self._validate_http_runtime_registration(
                candidate_entry,
                source_id=normalized_source_id,
                endpoint_path=normalized_endpoint_path,
                http_method=normalized_http_method,
            )
        if normalized_type == "openapi_http_runner":
            resolved_openapi = self._resolve_openapi_runtime_registration(
                candidate_entry,
                source_id=normalized_source_id or None,
                openapi_spec_uri=normalized_openapi_spec_uri or None,
                openapi_operation_id=normalized_openapi_operation_id or None,
            )
            normalized_source_id = resolved_openapi["source_id"]
            normalized_endpoint_path = resolved_openapi["endpoint_path"]
            normalized_http_method = resolved_openapi["http_method"]
            normalized_openapi_spec_uri = resolved_openapi["openapi_spec_uri"]
            normalized_openapi_operation_id = resolved_openapi["openapi_operation_id"]
        if normalized_type in {"snapshot_skill_runner", "skill_runner"}:
            self._validate_skill_name(normalized_skill_name)
        return SubagentRuntimeRegistration(
            registration_type=normalized_type,
            skill_name=normalized_skill_name or None,
            snapshot_type=normalized_snapshot_type or None,
            source_id=normalized_source_id or None,
            endpoint_path=normalized_endpoint_path or None,
            http_method=normalized_http_method or None,
            openapi_spec_uri=normalized_openapi_spec_uri or None,
            openapi_operation_id=normalized_openapi_operation_id or None,
            registration_template_id=template.template_id if template is not None else None,
            timeout_seconds=float(resolved_timeout_seconds) if resolved_timeout_seconds is not None else None,
            notes=[
                *( [f"registration_template_id={template.template_id}"] if template is not None else [] ),
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
                    ["validated_via_openapi_operation_spec"]
                    if normalized_type == "openapi_http_runner"
                    else []
                ),
                *(
                    ["validated_runtime_contract_schema"]
                    if normalized_type in {"skill_runner", "http_json_runner", "openapi_http_runner"}
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

    def _validate_runtime_contract(self, candidate_entry: SubagentCatalogEntry, *, registration_type: str) -> None:
        input_schema = candidate_entry.input_json_schema
        output_schema = candidate_entry.output_json_schema
        if not self._is_object_contract_schema(input_schema):
            raise self.host.orchestrator_error_cls(
                f"{registration_type} activation requires input_json_schema on candidate: {candidate_entry.subagent_type}"
            )
        if not self._is_object_contract_schema(output_schema):
            raise self.host.orchestrator_error_cls(
                f"{registration_type} activation requires output_json_schema on candidate: {candidate_entry.subagent_type}"
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

    def _select_runtime_registration_template(
        self,
        candidate_entry: SubagentCatalogEntry,
        *,
        template_id: str | None,
    ) -> SubagentRuntimeRegistrationTemplate | None:
        normalized_template_id = str(template_id or "").strip()
        if not normalized_template_id:
            return None
        for template in self._candidate_runtime_registration_templates(candidate_entry):
            if str(template.template_id).strip() == normalized_template_id:
                return template
        raise self.host.orchestrator_error_cls(
            f"runtime registration template not found for candidate: {normalized_template_id}"
        )

    @staticmethod
    def _candidate_runtime_registration_templates(
        candidate_entry: SubagentCatalogEntry,
    ) -> list[SubagentRuntimeRegistrationTemplate]:
        templates: list[SubagentRuntimeRegistrationTemplate] = []
        seen_ids: set[str] = set()
        for template in list(candidate_entry.runtime_registration_templates or []):
            if not isinstance(template, SubagentRuntimeRegistrationTemplate):
                continue
            normalized_id = str(template.template_id).strip()
            if not normalized_id or normalized_id in seen_ids:
                continue
            templates.append(template)
            seen_ids.add(normalized_id)
        openapi_hints = OrchestratorSubagentDiscoveryAdminSupport._candidate_openapi_hints(candidate_entry)
        template_id = str(openapi_hints.get("template_id") or "").strip()
        operation_id = str(openapi_hints.get("operation_id") or "").strip()
        if not operation_id:
            operation_id = OrchestratorSubagentDiscoveryAdminSupport._derive_single_openapi_operation_id(
                openapi_hints.get("spec")
            )
        source_id = str(openapi_hints.get("source_id") or "").strip()
        spec_uri = str(openapi_hints.get("spec_uri") or "").strip()
        if operation_id:
            normalized_id = template_id or f"openapi:{operation_id}"
            if normalized_id not in seen_ids:
                defaults: dict[str, Any] = {"openapi_operation_id": operation_id}
                if source_id:
                    defaults["source_id"] = source_id
                if spec_uri:
                    defaults["openapi_spec_uri"] = spec_uri
                templates.append(
                    SubagentRuntimeRegistrationTemplate(
                        template_id=normalized_id,
                        registration_type="openapi_http_runner",
                        summary=f"OpenAPI runtime alias for operation {operation_id}",
                        defaults=defaults,
                        notes=["generated_from_openapi_hints"],
                    )
                )
        return templates

    @staticmethod
    def _derive_single_openapi_operation_id(spec: object) -> str:
        if not isinstance(spec, dict):
            return ""
        operation_ids: list[str] = []
        for path_item in dict(spec.get("paths") or {}).values():
            if not isinstance(path_item, dict):
                continue
            for method_name, operation in path_item.items():
                normalized_method = str(method_name or "").strip().upper()
                if normalized_method not in {"GET", "POST"} or not isinstance(operation, dict):
                    continue
                operation_id = str(operation.get("operationId") or "").strip()
                if operation_id:
                    operation_ids.append(operation_id)
        unique_ids = sorted({item for item in operation_ids if item})
        if len(unique_ids) == 1:
            return unique_ids[0]
        return ""

    @staticmethod
    def _normalize_runtime_registration_templates(
        candidate_entry: SubagentCatalogEntry,
    ) -> SubagentCatalogEntry:
        templates = OrchestratorSubagentDiscoveryAdminSupport._candidate_runtime_registration_templates(candidate_entry)
        return candidate_entry.model_copy(
            update={"runtime_registration_templates": templates},
            deep=True,
        )

    def _resolve_openapi_runtime_registration(
        self,
        candidate_entry: SubagentCatalogEntry,
        *,
        source_id: str | None,
        openapi_spec_uri: str | None,
        openapi_operation_id: str | None,
    ) -> dict[str, str]:
        if candidate_entry.side_effect_level not in {"none", "read_only"}:
            raise self.host.orchestrator_error_cls(
                f"openapi_http_runner activation only supports read-only candidates: {candidate_entry.subagent_type}"
            )
        self._validate_runtime_contract(candidate_entry, registration_type="openapi_http_runner")
        hints = self._candidate_openapi_hints(candidate_entry)
        resolved_source_id = str(source_id or hints.get("source_id") or "").strip()
        resolved_spec_uri = str(openapi_spec_uri or hints.get("spec_uri") or "").strip()
        resolved_operation_id = str(openapi_operation_id or hints.get("operation_id") or "").strip()
        spec_payload = hints.get("spec")
        self._validate_http_source(resolved_source_id, registration_type="openapi_http_runner")
        if not resolved_operation_id:
            raise self.host.orchestrator_error_cls(
                f"openapi_http_runner activation requires operation_id for candidate: {candidate_entry.subagent_type}"
            )
        spec = self._load_openapi_spec(
            spec_uri=resolved_spec_uri or None,
            inline_spec=spec_payload,
        )
        endpoint_path, http_method = self._resolve_openapi_operation(
            spec,
            operation_id=resolved_operation_id,
        )
        return {
            "source_id": resolved_source_id,
            "endpoint_path": endpoint_path,
            "http_method": http_method,
            "openapi_spec_uri": resolved_spec_uri,
            "openapi_operation_id": resolved_operation_id,
        }

    @staticmethod
    def _candidate_openapi_hints(candidate_entry: SubagentCatalogEntry) -> dict[str, Any]:
        hints = candidate_entry.runtime_registration_hints
        if not isinstance(hints, dict):
            return {}
        openapi_hints = hints.get("openapi")
        if isinstance(openapi_hints, dict):
            return dict(openapi_hints)
        return {}

    def _load_openapi_spec(
        self,
        *,
        spec_uri: str | None,
        inline_spec: object,
    ) -> dict[str, Any]:
        if isinstance(inline_spec, dict):
            return dict(inline_spec)
        if not spec_uri:
            raise self.host.orchestrator_error_cls("openapi_http_runner activation requires inline spec or openapi_spec_uri")
        if spec_uri.startswith(("http://", "https://")):
            response = requests.get(spec_uri, timeout=10)
            response.raise_for_status()
            return self._parse_openapi_payload(response.text, origin=spec_uri)
        path = Path(spec_uri)
        if not path.exists():
            raise self.host.orchestrator_error_cls(f"openapi spec file not found: {spec_uri}")
        return self._parse_openapi_payload(path.read_text(encoding="utf-8"), origin=spec_uri)

    def _parse_openapi_payload(self, payload: str, *, origin: str) -> dict[str, Any]:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            parsed = yaml.safe_load(payload) or {}
        if not isinstance(parsed, dict):
            raise self.host.orchestrator_error_cls(f"openapi spec must decode to an object: {origin}")
        return parsed

    def _resolve_openapi_operation(
        self,
        spec: dict[str, Any],
        *,
        operation_id: str,
    ) -> tuple[str, str]:
        for path_name, path_item in dict(spec.get("paths") or {}).items():
            if not isinstance(path_item, dict):
                continue
            for method_name, operation in path_item.items():
                normalized_method = str(method_name or "").strip().upper()
                if normalized_method not in {"GET", "POST"} or not isinstance(operation, dict):
                    continue
                if str(operation.get("operationId") or "").strip() != operation_id:
                    continue
                normalized_path = str(path_name or "").strip()
                if not normalized_path.startswith("/"):
                    raise self.host.orchestrator_error_cls(
                        f"openapi operation path must be relative: {operation_id}"
                    )
                return normalized_path, normalized_method
        raise self.host.orchestrator_error_cls(f"openapi operation not found: {operation_id}")

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
        self._validate_http_source(source_id, registration_type="http_json_runner")
        if not endpoint_path:
            raise self.host.orchestrator_error_cls("endpoint_path is required for http_json_runner activation")
        if endpoint_path.startswith(("http://", "https://")):
            raise self.host.orchestrator_error_cls("endpoint_path must be relative to the approved data source base_url")
        if not endpoint_path.startswith("/"):
            raise self.host.orchestrator_error_cls("endpoint_path must start with '/' for http_json_runner activation")
        if http_method not in {"GET", "POST"}:
            raise self.host.orchestrator_error_cls("http_method must be GET or POST for http_json_runner activation")
        self._validate_runtime_contract(candidate_entry, registration_type="http_json_runner")

    def _validate_http_source(self, source_id: str, *, registration_type: str) -> None:
        if not source_id:
            raise self.host.orchestrator_error_cls(f"source_id is required for {registration_type} activation")
        source = DataSourceRegistry().get(source_id)
        if source is None:
            raise self.host.orchestrator_error_cls(f"unknown source_id for {registration_type} activation: {source_id}")
        if str(source.access).strip().lower() != "http":
            raise self.host.orchestrator_error_cls(
                f"{registration_type} activation requires an http-access data source: {source_id}"
            )
        if bool(source.requires_key):
            raise self.host.orchestrator_error_cls(
                f"{registration_type} activation does not support keyed data sources yet: {source_id}"
            )


__all__ = ["OrchestratorSubagentDiscoveryAdminSupport"]
