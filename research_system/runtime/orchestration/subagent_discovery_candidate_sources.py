from __future__ import annotations

from dataclasses import dataclass

from research_system.adapters.vendors.data_source_registry import DataSourceRegistry
from research_system.adapters.vendors.mcp_source_registry import McpSourceRegistry
from research_system.adapters.vendors.openapi_source_registry import OpenApiSourceRegistry
from research_system.adapters.vendors.skill_source_registry import SkillSourceRegistry
from research_system.runtime.orchestration.subagent_runtime_packaging import (
    SubagentRuntimePackagingSupport,
)
from research_system.runtime.platform.skill_runtime import SkillRuntime
from research_system.schemas.subagents import (
    SubagentCatalogEntry,
    SubagentDiscoveryTicket,
)
from research_system.subagents.runtime_registered import (
    RuntimeRegisteredSnapshotSkillInputModel,
    RuntimeRegisteredSnapshotSkillOutputModel,
)


@dataclass(frozen=True)
class _DiscoveryCandidateSourceHandler:
    protocol: str
    loader_name: str


_DISCOVERY_CANDIDATE_SOURCE_HANDLERS = (
    _DiscoveryCandidateSourceHandler(protocol="mcp", loader_name="_mcp_candidate_entries"),
    _DiscoveryCandidateSourceHandler(protocol="openapi", loader_name="_openapi_candidate_entries"),
    _DiscoveryCandidateSourceHandler(protocol="skill", loader_name="_skill_candidate_entries"),
)

SUPPORTED_DISCOVERY_CANDIDATE_SOURCE_TYPES = tuple(
    handler.protocol for handler in _DISCOVERY_CANDIDATE_SOURCE_HANDLERS
)


