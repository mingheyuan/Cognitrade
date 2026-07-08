from __future__ import annotations

from dataclasses import dataclass

from research_system.adapters.vendors.mcp_source_registry import McpSourceRegistry
from research_system.runtime.orchestration.subagent_runtime_packaging import (
    SubagentRuntimePackagingSupport,
)
from research_system.schemas.subagents import (
    SubagentCatalogEntry,
    SubagentDiscoveryTicket,
)


@dataclass(frozen=True)
class _DiscoveryCandidateSourceHandler:
    protocol: str
    loader_name: str


_DISCOVERY_CANDIDATE_SOURCE_HANDLERS = (
    _DiscoveryCandidateSourceHandler(protocol="mcp", loader_name="_mcp_candidate_entries"),
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

    def _mcp_registry(self) -> McpSourceRegistry:
        factory = getattr(self.host, "require_mcp_source_registry", None)
        if callable(factory):
            registry = factory()
            if isinstance(registry, McpSourceRegistry):
                return registry
        settings = getattr(self.host, "settings", None)
        return McpSourceRegistry(settings=settings)


__all__ = [
    "SUPPORTED_DISCOVERY_CANDIDATE_SOURCE_TYPES",
    "SubagentDiscoveryCandidateSupport",
]
