from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import yaml

from research_system.adapters.vendors.data_source_registry import DataSourceRegistry
from research_system.adapters.vendors.mcp_source_registry import McpSourceRegistry
from research_system.schemas.subagents import SubagentCatalogEntry


@dataclass(frozen=True)
class _RuntimeSourceResolverHandler:
    registration_type: str
    resolver_name: str


_RUNTIME_SOURCE_RESOLVER_HANDLERS = (
    _RuntimeSourceResolverHandler(
        registration_type="http_json_runner",
        resolver_name="_resolve_http_json_source",
    ),
    _RuntimeSourceResolverHandler(
        registration_type="openapi_http_runner",
        resolver_name="_resolve_openapi_http_source",
    ),
    _RuntimeSourceResolverHandler(
        registration_type="mcp_tool_runner",
        resolver_name="_resolve_mcp_tool_source",
    ),
)

SUPPORTED_RUNTIME_SOURCE_RESOLUTION_TYPES = tuple(
    handler.registration_type for handler in _RUNTIME_SOURCE_RESOLVER_HANDLERS
)


class SubagentRuntimeSourceResolutionSupport:
    def __init__(self, host=None) -> None:
        self.host = host
        self._handlers = {handler.registration_type: handler for handler in _RUNTIME_SOURCE_RESOLVER_HANDLERS}

    def resolution_types(self) -> tuple[str, ...]:
        return tuple(self._handlers)

    def resolve(
        self,
        *,
        registration_type: str,
        candidate_entry: SubagentCatalogEntry,
        source_id: str | None = None,
        endpoint_path: str | None = None,
        http_method: str | None = None,
        openapi_spec_uri: str | None = None,
        openapi_operation_id: str | None = None,
        openapi_hints: dict[str, Any] | None = None,
        tool_name: str | None = None,
    ) -> dict[str, str]:
        handler = self._handler(registration_type)
        return getattr(self, handler.resolver_name)(
            candidate_entry=candidate_entry,
            source_id=source_id,
            endpoint_path=endpoint_path,
            http_method=http_method,
            openapi_spec_uri=openapi_spec_uri,
            openapi_operation_id=openapi_operation_id,
            openapi_hints=dict(openapi_hints or {}),
            tool_name=tool_name,
        )

    def _handler(self, registration_type: str) -> _RuntimeSourceResolverHandler:
        normalized_type = str(registration_type or "").strip()
        handler = self._handlers.get(normalized_type)
        if handler is not None:
            return handler
        raise self._error(f"unsupported runtime source resolution type: {registration_type}")

    def _resolve_http_json_source(
        self,
        *,
        candidate_entry: SubagentCatalogEntry,
        source_id: str | None,
        endpoint_path: str | None,
        http_method: str | None,
        openapi_spec_uri: str | None,
        openapi_operation_id: str | None,
        openapi_hints: dict[str, Any],
        tool_name: str | None,
    ) -> dict[str, str]:
        del openapi_spec_uri, openapi_operation_id, openapi_hints, tool_name
        if candidate_entry.side_effect_level not in {"none", "read_only"}:
            raise self._error(
                f"http_json_runner activation only supports read-only candidates: {candidate_entry.subagent_type}"
            )
        normalized_source_id = str(source_id or "").strip()
        normalized_endpoint_path = str(endpoint_path or "").strip()
        normalized_http_method = str(http_method or "").strip().upper()
        self._validate_http_source(normalized_source_id, registration_type="http_json_runner")
        if not normalized_endpoint_path:
            raise self._error("endpoint_path is required for http_json_runner activation")
        if normalized_endpoint_path.startswith(("http://", "https://")):
            raise self._error("endpoint_path must be relative to the approved data source base_url")
        if not normalized_endpoint_path.startswith("/"):
            raise self._error("endpoint_path must start with '/' for http_json_runner activation")
        if normalized_http_method not in {"GET", "POST"}:
            raise self._error("http_method must be GET or POST for http_json_runner activation")
        return {
            "source_id": normalized_source_id,
            "endpoint_path": normalized_endpoint_path,
            "http_method": normalized_http_method,
            "openapi_spec_uri": "",
            "openapi_operation_id": "",
        }

    def _resolve_openapi_http_source(
        self,
        *,
        candidate_entry: SubagentCatalogEntry,
        source_id: str | None,
        endpoint_path: str | None,
        http_method: str | None,
        openapi_spec_uri: str | None,
        openapi_operation_id: str | None,
        openapi_hints: dict[str, Any],
        tool_name: str | None,
    ) -> dict[str, str]:
        del endpoint_path, http_method, tool_name
        if candidate_entry.side_effect_level not in {"none", "read_only"}:
            raise self._error(
                f"openapi_http_runner activation only supports read-only candidates: {candidate_entry.subagent_type}"
            )
        resolved_source_id = str(source_id or openapi_hints.get("source_id") or "").strip()
        resolved_spec_uri = str(openapi_spec_uri or openapi_hints.get("spec_uri") or "").strip()
        resolved_operation_id = str(openapi_operation_id or openapi_hints.get("operation_id") or "").strip()
        spec_payload = openapi_hints.get("spec")
        self._validate_http_source(resolved_source_id, registration_type="openapi_http_runner")
        if not resolved_operation_id:
            raise self._error(
                f"openapi_http_runner activation requires operation_id for candidate: {candidate_entry.subagent_type}"
            )
        spec = self._load_openapi_spec(spec_uri=resolved_spec_uri or None, inline_spec=spec_payload)
        resolved_endpoint_path, resolved_http_method = self._resolve_openapi_operation(
            spec,
            operation_id=resolved_operation_id,
        )
        return {
            "source_id": resolved_source_id,
            "endpoint_path": resolved_endpoint_path,
            "http_method": resolved_http_method,
            "openapi_spec_uri": resolved_spec_uri,
            "openapi_operation_id": resolved_operation_id,
        }

    def _resolve_mcp_tool_source(
        self,
        *,
        candidate_entry: SubagentCatalogEntry,
        source_id: str | None,
        endpoint_path: str | None,
        http_method: str | None,
        openapi_spec_uri: str | None,
        openapi_operation_id: str | None,
        openapi_hints: dict[str, Any],
        tool_name: str | None,
    ) -> dict[str, str]:
        del endpoint_path, http_method, openapi_spec_uri, openapi_operation_id, openapi_hints
        if candidate_entry.side_effect_level not in {"none", "read_only"}:
            raise self._error(
                f"mcp_tool_runner activation only supports read-only candidates: {candidate_entry.subagent_type}"
            )
        normalized_source_id = str(source_id or "").strip()
        normalized_tool_name = str(tool_name or "").strip()
        registry = self._mcp_registry()
        server = registry.get(normalized_source_id)
        if server is None:
            raise self._error(f"unknown source_id for mcp_tool_runner activation: {normalized_source_id}")
        if str(server.transport or "").strip().lower() != "stdio":
            raise self._error(
                f"mcp_tool_runner activation currently requires stdio transport: {normalized_source_id}"
            )
        if not normalized_tool_name:
            raise self._error("tool_name is required for mcp_tool_runner activation")
        tool = registry.tool(normalized_source_id, normalized_tool_name)
        if tool is None:
            raise self._error(
                f"unknown tool_name for mcp_tool_runner activation: {normalized_source_id}/{normalized_tool_name}"
            )
        if not bool(tool.read_only):
            raise self._error(
                f"mcp_tool_runner activation only supports read-only tools: {normalized_source_id}/{normalized_tool_name}"
            )
        return {
            "source_id": normalized_source_id,
            "tool_name": normalized_tool_name,
            "endpoint_path": "",
            "http_method": "",
            "openapi_spec_uri": "",
            "openapi_operation_id": "",
        }

    def _validate_http_source(self, source_id: str, *, registration_type: str) -> None:
        if not source_id:
            raise self._error(f"source_id is required for {registration_type} activation")
        source = DataSourceRegistry().get(source_id)
        if source is None:
            raise self._error(f"unknown source_id for {registration_type} activation: {source_id}")
        if str(source.access).strip().lower() != "http":
            raise self._error(
                f"{registration_type} activation requires an http-access data source: {source_id}"
            )
        if bool(source.requires_key):
            raise self._error(
                f"{registration_type} activation does not support keyed data sources yet: {source_id}"
            )

    def _load_openapi_spec(
        self,
        *,
        spec_uri: str | None,
        inline_spec: object,
    ) -> dict[str, Any]:
        if isinstance(inline_spec, dict):
            return dict(inline_spec)
        if not spec_uri:
            raise self._error("openapi_http_runner activation requires inline spec or openapi_spec_uri")
        if spec_uri.startswith(("http://", "https://")):
            response = requests.get(spec_uri, timeout=10)
            response.raise_for_status()
            return self._parse_openapi_payload(response.text, origin=spec_uri)
        path = Path(spec_uri)
        if not path.exists():
            raise self._error(f"openapi spec file not found: {spec_uri}")
        return self._parse_openapi_payload(path.read_text(encoding="utf-8"), origin=spec_uri)

    def _parse_openapi_payload(self, payload: str, *, origin: str) -> dict[str, Any]:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            parsed = yaml.safe_load(payload) or {}
        if not isinstance(parsed, dict):
            raise self._error(f"openapi spec must decode to an object: {origin}")
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
                    raise self._error(f"openapi operation path must be relative: {operation_id}")
                return normalized_path, normalized_method
        raise self._error(f"openapi operation not found: {operation_id}")

    @staticmethod
    def derive_single_openapi_operation_id(spec: object) -> str:
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

    def _error(self, message: str) -> Exception:
        error_cls = getattr(self.host, "orchestrator_error_cls", ValueError) or ValueError
        return error_cls(message)

    def _mcp_registry(self) -> McpSourceRegistry:
        factory = getattr(self.host, "require_mcp_source_registry", None)
        if callable(factory):
            registry = factory()
            if isinstance(registry, McpSourceRegistry):
                return registry
        settings = getattr(self.host, "settings", None)
        return McpSourceRegistry(settings=settings)


__all__ = [
    "SUPPORTED_RUNTIME_SOURCE_RESOLUTION_TYPES",
    "SubagentRuntimeSourceResolutionSupport",
]
