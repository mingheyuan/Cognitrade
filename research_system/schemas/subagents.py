from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SubagentInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subagent_type: str
    invocation_id: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    objective: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    requires_llm: bool = False
    fallback_subagent_type: str | None = None
    fallback_inputs: dict[str, Any] = Field(default_factory=dict)
    fallback_objective: str | None = None
    fallback_context: dict[str, Any] = Field(default_factory=dict)
    error_fallback_subagent_type: str | None = None
    error_fallback_inputs: dict[str, Any] = Field(default_factory=dict)
    error_fallback_objective: str | None = None
    error_fallback_context: dict[str, Any] = Field(default_factory=dict)


class SubagentExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subagent_type: str
    status: Literal["completed", "skipped", "failed"] = "completed"
    objective: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    output_keys: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
