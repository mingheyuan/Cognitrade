from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research_system.runtime.orchestration.subagent_runtime_source_resolution import (
    SubagentRuntimeSourceResolutionSupport,
)
from research_system.runtime.orchestration.subagent_runtime_templates import (
    SubagentRuntimeTemplateSupport,
)
from research_system.runtime.orchestration.subagent_runtime_validation import (
    SubagentRuntimeValidationSupport,
)
from research_system.schemas.subagents import (
    SubagentCatalogEntry,
    SubagentRuntimeRegistration,
    SubagentRuntimeRegistrationTemplate,
)


@dataclass(frozen=True)
class _RuntimeRegistrationBuilderHandler:
    registration_type: str
    builder_name: str


_RUNTIME_REGISTRATION_BUILDER_HANDLERS = (
    _RuntimeRegistrationBuilderHandler(
        registration_type="snapshot_skill_runner",
        builder_name="_build_snapshot_skill_registration",
    ),
    _RuntimeRegistrationBuilderHandler(
        registration_type="skill_runner",
        builder_name="_build_skill_registration",
    ),
    _RuntimeRegistrationBuilderHandler(
        registration_type="http_json_runner",
        builder_name="_build_http_json_registration",
    ),
    _RuntimeRegistrationBuilderHandler(
        registration_type="openapi_http_runner",
        builder_name="_build_openapi_http_registration",
    ),
)

SUPPORTED_RUNTIME_REGISTRATION_BUILDER_TYPES = tuple(
    handler.registration_type for handler in _RUNTIME_REGISTRATION_BUILDER_HANDLERS
)


class SubagentRuntimeRegistrationBuilderSupport:
    def __init__(self, host=None) -> None:
        self.host = host
        self._handlers = {
            handler.registration_type: handler for handler in _RUNTIME_REGISTRATION_BUILDER_HANDLERS
        }
        self.template_support = SubagentRuntimeTemplateSupport(host)
        self.source_resolution = SubagentRuntimeSourceResolutionSupport(host)
        self.validation_support = SubagentRuntimeValidationSupport(host)

    def builder_types(self) -> tuple[str, ...]:
        return tuple(self._handlers)

    def build_registration(
        self,
        *,
        registration_type: str,
        candidate_entry: SubagentCatalogEntry,
        template: SubagentRuntimeRegistrationTemplate | None,
        skill_name: str = "",
        snapshot_type: str = "",
        source_id: str | None = None,
        endpoint_path: str = "",
        http_method: str = "",
        openapi_spec_uri: str | None = None,
        openapi_operation_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> SubagentRuntimeRegistration:
        handler = self._handler(registration_type)
        return getattr(self, handler.builder_name)(
            candidate_entry=candidate_entry,
            template=template,
            skill_name=str(skill_name or "").strip(),
            snapshot_type=str(snapshot_type or "").strip(),
            source_id=str(source_id or "").strip() or None,
            endpoint_path=str(endpoint_path or "").strip(),
            http_method=str(http_method or "").strip().upper(),
            openapi_spec_uri=str(openapi_spec_uri or "").strip() or None,
            openapi_operation_id=str(openapi_operation_id or "").strip() or None,
            timeout_seconds=timeout_seconds,
        )

    def _handler(self, registration_type: str) -> _RuntimeRegistrationBuilderHandler:
        normalized_type = str(registration_type or "").strip()
        handler = self._handlers.get(normalized_type)
        if handler is not None:
            return handler
        raise self._error(f"unsupported runtime registration builder type: {registration_type}")

    def _build_snapshot_skill_registration(
        self,
        *,
        candidate_entry: SubagentCatalogEntry,
        template: SubagentRuntimeRegistrationTemplate | None,
        skill_name: str,
        snapshot_type: str,
        **_ignored: Any,
    ) -> SubagentRuntimeRegistration:
        validation_notes = self.validation_support.validate_registration(
            registration_type="snapshot_skill_runner",
            candidate_entry=candidate_entry,
            skill_name=skill_name,
            snapshot_type=snapshot_type,
        )
        if not snapshot_type:
            raise self._error("snapshot_type is required for snapshot_skill_runner activation")
        return SubagentRuntimeRegistration(
            registration_type="snapshot_skill_runner",
            skill_name=skill_name,
            snapshot_type=snapshot_type,
            registration_template_id=template.template_id if template is not None else None,
            notes=[
                *self._template_notes(template),
                *validation_notes,
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
        validation_notes = self.validation_support.validate_registration(
            registration_type="skill_runner",
            candidate_entry=candidate_entry,
            skill_name=skill_name,
        )
        return SubagentRuntimeRegistration(
            registration_type="skill_runner",
            skill_name=skill_name,
            registration_template_id=template.template_id if template is not None else None,
            notes=[
                *self._template_notes(template),
                *validation_notes,
            ],
        )

    def _build_http_json_registration(
        self,
        *,
        candidate_entry: SubagentCatalogEntry,
        template: SubagentRuntimeRegistrationTemplate | None,
        source_id: str | None,
        endpoint_path: str,
        http_method: str,
        timeout_seconds: float | None,
        **_ignored: Any,
    ) -> SubagentRuntimeRegistration:
        resolved_source = self.source_resolution.resolve(
            registration_type="http_json_runner",
            candidate_entry=candidate_entry,
            source_id=source_id,
            endpoint_path=endpoint_path,
            http_method=http_method,
            openapi_hints={},
        )
        validation_notes = self.validation_support.validate_registration(
            registration_type="http_json_runner",
            candidate_entry=candidate_entry,
        )
        return SubagentRuntimeRegistration(
            registration_type="http_json_runner",
            source_id=resolved_source["source_id"],
            endpoint_path=resolved_source["endpoint_path"],
            http_method=resolved_source["http_method"] or None,
            registration_template_id=template.template_id if template is not None else None,
            timeout_seconds=float(timeout_seconds) if timeout_seconds is not None else None,
            notes=[
                *self._template_notes(template),
                "validated_via_data_source_registry",
                "validated_http_json_endpoint",
                *validation_notes,
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
        resolved_openapi = self.source_resolution.resolve(
            registration_type="openapi_http_runner",
            candidate_entry=candidate_entry,
            source_id=source_id,
            openapi_spec_uri=openapi_spec_uri,
            openapi_operation_id=openapi_operation_id,
            openapi_hints=self.template_support.openapi_hints(candidate_entry),
        )
        validation_notes = self.validation_support.validate_registration(
            registration_type="openapi_http_runner",
            candidate_entry=candidate_entry,
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
                *self._template_notes(template),
                "validated_via_openapi_operation_spec",
                *validation_notes,
            ],
        )

    @staticmethod
    def _template_notes(template: SubagentRuntimeRegistrationTemplate | None) -> list[str]:
        if template is None:
            return []
        return [f"registration_template_id={template.template_id}"]

    def _error(self, message: str) -> Exception:
        error_cls = getattr(self.host, "orchestrator_error_cls", ValueError) or ValueError
        return error_cls(message)


__all__ = [
    "SUPPORTED_RUNTIME_REGISTRATION_BUILDER_TYPES",
    "SubagentRuntimeRegistrationBuilderSupport",
]
