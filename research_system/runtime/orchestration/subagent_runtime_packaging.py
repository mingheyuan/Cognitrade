from __future__ import annotations

from research_system.runtime.orchestration.subagent_runtime_materialization import (
    SubagentRuntimeMaterializationSupport,
)
from research_system.runtime.orchestration.subagent_runtime_registration_builder import (
    SubagentRuntimeRegistrationBuilderSupport,
)
from research_system.runtime.orchestration.subagent_runtime_templates import (
    SubagentRuntimeTemplateSupport,
)
from research_system.schemas.subagent_runtime_registration import SUPPORTED_RUNTIME_REGISTRATION_TYPES
from research_system.schemas.subagents import (
    SubagentActivationEntry,
    SubagentCatalogEntry,
    SubagentRuntimeRegistration,
    SubagentRuntimeRegistrationTemplate,
)
from research_system.subagents.base import SubagentCatalogMetadata, SubagentRuntimePolicy
from research_system.subagents.runtime_registered import (
    RuntimeRegisteredSnapshotSkillInputModel,
    RuntimeRegisteredSnapshotSkillOutputModel,
    build_runtime_registered_model,
)


class SubagentRuntimePackagingSupport:
    def __init__(self, host=None) -> None:
        self.host = host
        self.template_support = SubagentRuntimeTemplateSupport(host)
        self.registration_builder = SubagentRuntimeRegistrationBuilderSupport(host)
        self.materialization_support = SubagentRuntimeMaterializationSupport(host)

    def normalize_candidate_entry(self, candidate_entry: SubagentCatalogEntry) -> SubagentCatalogEntry:
        return candidate_entry.model_copy(
            update={"runtime_registration_templates": self.candidate_templates(candidate_entry)},
            deep=True,
        )

    def registration_types(self) -> tuple[str, ...]:
        return self.registration_builder.builder_types()

    def materialize_runtime_subagent(
        self,
        *,
        activation_entry: SubagentActivationEntry,
        skill_runtime=None,
    ):
        catalog_entry = activation_entry.catalog_entry
        return self.materialization_support.materialize_runtime_subagent(
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
        runtime_registration = activation_entry.runtime_registration
        registration_type = str(getattr(runtime_registration, "registration_type", "") or "").strip()
        if registration_type == "snapshot_skill_runner":
            input_schema = dict(catalog_entry.input_json_schema or {}) or RuntimeRegisteredSnapshotSkillInputModel.model_json_schema()
            output_schema = dict(catalog_entry.output_json_schema or {}) or RuntimeRegisteredSnapshotSkillOutputModel.model_json_schema()
            return (
                build_runtime_registered_model(
                    model_name="ActivatedSnapshotSkillInputModel",
                    schema=input_schema,
                ),
                build_runtime_registered_model(
                    model_name="ActivatedSnapshotSkillOutputModel",
                    schema=output_schema,
                ),
            )
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
        return self.template_support.candidate_templates(candidate_entry)

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
        raise self._error(f"runtime registration template not found for candidate: {normalized_template_id}")

    def resolve_runtime_registration(
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
    ) -> SubagentRuntimeRegistration | None:
        template = self.select_template(candidate_entry, template_id=registration_template_id)
        template_defaults = dict(template.defaults or {}) if template is not None else {}
        normalized_type = str(registration_type or getattr(template, "registration_type", "") or "").strip()
        if not normalized_type:
            return None
        if template is not None and str(template.registration_type).strip() != normalized_type:
            raise self._error(f"runtime registration template type mismatch: {template.template_id}")
        return self.registration_builder.build_registration(
            registration_type=normalized_type,
            candidate_entry=candidate_entry,
            template=template,
            skill_name=str(skill_name or template_defaults.get("skill_name") or "").strip(),
            snapshot_type=str(snapshot_type or template_defaults.get("snapshot_type") or "").strip(),
            source_id=str(source_id or template_defaults.get("source_id") or "").strip() or None,
            tool_name=str(tool_name or template_defaults.get("tool_name") or "").strip(),
            endpoint_path=str(endpoint_path or template_defaults.get("endpoint_path") or "").strip(),
            http_method=str(http_method or template_defaults.get("http_method") or "").strip().upper(),
            openapi_spec_uri=str(openapi_spec_uri or template_defaults.get("openapi_spec_uri") or "").strip() or None,
            openapi_operation_id=str(
                openapi_operation_id or template_defaults.get("openapi_operation_id") or ""
            ).strip()
            or None,
            timeout_seconds=(
                timeout_seconds if timeout_seconds is not None else template_defaults.get("timeout_seconds")
            ),
        )

    def _error(self, message: str) -> Exception:
        error_cls = getattr(self.host, "orchestrator_error_cls", ValueError) or ValueError
        return error_cls(message)


__all__ = [
    "SUPPORTED_RUNTIME_REGISTRATION_TYPES",
    "SubagentRuntimePackagingSupport",
]
