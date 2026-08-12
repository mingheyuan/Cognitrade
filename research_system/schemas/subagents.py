from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


__all__ = ["RoleContract"]
