from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

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
class SubagentDefinition:
    subagent_type: str
    input_model: type[BaseModel] | None = None
    output_model: type[BaseModel] | None = None
    runtime_policy: SubagentRuntimePolicy = field(default_factory=SubagentRuntimePolicy)


class SupportsSubagent(Protocol):
    subagent_type: str
    input_model: type[BaseModel] | None
    output_model: type[BaseModel] | None
    runtime_policy: SubagentRuntimePolicy

    def run(self, **kwargs: Any) -> dict[str, Any]:
        ...


def build_subagent_definition(subagent: SupportsSubagent) -> SubagentDefinition:
    return SubagentDefinition(
        subagent_type=subagent.subagent_type,
        input_model=getattr(subagent, "input_model", None),
        output_model=getattr(subagent, "output_model", None),
        runtime_policy=getattr(subagent, "runtime_policy", SubagentRuntimePolicy()),
    )
