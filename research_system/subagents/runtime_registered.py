from __future__ import annotations

import json
import os
import subprocess
from typing import Any
from urllib.parse import urljoin

import requests

from pydantic import BaseModel, ConfigDict, Field, create_model

from research_system.adapters.vendors.mcp_source_registry import McpServerSource
from research_system.runtime.platform.skill_runtime import SkillRuntime
from research_system.subagents.base import SubagentCatalogMetadata, SubagentRuntimePolicy


class RuntimeRegisteredSnapshotSkillInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    symbol: str | None = None


class RuntimeRegisteredSnapshotSkillOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_type: str
    snapshot: dict[str, Any] | None = None
    degraded: bool = False
    degrade_reason: str | None = None


class RuntimeRegisteredSnapshotSkillSubagent:
    input_model = RuntimeRegisteredSnapshotSkillInputModel
    output_model = RuntimeRegisteredSnapshotSkillOutputModel
    runtime_policy = SubagentRuntimePolicy(allow_parallel=True, max_concurrency=4)
    catalog_metadata = SubagentCatalogMetadata(
        summary="Execute one validated snapshot skill as a runtime-registered subagent alias.",
        capabilities=("snapshot_collection", "skill_bridge", "runtime_registration"),
        stage="collect",
        cost_class="medium",
        latency_class="medium",
        side_effect_level="read_only",
        suitable_intents=("stock_check", "stock_compare", "stock_screen", "sector_research", "review_thesis"),
        tags=("runtime_registered", "read_only"),
    )

    def __init__(
        self,
        *,
        subagent_type: str,
        snapshot_type: str,
        skill_name: str,
        skill_runtime: SkillRuntime,
        input_model: type[BaseModel] | None = None,
        output_model: type[BaseModel] | None = None,
        runtime_policy: SubagentRuntimePolicy | None = None,
        catalog_metadata: SubagentCatalogMetadata | None = None,
    ) -> None:
        self.subagent_type = subagent_type
        self.snapshot_type = snapshot_type
        self.skill_name = skill_name
        self.skill_runtime = skill_runtime
        self.input_model = input_model or type(self).input_model
        self.output_model = output_model or type(self).output_model
        self.runtime_policy = runtime_policy or type(self).runtime_policy
        self.catalog_metadata = catalog_metadata or type(self).catalog_metadata

    def run(self, *, query: str, symbol: str | None = None) -> dict[str, Any]:
        try:
            snapshot = self.skill_runtime.run(self.skill_name, query=query, symbol=symbol)
        except Exception as exc:
            return {
                "snapshot_type": self.snapshot_type,
                "snapshot": None,
                "degraded": True,
                "degrade_reason": type(exc).__name__,
            }
        return {
            "snapshot_type": self.snapshot_type,
            "snapshot": snapshot,
            "degraded": False,
            "degrade_reason": None,
        }


class RuntimeRegisteredSkillSubagent:
    runtime_policy = SubagentRuntimePolicy(allow_parallel=True, max_concurrency=4)
    catalog_metadata = SubagentCatalogMetadata(
        summary="Execute one validated local skill as a runtime-registered subagent alias.",
        capabilities=("skill_bridge", "runtime_registration"),
        cost_class="medium",
        latency_class="medium",
        side_effect_level="read_only",
        tags=("runtime_registered", "read_only"),
    )

    def __init__(
        self,
        *,
        subagent_type: str,
        skill_name: str,
        skill_runtime: SkillRuntime,
        input_model: type[BaseModel] | None = None,
        output_model: type[BaseModel] | None = None,
        runtime_policy: SubagentRuntimePolicy | None = None,
        catalog_metadata: SubagentCatalogMetadata | None = None,
    ) -> None:
        self.subagent_type = subagent_type
        self.skill_name = skill_name
        self.skill_runtime = skill_runtime
        self.input_model = input_model
        self.output_model = output_model
        self.runtime_policy = runtime_policy or type(self).runtime_policy
        self.catalog_metadata = catalog_metadata or type(self).catalog_metadata

    def run(self, **kwargs: Any) -> dict[str, Any]:
        result = self.skill_runtime.run(self.skill_name, **kwargs)
        if isinstance(result, dict):
            return dict(result)
        return {"result": result}


