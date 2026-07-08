from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel


@dataclass(frozen=True)
class SubagentRuntimePolicy:
    requires_llm: bool = False
    fallback_subagent_type: str | None = None
    error_fallback_subagent_type: str | None = None
    allow_parallel: bool = False
    max_concurrency: int | None = None
    timeout_seconds: float | None = None
    max_retries: int = 0


@dataclass(frozen=True)
class SubagentCatalogMetadata:
    summary: str | None = None
    capabilities: tuple[str, ...] = ()
    stage: str | None = None
    cost_class: Literal["low", "medium", "high"] | None = None
    latency_class: Literal["low", "medium", "high"] | None = None
    side_effect_level: Literal["none", "read_only", "write_local", "write_external"] = "none"
    suitable_intents: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class SubagentDefinition:
    subagent_type: str
    input_model: type[BaseModel] | None = None
    output_model: type[BaseModel] | None = None
    runtime_policy: SubagentRuntimePolicy = field(default_factory=SubagentRuntimePolicy)
    catalog_metadata: SubagentCatalogMetadata = field(default_factory=SubagentCatalogMetadata)


class SupportsSubagent(Protocol):
    subagent_type: str
    input_model: type[BaseModel] | None
    output_model: type[BaseModel] | None
    runtime_policy: SubagentRuntimePolicy
    catalog_metadata: SubagentCatalogMetadata

    def run(self, **kwargs: Any) -> dict[str, Any]:
        ...


def build_subagent_definition(subagent: SupportsSubagent) -> SubagentDefinition:
    return SubagentDefinition(
        subagent_type=subagent.subagent_type,
        input_model=getattr(subagent, "input_model", None),
        output_model=getattr(subagent, "output_model", None),
        runtime_policy=getattr(subagent, "runtime_policy", SubagentRuntimePolicy()),
        catalog_metadata=getattr(subagent, "catalog_metadata", SubagentCatalogMetadata()),
    )
