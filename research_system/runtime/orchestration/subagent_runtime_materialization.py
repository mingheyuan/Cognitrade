from __future__ import annotations

from dataclasses import dataclass

from research_system.adapters.vendors.data_source_registry import DataSourceRegistry
from research_system.schemas.subagents import SubagentActivationEntry
from research_system.subagents.base import SubagentCatalogMetadata, SubagentRuntimePolicy
from research_system.subagents.runtime_registered import (
    RuntimeRegisteredHttpJsonSubagent,
    RuntimeRegisteredSkillSubagent,
    RuntimeRegisteredSnapshotSkillSubagent,
)


@dataclass(frozen=True)
class _RuntimeMaterializationHandler:
    registration_type: str
    materializer_name: str


_RUNTIME_MATERIALIZATION_HANDLERS = (
    _RuntimeMaterializationHandler(
        registration_type="snapshot_skill_runner",
        materializer_name="_materialize_snapshot_skill_subagent",
    ),
    _RuntimeMaterializationHandler(
        registration_type="skill_runner",
        materializer_name="_materialize_skill_subagent",
    ),
    _RuntimeMaterializationHandler(
        registration_type="http_json_runner",
        materializer_name="_materialize_http_json_subagent",
    ),
    _RuntimeMaterializationHandler(
        registration_type="openapi_http_runner",
        materializer_name="_materialize_http_json_subagent",
    ),
)

SUPPORTED_RUNTIME_MATERIALIZATION_TYPES = tuple(
    handler.registration_type for handler in _RUNTIME_MATERIALIZATION_HANDLERS
)


class SubagentRuntimeMaterializationSupport:
    def __init__(self, host=None) -> None:
        self.host = host
        self._handlers = {handler.registration_type: handler for handler in _RUNTIME_MATERIALIZATION_HANDLERS}

    def materialization_types(self) -> tuple[str, ...]:
        return tuple(self._handlers)

    def materialize_runtime_subagent(
        self,
        *,
        activation_entry: SubagentActivationEntry,
        skill_runtime=None,
        runtime_policy: SubagentRuntimePolicy,
        catalog_metadata: SubagentCatalogMetadata,
        runtime_models: tuple[type | None, type | None],
    ):
        runtime_registration = activation_entry.runtime_registration
        if runtime_registration is None:
            return None
        handler = self._handler(runtime_registration.registration_type, allow_missing=True)
        if handler is None:
            return None
        return getattr(self, handler.materializer_name)(
            activation_entry=activation_entry,
            skill_runtime=skill_runtime,
            runtime_policy=runtime_policy,
            catalog_metadata=catalog_metadata,
            runtime_models=runtime_models,
        )

    def _handler(
        self,
        registration_type: str,
        *,
        allow_missing: bool = False,
    ) -> _RuntimeMaterializationHandler | None:
        normalized_type = str(registration_type or "").strip()
        handler = self._handlers.get(normalized_type)
        if handler is not None or allow_missing:
            return handler
        raise self._error(f"unsupported runtime materialization type: {registration_type}")

    def _materialize_snapshot_skill_subagent(
        self,
        *,
        activation_entry: SubagentActivationEntry,
        skill_runtime,
        runtime_policy: SubagentRuntimePolicy,
        catalog_metadata: SubagentCatalogMetadata,
        runtime_models: tuple[type | None, type | None],
    ):
        del runtime_models
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
        del skill_runtime
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

    def _error(self, message: str) -> Exception:
        error_cls = getattr(self.host, "orchestrator_error_cls", ValueError) or ValueError
        return error_cls(message)


__all__ = [
    "SUPPORTED_RUNTIME_MATERIALIZATION_TYPES",
    "SubagentRuntimeMaterializationSupport",
]