class RuntimeRegisteredHttpJsonSubagent:
    runtime_policy = SubagentRuntimePolicy(allow_parallel=True, max_concurrency=2)
    catalog_metadata = SubagentCatalogMetadata(
        summary="Execute one validated HTTP JSON endpoint as a runtime-registered subagent alias.",
        capabilities=("http_adapter_bridge", "runtime_registration"),
        cost_class="medium",
        latency_class="medium",
        side_effect_level="read_only",
        tags=("runtime_registered", "read_only", "http_json"),
    )

    def __init__(
        self,
        *,
        subagent_type: str,
        source_id: str,
        base_url: str,
        endpoint_path: str,
        http_method: str,
        timeout_seconds: float = 20.0,
        input_model: type[BaseModel] | None = None,
        output_model: type[BaseModel] | None = None,
        runtime_policy: SubagentRuntimePolicy | None = None,
        catalog_metadata: SubagentCatalogMetadata | None = None,
    ) -> None:
        self.subagent_type = subagent_type
        self.source_id = source_id
        self.base_url = str(base_url).rstrip("/")
        self.endpoint_path = self._normalize_endpoint_path(endpoint_path)
        self.http_method = str(http_method).strip().upper() or "GET"
        self.timeout_seconds = float(timeout_seconds or 20.0)
        self.input_model = input_model
        self.output_model = output_model
        self.runtime_policy = runtime_policy or type(self).runtime_policy
        self.catalog_metadata = catalog_metadata or type(self).catalog_metadata

    def run(self, **kwargs: Any) -> dict[str, Any]:
        url = urljoin(f"{self.base_url}/", self.endpoint_path.lstrip("/"))
        request_kwargs: dict[str, Any] = {"timeout": self.timeout_seconds}
        if self.http_method == "GET":
            request_kwargs["params"] = kwargs
        else:
            request_kwargs["json"] = kwargs
        response = requests.request(self.http_method, url, **request_kwargs)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return dict(payload)
        return {"result": payload}

    @staticmethod
    def _normalize_endpoint_path(endpoint_path: str) -> str:
        normalized = str(endpoint_path or "").strip()
        if normalized.startswith("/"):
            return normalized
        return f"/{normalized}"


class RuntimeRegisteredMcpToolSubagent:
    runtime_policy = SubagentRuntimePolicy(allow_parallel=True, max_concurrency=1)
    catalog_metadata = SubagentCatalogMetadata(
        summary="Execute one validated MCP tool as a runtime-registered subagent alias.",
        capabilities=("mcp_tool_bridge", "runtime_registration"),
        cost_class="medium",
        latency_class="medium",
        side_effect_level="read_only",
        tags=("runtime_registered", "read_only", "mcp"),
    )

    def __init__(
        self,
        *,
        subagent_type: str,
        server_source: McpServerSource,
        tool_name: str,
        timeout_seconds: float = 20.0,
        input_model: type[BaseModel] | None = None,
        output_model: type[BaseModel] | None = None,
        runtime_policy: SubagentRuntimePolicy | None = None,
        catalog_metadata: SubagentCatalogMetadata | None = None,
    ) -> None:
        self.subagent_type = subagent_type
        self.server_source = server_source
        self.tool_name = tool_name
        self.timeout_seconds = float(timeout_seconds or server_source.timeout_seconds or 20.0)
        self.input_model = input_model
        self.output_model = output_model
        self.runtime_policy = runtime_policy or type(self).runtime_policy
        self.catalog_metadata = catalog_metadata or type(self).catalog_metadata

    def run(self, **kwargs: Any) -> dict[str, Any]:
        payload = _call_mcp_tool(
            server_source=self.server_source,
            tool_name=self.tool_name,
            arguments=kwargs,
            timeout_seconds=self.timeout_seconds,
        )
        if isinstance(payload, dict):
            return dict(payload)
        return {"result": payload}


def build_runtime_registered_model(
    *,
    model_name: str,
    schema: dict[str, Any] | None,
) -> type[BaseModel] | None:
    if not isinstance(schema, dict):
        return None
    normalized_type = str(schema.get("type") or "").strip().lower()
    properties = schema.get("properties")
    if normalized_type and normalized_type != "object":
        return None
    if not isinstance(properties, dict):
        return None
    required = {
        str(item).strip()
        for item in list(schema.get("required") or [])
        if str(item).strip()
    }
    field_definitions: dict[str, tuple[Any, Any]] = {}
    for field_name, field_schema in properties.items():
        if not isinstance(field_schema, dict):
            continue
        annotation = _annotation_from_schema(field_schema, model_name=f"{model_name}_{field_name}")
        default = ... if field_name in required else None
        description = str(field_schema.get("description") or "").strip() or None
        field_definitions[str(field_name)] = (
            annotation,
            Field(default=default, description=description),
        )
    return create_model(
        model_name,
        __config__=ConfigDict(extra="forbid"),
        **field_definitions,
    )


def _annotation_from_schema(schema: dict[str, Any], *, model_name: str) -> Any:
    type_decl = schema.get("type")
    allowed_types = [item for item in _normalize_type_decl(type_decl) if item]
    nullable = "null" in allowed_types
    normalized_types = [item for item in allowed_types if item != "null"]
    if not normalized_types:
        annotation = Any
    elif len(normalized_types) > 1:
        annotation = Any
    else:
        annotation = _annotation_for_single_type(
            normalized_types[0],
            schema=schema,
            model_name=model_name,
        )
    return annotation | None if nullable else annotation