class SubagentDiscoveryCandidateSupport:
    def __init__(self, host=None) -> None:
        self.host = host
        self.runtime_packaging = SubagentRuntimePackagingSupport(host)
        self._handlers = {
            handler.protocol: handler for handler in _DISCOVERY_CANDIDATE_SOURCE_HANDLERS
        }

    def source_types(self) -> tuple[str, ...]:
        return tuple(self._handlers)

    def candidate_entries(self, ticket: SubagentDiscoveryTicket) -> list[SubagentCatalogEntry]:
        preferred_protocols = [
            str(item).strip().lower()
            for item in list(ticket.tool_discovery_request.preferred_protocols or [])
            if str(item).strip()
        ]
        entries: list[SubagentCatalogEntry] = []
        seen_subagent_types: set[str] = set()
        for protocol in preferred_protocols:
            handler = self._handlers.get(protocol)
            if handler is None:
                continue
            for entry in getattr(self, handler.loader_name)(ticket):
                normalized_type = str(entry.subagent_type or "").strip()
                if not normalized_type or normalized_type in seen_subagent_types:
                    continue
                seen_subagent_types.add(normalized_type)
                entries.append(
                    self.runtime_packaging.normalize_candidate_entry(entry)
                )
        return entries

    def _mcp_candidate_entries(self, ticket: SubagentDiscoveryTicket) -> list[SubagentCatalogEntry]:
        registry = self._mcp_registry()
        gap = ticket.capability_gap
        matching_rows: list[tuple[str, object, object]] = []
        for server in registry.list_all():
            for tool in server.tools:
                if not bool(tool.read_only):
                    continue
                if not self._matches_stage(gap.stage, tool.stage):
                    continue
                if not self._matches_intent(gap.intent, tool.suitable_intents):
                    continue
                if not self._matches_capability(gap.capability, tool.capabilities):
                    continue
                if not self._is_object_schema(tool.input_json_schema):
                    continue
                if not self._is_object_schema(tool.output_json_schema):
                    continue
                matching_rows.append((server.id, server, tool))
        total = len(matching_rows)
        entries: list[SubagentCatalogEntry] = []
        for index, (server_id, server, tool) in enumerate(matching_rows):
            entries.append(
                SubagentCatalogEntry(
                    subagent_type=self._candidate_subagent_type(
                        ticket,
                        source_id=server_id,
                        tool_name=tool.name,
                        explicit_subagent_type=getattr(tool, "candidate_subagent_type", None),
                        total=total,
                        index=index,
                    ),
                    summary=str(tool.summary or "").strip()
                    or f"Approved MCP tool candidate for {tool.name}@{server.name}",
                    capabilities=list(tool.capabilities or [str(gap.capability).strip()]),
                    stage=str(tool.stage or gap.stage or "").strip() or None,
                    cost_class="medium",
                    latency_class="medium",
                    side_effect_level="read_only",
                    suitable_intents=list(tool.suitable_intents or ([str(gap.intent).strip()] if str(gap.intent or "").strip() else [])),
                    tags=[
                        "approved_discovery_candidate",
                        "configured_mcp_tool",
                        "pending_runtime_registration",
                        f"mcp_source:{server_id}",
                    ],
                    requires_llm=bool(gap.requires_llm),
                    allow_parallel=False,
                    max_concurrency=1,
                    input_json_schema=dict(tool.input_json_schema or {}),
                    output_json_schema=dict(tool.output_json_schema or {}),
                    runtime_registration_hints={
                        "mcp": {
                            "source_id": server_id,
                            "tool_name": str(tool.name).strip(),
                            "timeout_seconds": float(getattr(server, "timeout_seconds", 20.0) or 20.0),
                            "summary": str(tool.summary or "").strip()
                            or f"MCP tool alias for {tool.name}@{server.name}",
                        }
                    },
                )
            )
        return entries

    def _openapi_candidate_entries(self, ticket: SubagentDiscoveryTicket) -> list[SubagentCatalogEntry]:
        registry = self._openapi_registry()
        gap = ticket.capability_gap
        matching_rows = []
        for operation in registry.list_all():
            if not bool(operation.read_only):
                continue
            if not self._matches_stage(gap.stage, operation.stage):
                continue
            if not self._matches_intent(gap.intent, operation.suitable_intents):
                continue
            if not self._matches_capability(gap.capability, operation.capabilities):
                continue
            if not self._is_object_schema(operation.input_json_schema):
                continue
            if not self._is_object_schema(operation.output_json_schema):
                continue
            if not self._is_discovery_http_source_allowed(operation.source_id):
                continue
            normalized_operation_id = str(operation.operation_id or "").strip()
            if not normalized_operation_id:
                continue
            spec_uri = str(operation.spec_uri or "").strip()
            spec = dict(operation.spec or {}) if isinstance(operation.spec, dict) else None
            if not spec_uri and not spec:
                continue
            if spec and not self._openapi_spec_contains_operation(spec, normalized_operation_id):
                continue
            matching_rows.append(operation)
        total = len(matching_rows)
        entries: list[SubagentCatalogEntry] = []
        for index, operation in enumerate(matching_rows):
            openapi_hints = {
                "source_id": str(operation.source_id).strip(),
                "operation_id": str(operation.operation_id).strip(),
                "summary": str(operation.summary or "").strip()
                or f"OpenAPI operation alias for {operation.operation_id}@{operation.source_id}",
            }
            if str(operation.spec_uri or "").strip():
                openapi_hints["spec_uri"] = str(operation.spec_uri).strip()
            if isinstance(operation.spec, dict):
                openapi_hints["spec"] = dict(operation.spec)
            entries.append(
                SubagentCatalogEntry(
                    subagent_type=self._candidate_subagent_type(
                        ticket,
                        source_id=str(operation.source_id).strip(),
                        tool_name=str(operation.operation_id).strip(),
                        explicit_subagent_type=operation.candidate_subagent_type,
                        total=total,
                        index=index,
                    ),
                    summary=str(operation.summary or "").strip()
                    or f"Approved OpenAPI candidate for {operation.operation_id}@{operation.source_id}",
                    capabilities=list(operation.capabilities or [str(gap.capability).strip()]),
                    stage=str(operation.stage or gap.stage or "").strip() or None,
                    cost_class="medium",
                    latency_class="medium",
                    side_effect_level="read_only",
                    suitable_intents=list(
                        operation.suitable_intents
                        or ([str(gap.intent).strip()] if str(gap.intent or "").strip() else [])
                    ),
                    tags=[
                        "approved_discovery_candidate",
                        "configured_openapi_operation",
                        "pending_runtime_registration",
                        f"openapi_source:{operation.source_id}",
                    ],
                    requires_llm=bool(gap.requires_llm),
                    allow_parallel=False,
                    max_concurrency=1,
                    input_json_schema=dict(operation.input_json_schema or {}),
                    output_json_schema=dict(operation.output_json_schema or {}),
                    runtime_registration_hints={"openapi": openapi_hints},
                )
            )
        return entries

    def _skill_candidate_entries(self, ticket: SubagentDiscoveryTicket) -> list[SubagentCatalogEntry]:
        registry = self._skill_source_registry()
        installed_skill_names = self._installed_skill_names()
        gap = ticket.capability_gap
        matching_rows = []
        for source in registry.list_all():
            normalized_registration_type = str(source.registration_type or "").strip()
            if normalized_registration_type not in {"snapshot_skill_runner", "skill_runner"}:
                continue
            if str(source.skill_name or "").strip() not in installed_skill_names:
                continue
            if not self._matches_stage(gap.stage, source.stage):
                continue
            if not self._matches_intent(gap.intent, source.suitable_intents):
                continue
            if not self._matches_capability(gap.capability, source.capabilities):
                continue
            if normalized_registration_type == "snapshot_skill_runner":
                if not str(source.snapshot_type or "").strip():
                    continue
            else:
                if not self._is_object_schema(source.input_json_schema):
                    continue
                if not self._is_object_schema(source.output_json_schema):
                    continue
            matching_rows.append(source)
        total = len(matching_rows)
        entries: list[SubagentCatalogEntry] = []
        for index, source in enumerate(matching_rows):
            normalized_registration_type = str(source.registration_type or "").strip()
            input_schema = self._candidate_input_schema_for_skill_source(source)
            output_schema = self._candidate_output_schema_for_skill_source(source)
            runtime_hints = (
                {
                    "snapshot_skill": {
                        "skill_name": str(source.skill_name).strip(),
                        "snapshot_type": str(source.snapshot_type or "").strip(),
                        "summary": str(source.summary or "").strip()
                        or f"Snapshot skill alias for {source.snapshot_type}",
                    }
                }
                if normalized_registration_type == "snapshot_skill_runner"
                else {
                    "skill": {
                        "skill_name": str(source.skill_name).strip(),
                        "summary": str(source.summary or "").strip()
                        or f"Skill runner alias for {source.skill_name}",
                    }
                }
            )
            entries.append(
                SubagentCatalogEntry(
                    subagent_type=self._candidate_subagent_type(
                        ticket,
                        source_id=normalized_registration_type,
                        tool_name=str(source.skill_name).strip(),
                        explicit_subagent_type=source.candidate_subagent_type,
                        total=total,
                        index=index,
                    ),
                    summary=str(source.summary or "").strip()
                    or f"Approved skill candidate for {source.skill_name}",
                    capabilities=list(source.capabilities or [str(gap.capability).strip()]),
                    stage=str(source.stage or gap.stage or "").strip() or None,
                    cost_class="medium",
                    latency_class="medium",
                    side_effect_level="read_only",
                    suitable_intents=list(
                        source.suitable_intents
                        or ([str(gap.intent).strip()] if str(gap.intent or "").strip() else [])
                    ),
                    tags=[
                        "approved_discovery_candidate",
                        "configured_skill_source",
                        "pending_runtime_registration",
                        f"skill_registration:{normalized_registration_type}",
                    ],
                    requires_llm=bool(gap.requires_llm),
                    allow_parallel=True,
                    max_concurrency=4,
                    input_json_schema=input_schema,
                    output_json_schema=output_schema,
                    runtime_registration_hints=runtime_hints,
                )
            )
        return entries

    @staticmethod
    def _matches_stage(gap_stage: str | None, tool_stage: str | None) -> bool:
        normalized_gap_stage = str(gap_stage or "").strip().lower()
        normalized_tool_stage = str(tool_stage or "").strip().lower()
        if not normalized_tool_stage or not normalized_gap_stage:
            return True
        return normalized_gap_stage == normalized_tool_stage

    @staticmethod
    def _matches_intent(gap_intent: str | None, suitable_intents: tuple[str, ...]) -> bool:
        normalized_gap_intent = str(gap_intent or "").strip()
        if not suitable_intents or not normalized_gap_intent:
            return True
        return normalized_gap_intent in {str(item).strip() for item in suitable_intents if str(item).strip()}

    @staticmethod
    def _matches_capability(gap_capability: str, capabilities: tuple[str, ...]) -> bool:
        normalized_gap_capability = str(gap_capability or "").strip()
        if not normalized_gap_capability:
            return False
        normalized_capabilities = {
            str(item).strip() for item in capabilities if str(item).strip()
        }
        if not normalized_capabilities:
            return False
        return normalized_gap_capability in normalized_capabilities

    @staticmethod
    def _is_object_schema(schema: object) -> bool:
        if not isinstance(schema, dict):
            return False
        normalized_type = str(schema.get("type") or "").strip().lower()
        properties = schema.get("properties")
        if normalized_type and normalized_type != "object":
            return False
        return isinstance(properties, dict)

    @staticmethod
    def _candidate_subagent_type(
        ticket: SubagentDiscoveryTicket,
        *,
        source_id: str,
        tool_name: str,
        explicit_subagent_type: str | None,
        total: int,
        index: int,
    ) -> str:
        if str(explicit_subagent_type or "").strip():
            return str(explicit_subagent_type).strip()
        gap = ticket.capability_gap
        base = str(gap.preferred_subagent_type or "").strip()
        if not base:
            stage = str(gap.stage or "phase").strip() or "phase"
            capability = str(gap.capability or "capability").strip() or "capability"
            base = f"candidate_{stage}_{capability}"
        if total <= 1:
            return SubagentDiscoveryCandidateSupport._sanitize_subagent_type(base)
        suffix = "__".join(
            part
            for part in (
                SubagentDiscoveryCandidateSupport._sanitize_subagent_type(source_id),
                SubagentDiscoveryCandidateSupport._sanitize_subagent_type(tool_name),
                str(index + 1),
            )
            if part
        )
        return SubagentDiscoveryCandidateSupport._sanitize_subagent_type(f"{base}__{suffix}")

    @staticmethod
    def _sanitize_subagent_type(value: str) -> str:
        return (
            str(value or "")
            .strip()
            .replace("-", "_")
            .replace(" ", "_")
            .replace("/", "_")
            .replace("\\", "_")
        )

    @staticmethod
    def _candidate_input_schema_for_skill_source(source) -> dict:
        normalized_registration_type = str(source.registration_type or "").strip()
        if normalized_registration_type == "snapshot_skill_runner":
            return RuntimeRegisteredSnapshotSkillInputModel.model_json_schema()
        return dict(source.input_json_schema or {})

    @staticmethod
    def _candidate_output_schema_for_skill_source(source) -> dict:
        normalized_registration_type = str(source.registration_type or "").strip()
        if normalized_registration_type == "snapshot_skill_runner":
            return RuntimeRegisteredSnapshotSkillOutputModel.model_json_schema()
        return dict(source.output_json_schema or {})

    @staticmethod
    def _is_discovery_http_source_allowed(source_id: str) -> bool:
        source = DataSourceRegistry().get(str(source_id or "").strip())
        if source is None:
            return False
        if str(source.access or "").strip().lower() != "http":
            return False
        if bool(source.requires_key):
            return False
        return True

    @staticmethod
    def _openapi_spec_contains_operation(spec: dict, operation_id: str) -> bool:
        normalized_operation_id = str(operation_id or "").strip()
        if not normalized_operation_id:
            return False
        for path_item in dict(spec.get("paths") or {}).values():
            if not isinstance(path_item, dict):
                continue
            for method_name, operation in path_item.items():
                normalized_method = str(method_name or "").strip().upper()
                if normalized_method not in {"GET", "POST"} or not isinstance(operation, dict):
                    continue
                if str(operation.get("operationId") or "").strip() == normalized_operation_id:
                    return True
        return False

    def _mcp_registry(self) -> McpSourceRegistry:
        factory = getattr(self.host, "require_mcp_source_registry", None)
        if callable(factory):
            registry = factory()
            if isinstance(registry, McpSourceRegistry):
                return registry
        settings = getattr(self.host, "settings", None)
        return McpSourceRegistry(settings=settings)

    def _openapi_registry(self) -> OpenApiSourceRegistry:
        factory = getattr(self.host, "require_openapi_source_registry", None)
        if callable(factory):
            registry = factory()
            if isinstance(registry, OpenApiSourceRegistry):
                return registry
        settings = getattr(self.host, "settings", None)
        return OpenApiSourceRegistry(settings=settings)

    def _skill_source_registry(self) -> SkillSourceRegistry:
        factory = getattr(self.host, "require_skill_source_registry", None)
        if callable(factory):
            registry = factory()
            if isinstance(registry, SkillSourceRegistry):
                return registry
        settings = getattr(self.host, "settings", None)
        return SkillSourceRegistry(settings=settings)

    def _installed_skill_names(self) -> set[str]:
        factory = getattr(self.host, "require_skill_runtime", None)
        if callable(factory):
            runtime = factory()
        else:
            settings = getattr(self.host, "settings", None)
            runtime = SkillRuntime(settings) if settings is not None else None
        backend = getattr(runtime, "backend", None)
        registry = getattr(backend, "registry", None)
        if not isinstance(registry, dict):
            return set()
        return {str(key).strip() for key in registry if str(key).strip()}


__all__ = [
    "SUPPORTED_DISCOVERY_CANDIDATE_SOURCE_TYPES",
    "SubagentDiscoveryCandidateSupport",
]
