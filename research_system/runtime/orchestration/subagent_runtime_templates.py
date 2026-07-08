from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research_system.runtime.orchestration.subagent_runtime_source_resolution import (
    SubagentRuntimeSourceResolutionSupport,
)
from research_system.schemas.subagent_runtime_registration_hints import runtime_registration_hint_payload
from research_system.schemas.subagents import (
    SubagentCatalogEntry,
    SubagentRuntimeRegistrationTemplate,
)


@dataclass(frozen=True)
class _RuntimeTemplateHandler:
    registration_type: str
    builder_name: str


_RUNTIME_TEMPLATE_HANDLERS = (
    _RuntimeTemplateHandler(
        registration_type="snapshot_skill_runner",
        builder_name="_snapshot_skill_template",
    ),
    _RuntimeTemplateHandler(
        registration_type="skill_runner",
        builder_name="_skill_template",
    ),
    _RuntimeTemplateHandler(
        registration_type="http_json_runner",
        builder_name="_http_json_template",
    ),
    _RuntimeTemplateHandler(
        registration_type="openapi_http_runner",
        builder_name="_openapi_template",
    ),
)

SUPPORTED_RUNTIME_TEMPLATE_TYPES = tuple(handler.registration_type for handler in _RUNTIME_TEMPLATE_HANDLERS)


class SubagentRuntimeTemplateSupport:
    def __init__(self, host=None) -> None:
        self.host = host
        self._handlers = {handler.registration_type: handler for handler in _RUNTIME_TEMPLATE_HANDLERS}

    def template_types(self) -> tuple[str, ...]:
        return tuple(self._handlers)

    def build_template(
        self,
        *,
        registration_type: str,
        candidate_entry: SubagentCatalogEntry,
    ) -> SubagentRuntimeRegistrationTemplate | None:
        handler = self._handler(registration_type)
        return getattr(self, handler.builder_name)(candidate_entry)

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
        for registration_type in self.template_types():
            template = self.build_template(
                registration_type=registration_type,
                candidate_entry=candidate_entry,
            )
            if template is None:
                continue
            normalized_id = str(template.template_id).strip()
            if not normalized_id or normalized_id in seen_ids:
                continue
            templates.append(template)
            seen_ids.add(normalized_id)
        return templates

    @staticmethod
    def openapi_hints(candidate_entry: SubagentCatalogEntry) -> dict[str, Any]:
        return SubagentRuntimeTemplateSupport.runtime_registration_hint(candidate_entry, section="openapi")

    @staticmethod
    def runtime_registration_hint(
        candidate_entry: SubagentCatalogEntry,
        *,
        section: str,
    ) -> dict[str, Any]:
        return runtime_registration_hint_payload(candidate_entry.runtime_registration_hints, section=section)

    def _handler(self, registration_type: str) -> _RuntimeTemplateHandler:
        normalized_type = str(registration_type or "").strip()
        handler = self._handlers.get(normalized_type)
        if handler is not None:
            return handler
        raise self._error(f"unsupported runtime template type: {registration_type}")

    def _snapshot_skill_template(
        self,
        candidate_entry: SubagentCatalogEntry,
    ) -> SubagentRuntimeRegistrationTemplate | None:
        hints = self.runtime_registration_hint(candidate_entry, section="snapshot_skill")
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
        hints = self.runtime_registration_hint(candidate_entry, section="skill")
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
        hints = self.runtime_registration_hint(candidate_entry, section="http_json")
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
        openapi_hints = self.openapi_hints(candidate_entry)
        template_id = str(openapi_hints.get("template_id") or "").strip()
        operation_id = str(openapi_hints.get("operation_id") or "").strip()
        if not operation_id:
            operation_id = SubagentRuntimeSourceResolutionSupport.derive_single_openapi_operation_id(
                openapi_hints.get("spec")
            )
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

    @staticmethod
    def _http_template_id(*, source_id: str, endpoint_path: str, http_method: str) -> str:
        path_bits = [
            bit.replace("-", "_")
            for bit in str(endpoint_path or "").strip("/").split("/")
            if str(bit).strip()
        ]
        path_token = "_".join(path_bits) or "root"
        return f"http:{source_id}:{http_method.lower()}:{path_token}"

    def _error(self, message: str) -> Exception:
        error_cls = getattr(self.host, "orchestrator_error_cls", ValueError) or ValueError
        return error_cls(message)


__all__ = [
    "SUPPORTED_RUNTIME_TEMPLATE_TYPES",
    "SubagentRuntimeTemplateSupport",
]
