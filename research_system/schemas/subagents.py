from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class RoleContract(BaseModel):
    """Governed capability contract for built-in and Pack roles."""

    model_config = ConfigDict(extra="forbid")

    role_id: str
    role_version: str
    display_name: str
    objective: str
    input_artifact_types: list[str] = Field(default_factory=list)
    output_artifact_types: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)
    allowed_permissions: list[str] = Field(default_factory=list)
    max_model_turns: int = Field(default=1, ge=1)
    timeout_seconds: int = Field(default=30, ge=1)
    requires_approval: bool = False
    can_spawn_child_turn: bool = False
    failure_mode: Literal["halt", "continue", "report"] = "halt"
    source: Literal["builtin", "pack", "runtime", "user"] = "builtin"

    @model_validator(mode="after")
    def validate_tools(self) -> "RoleContract":
        overlap = set(self.allowed_tools) & set(self.denied_tools)
        if overlap:
            raise ValueError(
                f"role tools cannot be both allowed and denied: {sorted(overlap)}"
            )
        return self


class SubagentExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subagent_type: str
    invocation_id: str | None = None
    status: Literal["completed", "skipped", "degraded", "failed"] = "completed"
    objective: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    output_keys: list[str] = Field(default_factory=list)
    degrade_reason: str | None = None
    degrade_reasons: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


__all__ = ["RoleContract", "SubagentExecutionRecord", "SubagentInvocation"]
