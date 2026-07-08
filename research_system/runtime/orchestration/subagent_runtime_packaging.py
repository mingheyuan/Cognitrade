from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import requests
import yaml

from research_system.adapters.vendors.data_source_registry import DataSourceRegistry
from research_system.schemas.subagent_runtime_registration import SUPPORTED_RUNTIME_REGISTRATION_TYPES
from research_system.schemas.subagent_runtime_registration_hints import runtime_registration_hint_payload
from research_system.schemas.subagents import (
    SubagentActivationEntry,
    SubagentCatalogEntry,
    SubagentRuntimeRegistration,
    SubagentRuntimeRegistrationTemplate,
)
from research_system.subagents.base import SubagentCatalogMetadata, SubagentRuntimePolicy
from research_system.subagents.runtime_registered import (
    RuntimeRegisteredHttpJsonSubagent,
    RuntimeRegisteredSkillSubagent,
    RuntimeRegisteredSnapshotSkillSubagent,
    build_runtime_registered_model,
)


@dataclass(frozen=True)
class _RuntimeRegistrationHandler:
    registration_type: str
    template_builder_name: str
    registration_builder_name: str
    materializer_name: str


_RUNTIME_REGISTRATION_HANDLERS = (
    _RuntimeRegistrationHandler(
        registration_type="snapshot_skill_runner",
        template_builder_name="_snapshot_skill_template",
        registration_builder_name="_build_snapshot_skill_registration",
        materializer_name="_materialize_snapshot_skill_subagent",
    ),
    _RuntimeRegistrationHandler(
        registration_type="skill_runner",
        template_builder_name="_skill_template",
        registration_builder_name="_build_skill_registration",
        materializer_name="_materialize_skill_subagent",
    ),
    _RuntimeRegistrationHandler(
        registration_type="http_json_runner",
        template_builder_name="_http_json_template",
        registration_builder_name="_build_http_json_registration",
        materializer_name="_materialize_http_json_subagent",
    ),
    _RuntimeRegistrationHandler(
        registration_type="openapi_http_runner",
        template_builder_name="_openapi_template",
        registration_builder_name="_build_openapi_http_registration",
        materializer_name="_materialize_http_json_subagent",
    ),
)