def _annotation_for_single_type(
    normalized_type: str,
    *,
    schema: dict[str, Any],
    model_name: str,
) -> Any:
    if normalized_type == "string":
        return str
    if normalized_type == "integer":
        return int
    if normalized_type == "number":
        return float
    if normalized_type == "boolean":
        return bool
    if normalized_type == "array":
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            return list[_annotation_from_schema(item_schema, model_name=f"{model_name}_item")]
        return list[Any]
    if normalized_type == "object":
        nested_model = build_runtime_registered_model(model_name=model_name, schema=schema)
        return nested_model or dict[str, Any]
    return Any


def _normalize_type_decl(type_decl: Any) -> list[str]:
    if isinstance(type_decl, str):
        return [type_decl.strip().lower()]
    if isinstance(type_decl, list):
        return [str(item).strip().lower() for item in type_decl if str(item).strip()]
    return []


def _call_mcp_tool(
    *,
    server_source: McpServerSource,
    tool_name: str,
    arguments: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any] | Any:
    request_bytes = b"".join(
        _encode_mcp_message(message)
        for message in (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "cognitrade", "version": "0.0.0"},
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": str(tool_name or "").strip(), "arguments": dict(arguments or {})},
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "shutdown",
                "params": {},
            },
            {
                "jsonrpc": "2.0",
                "method": "exit",
                "params": {},
            },
        )
    )
    process = subprocess.Popen(
        [server_source.command, *list(server_source.args)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=server_source.cwd or None,
        env={**os.environ, **dict(server_source.env or {})},
    )
    try:
        stdout, stderr = process.communicate(input=request_bytes, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise RuntimeError(
            f"mcp tool invocation timed out: {server_source.id}/{tool_name}"
        ) from exc
    if process.returncode not in {0, None}:
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"mcp tool invocation failed: {server_source.id}/{tool_name}"
            + (f" stderr={stderr_text}" if stderr_text else "")
        )
    responses = {
        message.get("id"): message
        for message in _parse_mcp_messages(stdout)
        if isinstance(message, dict) and "id" in message
    }
    response = responses.get(2)
    if not isinstance(response, dict):
        raise RuntimeError(f"mcp tool invocation missing tools/call response: {server_source.id}/{tool_name}")
    error = response.get("error")
    if isinstance(error, dict):
        raise RuntimeError(
            f"mcp tool invocation returned error: {server_source.id}/{tool_name} {error.get('message') or error}"
        )
    result = response.get("result")
    if not isinstance(result, dict):
        return {"result": result}
    if bool(result.get("isError")):
        raise RuntimeError(f"mcp tool invocation returned tool error: {server_source.id}/{tool_name}")
    structured_content = result.get("structuredContent")
    if isinstance(structured_content, dict):
        return dict(structured_content)
    content = result.get("content")
    if isinstance(content, list):
        text_content = [
            str(item.get("text") or "").strip()
            for item in content
            if isinstance(item, dict) and str(item.get("type") or "").strip() == "text"
        ]
        if len(text_content) == 1 and text_content[0]:
            try:
                parsed = json.loads(text_content[0])
            except json.JSONDecodeError:
                return {"text": text_content[0]}
            if isinstance(parsed, dict):
                return parsed
            return {"result": parsed}
    return dict(result)


def _encode_mcp_message(message: dict[str, Any]) -> bytes:
    payload = json.dumps(message, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(payload)}\r\nContent-Type: application/json\r\n\r\n".encode("utf-8")
    return header + payload


def _parse_mcp_messages(buffer: bytes) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    offset = 0
    while offset < len(buffer):
        header_end = buffer.find(b"\r\n\r\n", offset)
        if header_end < 0:
            trailing = buffer[offset:].strip()
            if trailing:
                raise RuntimeError("invalid MCP frame: missing header terminator")
            break
        header_blob = buffer[offset:header_end].decode("utf-8", errors="replace")
        headers = {}
        for line in header_blob.split("\r\n"):
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
        content_length = int(headers.get("content-length") or "0")
        body_start = header_end + 4
        body_end = body_start + content_length
        if body_end > len(buffer):
            raise RuntimeError("invalid MCP frame: truncated body")
        payload = json.loads(buffer[body_start:body_end].decode("utf-8"))
        if isinstance(payload, dict):
            messages.append(payload)
        offset = body_end
    return messages


__all__ = [
    "RuntimeRegisteredHttpJsonSubagent",
    "RuntimeRegisteredMcpToolSubagent",
    "RuntimeRegisteredSnapshotSkillInputModel",
    "RuntimeRegisteredSnapshotSkillOutputModel",
    "RuntimeRegisteredSnapshotSkillSubagent",
    "RuntimeRegisteredSkillSubagent",
    "build_runtime_registered_model",
]
