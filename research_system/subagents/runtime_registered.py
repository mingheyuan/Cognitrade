from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import requests

from pydantic import BaseModel, ConfigDict, Field, create_model

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
        runtime_policy: SubagentRuntimePolicy | None = None,
        catalog_metadata: SubagentCatalogMetadata | None = None,
    ) -> None:
        self.subagent_type = subagent_type
        self.snapshot_type = snapshot_type
        self.skill_name = skill_name
        self.skill_runtime = skill_runtime
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


__all__ = [
    "RuntimeRegisteredHttpJsonSubagent",
    "RuntimeRegisteredSnapshotSkillInputModel",
    "RuntimeRegisteredSnapshotSkillOutputModel",
    "RuntimeRegisteredSnapshotSkillSubagent",
    "RuntimeRegisteredSkillSubagent",
    "build_runtime_registered_model",
]