class SubagentRuntimePackagingSupport:
    def __init__(self, host=None) -> None:
        self.host = host
        self._handlers = {handler.registration_type: handler for handler in _RUNTIME_REGISTRATION_HANDLERS}

    def normalize_candidate_entry(self, candidate_entry: SubagentCatalogEntry) -> SubagentCatalogEntry:
        return candidate_entry.model_copy(
            update={"runtime_registration_templates": self.candidate_templates(candidate_entry)},
            deep=True,
        )

    def registration_types(self) -> tuple[str, ...]:
        return tuple(self._handlers)

    def materialize_runtime_subagent(
        self,
        *,
        activation_entry: SubagentActivationEntry,
        skill_runtime=None,
    ):
        runtime_registration = activation_entry.runtime_registration
        if runtime_registration is None:
            return None
        handler = self._handler(runtime_registration.registration_type, allow_missing=True)
        if handler is None:
            return None
        catalog_entry = activation_entry.catalog_entry
        return getattr(self, handler.materializer_name)(
            activation_entry=activation_entry,
            skill_runtime=skill_runtime,
            runtime_policy=self.runtime_policy(catalog_entry),
            catalog_metadata=self.catalog_metadata(catalog_entry),
            runtime_models=self.runtime_models(
                activation_entry=activation_entry,
                catalog_entry=catalog_entry,
            ),
        )

    @staticmethod
    def runtime_policy(catalog_entry: SubagentCatalogEntry) -> SubagentRuntimePolicy:
        return SubagentRuntimePolicy(
            requires_llm=bool(catalog_entry.requires_llm),
            fallback_subagent_type=catalog_entry.fallback_subagent_type,
            error_fallback_subagent_type=catalog_entry.error_fallback_subagent_type,
            allow_parallel=bool(catalog_entry.allow_parallel),
            max_concurrency=catalog_entry.max_concurrency,
        )

    @staticmethod
    def catalog_metadata(catalog_entry: SubagentCatalogEntry) -> SubagentCatalogMetadata:
        return SubagentCatalogMetadata(
            summary=catalog_entry.summary,
            capabilities=tuple(item for item in catalog_entry.capabilities if str(item).strip()),
            stage=catalog_entry.stage,
            cost_class=catalog_entry.cost_class,
            latency_class=catalog_entry.latency_class,
            side_effect_level=catalog_entry.side_effect_level,
            suitable_intents=tuple(item for item in catalog_entry.suitable_intents if str(item).strip()),
            tags=tuple(item for item in catalog_entry.tags if str(item).strip()),
        )

    @staticmethod
    def runtime_models(
        *,
        activation_entry: SubagentActivationEntry,
        catalog_entry: SubagentCatalogEntry,
    ) -> tuple[type | None, type | None]:
        input_model = build_runtime_registered_model(
            model_name=f"{activation_entry.subagent_type}_input",
            schema=catalog_entry.input_json_schema,
        )
        output_model = build_runtime_registered_model(
            model_name=f"{activation_entry.subagent_type}_output",
            schema=catalog_entry.output_json_schema,
        )
        return input_model, output_model

    def candidate_templates(self, candidate_entry: SubagentCatalogEntry) -> list[SubagentRuntimeRegistrationTemplate]:
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
        for handler in self._handlers.values():
            template = getattr(self, handler.template_builder_name)(candidate_entry)
            if template is None:
                continue
            normalized_id = str(template.template_id).strip()
            if not normalized_id or normalized_id in seen_ids:
                continue
            templates.append(template)
            seen_ids.add(normalized_id)
        return templates

    def select_template(
        self,
        candidate_entry: SubagentCatalogEntry,
        *,
        template_id: str | None,
    ) -> SubagentRuntimeRegistrationTemplate | None:
        normalized_template_id = str(template_id or "").strip()
        if not normalized_template_id:
            return None
        for template in self.candidate_templates(candidate_entry):
            if str(template.template_id).strip() == normalized_template_id:
                return template
        raise self.host.orchestrator_error_cls(
            f"runtime registration template not found for candidate: {normalized_template_id}"
        )

    def resolve_runtime_registration(
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
        template = self.select_template(candidate_entry, template_id=registration_template_id)
        template_defaults = dict(template.defaults or {}) if template is not None else {}
        normalized_type = str(registration_type or getattr(template, "registration_type", "") or "").strip()
        if not normalized_type:
            return None
        handler = self._handler(normalized_type)
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
        resolved_timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else template_defaults.get("timeout_seconds")
        )
        return getattr(self, handler.registration_builder_name)(
            candidate_entry=candidate_entry,
            template=template,
            skill_name=normalized_skill_name,
            snapshot_type=normalized_snapshot_type,
            source_id=normalized_source_id or None,
            endpoint_path=normalized_endpoint_path,
            http_method=normalized_http_method,
            openapi_spec_uri=normalized_openapi_spec_uri or None,
            openapi_operation_id=normalized_openapi_operation_id or None,
            timeout_seconds=resolved_timeout_seconds,
        )

    def _handler(self, registration_type: str, *, allow_missing: bool = False) -> _RuntimeRegistrationHandler | None:
        normalized_type = str(registration_type or "").strip()
        handler = self._handlers.get(normalized_type)
        if handler is not None or allow_missing:
            return handler
        raise self.host.orchestrator_error_cls(f"unsupported runtime registration type: {registration_type}")

    def _materialize_snapshot_skill_subagent(
        self,
        *,
        activation_entry: SubagentActivationEntry,
        skill_runtime,
        runtime_policy: SubagentRuntimePolicy,
        catalog_metadata: SubagentCatalogMetadata,
        runtime_models: tuple[type | None, type | None],
    ):
        runtime_registration = activation_entry.runtime_registration
        if runtime_registration is None or skill_runtime is None:
            return None
        catalog_entry = activation_entry.catalog_entry
        if str(catalog_entry.stage or "").strip().lower() not in {"", "collect"}:
            return None
        return RuntimeRegisteredSnapshotSkillSubagent(
            subagent_type=str(activation_entry.subagent_type).strip(),
            snapshot_type=str(runtime_registration.snapshot_type or "").strip(),
            skill_name=str(runtime_registration.skill_name).strip(),
            skill_runtime=skill_runtime,
            runtime_policy=runtime_policy,
            catalog_metadata=catalog_metadata,
        )

    def _materialize_skill_subagent(
        self,
        *,
        activation_entry: SubagentActivationEntry,
        skill_runtime,
        runtime_policy: SubagentRuntimePolicy,
        catalog_metadata: SubagentCatalogMetadata,
        runtime_models: tuple[type | None, type | None],
    ):
        runtime_registration = activation_entry.runtime_registration
        if runtime_registration is None or skill_runtime is None:
            return None
        input_model, output_model = runtime_models
        return RuntimeRegisteredSkillSubagent(
            subagent_type=str(activation_entry.subagent_type).strip(),
            skill_name=str(runtime_registration.skill_name).strip(),
            skill_runtime=skill_runtime,
            input_model=input_model,
            output_model=output_model,
            runtime_policy=runtime_policy,
            catalog_metadata=catalog_metadata,
        )

    def _materialize_http_json_subagent(
        self,
        *,
        activation_entry: SubagentActivationEntry,
        skill_runtime,
        runtime_policy: SubagentRuntimePolicy,
        catalog_metadata: SubagentCatalogMetadata,
        runtime_models: tuple[type | None, type | None],
    ):
        runtime_registration = activation_entry.runtime_registration
        if runtime_registration is None:
            return None
        source_id = str(runtime_registration.source_id or "").strip()
        data_source = DataSourceRegistry().get(source_id)
        if data_source is None or str(data_source.access).strip().lower() != "http":
            return None
        input_model, output_model = runtime_models
        return RuntimeRegisteredHttpJsonSubagent(
            subagent_type=str(activation_entry.subagent_type).strip(),
            source_id=source_id,
            base_url=str(data_source.base_url).strip(),
            endpoint_path=str(runtime_registration.endpoint_path or "").strip(),
            http_method=str(runtime_registration.http_method or "").strip().upper() or "GET",
            timeout_seconds=float(runtime_registration.timeout_seconds or 20.0),
            input_model=input_model,
            output_model=output_model,
            runtime_policy=runtime_policy,
            catalog_metadata=catalog_metadata,
        )

    def _build_snapshot_skill_registration(
        self,
        *,
        candidate_entry: SubagentCatalogEntry,
        template: SubagentRuntimeRegistrationTemplate | None,
        skill_name: str,
        snapshot_type: str,
        **_ignored: Any,
    ) -> SubagentRuntimeRegistration:
        normalized_stage = str(candidate_entry.stage or "").strip().lower()
        if normalized_stage not in {"", "collect"}:
            raise self.host.orchestrator_error_cls(
                f"snapshot_skill_runner activation is only supported for collect-stage candidates: {candidate_entry.subagent_type}"
            )
        if not skill_name:
            raise self.host.orchestrator_error_cls("skill_name is required for snapshot_skill_runner activation")
        if not snapshot_type:
            raise self.host.orchestrator_error_cls("snapshot_type is required for snapshot_skill_runner activation")
        self._validate_skill_name(skill_name)
        return SubagentRuntimeRegistration(
            registration_type="snapshot_skill_runner",
            skill_name=skill_name,
            snapshot_type=snapshot_type,
            registration_template_id=template.template_id if template is not None else None,
            notes=[
                *([f"registration_template_id={template.template_id}"] if template is not None else []),
                "validated_via_skill_runtime_registry",
            ],
        )

    def _build_skill_registration(
        self,
        *,
        candidate_entry: SubagentCatalogEntry,
        template: SubagentRuntimeRegistrationTemplate | None,
        skill_name: str,
        **_ignored: Any,
    ) -> SubagentRuntimeRegistration:
        if not skill_name:
            raise self.host.orchestrator_error_cls("skill_name is required for skill_runner activation")
        self._validate_runtime_contract(candidate_entry, registration_type="skill_runner")
        self._validate_skill_name(skill_name)
        return SubagentRuntimeRegistration(
            registration_type="skill_runner",
            skill_name=skill_name,
            registration_template_id=template.template_id if template is not None else None,
            notes=[
                *([f"registration_template_id={template.template_id}"] if template is not None else []),
                "validated_via_skill_runtime_registry",
                "validated_runtime_contract_schema",
            ],
        )

    def _build_http_json_registration(
        self,
        *,
        candidate_entry: SubagentCatalogEntry,
        template: SubagentRuntimeRegistrationTemplate | None,
        source_id: str,
        endpoint_path: str,
        http_method: str,
        timeout_seconds: float | None,
        **_ignored: Any,
    ) -> SubagentRuntimeRegistration:
        self._validate_http_runtime_registration(
            candidate_entry,
            source_id=source_id,
            endpoint_path=endpoint_path,
            http_method=http_method,
        )
        return SubagentRuntimeRegistration(
            registration_type="http_json_runner",
            source_id=source_id,
            endpoint_path=endpoint_path,
            http_method=http_method or None,
            registration_template_id=template.template_id if template is not None else None,
            timeout_seconds=float(timeout_seconds) if timeout_seconds is not None else None,
            notes=[
                *([f"registration_template_id={template.template_id}"] if template is not None else []),
                "validated_via_data_source_registry",
                "validated_http_json_endpoint",
                "validated_runtime_contract_schema",
            ],
        )

    def _build_openapi_http_registration(
        self,
        *,
        candidate_entry: SubagentCatalogEntry,
        template: SubagentRuntimeRegistrationTemplate | None,
        source_id: str | None,
        openapi_spec_uri: str | None,
        openapi_operation_id: str | None,
        timeout_seconds: float | None,
        **_ignored: Any,
    ) -> SubagentRuntimeRegistration:
        resolved_openapi = self._resolve_openapi_runtime_registration(
            candidate_entry,
            source_id=source_id,
            openapi_spec_uri=openapi_spec_uri,
            openapi_operation_id=openapi_operation_id,
        )
        return SubagentRuntimeRegistration(
            registration_type="openapi_http_runner",
            source_id=resolved_openapi["source_id"],
            endpoint_path=resolved_openapi["endpoint_path"],
            http_method=resolved_openapi["http_method"] or None,
            openapi_spec_uri=resolved_openapi["openapi_spec_uri"] or None,
            openapi_operation_id=resolved_openapi["openapi_operation_id"] or None,
            registration_template_id=template.template_id if template is not None else None,
            timeout_seconds=float(timeout_seconds) if timeout_seconds is not None else None,
            notes=[
                *([f"registration_template_id={template.template_id}"] if template is not None else []),
                "validated_via_openapi_operation_spec",
                "validated_runtime_contract_schema",
            ],
        )

    def _snapshot_skill_template(
        self,
        candidate_entry: SubagentCatalogEntry,
    ) -> SubagentRuntimeRegistrationTemplate | None:
        hints = self._runtime_registration_hint(candidate_entry, section="snapshot_skill")
        skill_name = str(hints.get("skill_name") or "").strip()
        snapshot_type = str(hints.get("snapshot_type") or "").strip()
        if not skill_name or not snapshot_type:
            return None
        template_id = str(hints.get("template_id") or "").strip() or f"snapshot:{snapshot_type}:{skill_name}"
        return SubagentRuntimeRegistrationTemplate(
            template_id=template_id,
            registration_type="snapshot_skill_runner",
            summary=str(hints.get("summary") or "").strip() or f"Snapshot skill alias for {snapshot_type}",
            defaults={
                "skill_name": skill_name,
                "snapshot_type": snapshot_type,
            },
            notes=["generated_from_snapshot_skill_hints"],
        )

    def _skill_template(self, candidate_entry: SubagentCatalogEntry) -> SubagentRuntimeRegistrationTemplate | None:
        hints = self._runtime_registration_hint(candidate_entry, section="skill")
        skill_name = str(hints.get("skill_name") or "").strip()
        if not skill_name:
            return None
        template_id = str(hints.get("template_id") or "").strip() or f"skill:{skill_name}"
        return SubagentRuntimeRegistrationTemplate(
            template_id=template_id,
            registration_type="skill_runner",
            summary=str(hints.get("summary") or "").strip() or f"Skill runner alias for {skill_name}",
            defaults={"skill_name": skill_name},
            notes=["generated_from_skill_hints"],
        )

    def _http_json_template(
        self,
        candidate_entry: SubagentCatalogEntry,
    ) -> SubagentRuntimeRegistrationTemplate | None:
        hints = self._runtime_registration_hint(candidate_entry, section="http_json")
        source_id = str(hints.get("source_id") or "").strip()
        endpoint_path = str(hints.get("endpoint_path") or "").strip()
        http_method = str(hints.get("http_method") or "").strip().upper()
        if not source_id or not endpoint_path or http_method not in {"GET", "POST"}:
            return None
        template_id = str(hints.get("template_id") or "").strip() or self._http_template_id(
            source_id=source_id,
            endpoint_path=endpoint_path,
            http_method=http_method,
        )
        defaults: dict[str, Any] = {
            "source_id": source_id,
            "endpoint_path": endpoint_path,
            "http_method": http_method,
        }
        timeout_seconds = hints.get("timeout_seconds")
        if timeout_seconds is not None:
            defaults["timeout_seconds"] = timeout_seconds
        return SubagentRuntimeRegistrationTemplate(
            template_id=template_id,
            registration_type="http_json_runner",
            summary=str(hints.get("summary") or "").strip() or f"HTTP JSON alias for {source_id}",
            defaults=defaults,
            notes=["generated_from_http_json_hints"],
        )

    def _openapi_template(
        self,
        candidate_entry: SubagentCatalogEntry,
    ) -> SubagentRuntimeRegistrationTemplate | None:
        openapi_hints = self._candidate_openapi_hints(candidate_entry)
        template_id = str(openapi_hints.get("template_id") or "").strip()
        operation_id = str(openapi_hints.get("operation_id") or "").strip()
        if not operation_id:
            operation_id = self._derive_single_openapi_operation_id(openapi_hints.get("spec"))
        source_id = str(openapi_hints.get("source_id") or "").strip()
        spec_uri = str(openapi_hints.get("spec_uri") or "").strip()
        if not operation_id:
            return None
        normalized_id = template_id or f"openapi:{operation_id}"
        defaults: dict[str, Any] = {"openapi_operation_id": operation_id}
        if source_id:
            defaults["source_id"] = source_id
        if spec_uri:
            defaults["openapi_spec_uri"] = spec_uri
        return SubagentRuntimeRegistrationTemplate(
            template_id=normalized_id,
            registration_type="openapi_http_runner",
            summary=f"OpenAPI runtime alias for operation {operation_id}",
            defaults=defaults,
            notes=["generated_from_openapi_hints"],
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
        spec = self._load_openapi_spec(spec_uri=resolved_spec_uri or None, inline_spec=spec_payload)
        endpoint_path, http_method = self._resolve_openapi_operation(spec, operation_id=resolved_operation_id)
        return {
            "source_id": resolved_source_id,
            "endpoint_path": endpoint_path,
            "http_method": http_method,
            "openapi_spec_uri": resolved_spec_uri,
            "openapi_operation_id": resolved_operation_id,
        }

    @staticmethod
    def _candidate_openapi_hints(candidate_entry: SubagentCatalogEntry) -> dict[str, Any]:
        return SubagentRuntimePackagingSupport._runtime_registration_hint(candidate_entry, section="openapi")

    @staticmethod
    def _runtime_registration_hint(candidate_entry: SubagentCatalogEntry, *, section: str) -> dict[str, Any]:
        return runtime_registration_hint_payload(candidate_entry.runtime_registration_hints, section=section)

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

    @staticmethod
    def _http_template_id(*, source_id: str, endpoint_path: str, http_method: str) -> str:
        path_bits = [
            bit.replace("-", "_")
            for bit in str(endpoint_path or "").strip("/").split("/")
            if str(bit).strip()
        ]
        path_token = "_".join(path_bits) or "root"
        return f"http:{source_id}:{http_method.lower()}:{path_token}"


__all__ = [
    "SUPPORTED_RUNTIME_REGISTRATION_TYPES",
    "SubagentRuntimePackagingSupport",
]
