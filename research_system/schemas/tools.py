from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ToolNamespace = Literal["core", "pack", "external"]


class ToolDefinition(BaseModel):
    """Canonical static contract for every Agent OS Tool."""

    model_config = ConfigDict(extra="forbid")

    # ``name`` is the runtime registry key. ``tool_id`` is accepted because
    # Pack manifests historically used that spelling; both are normalized.
    name: str
    tool_id: str | None = None
    namespace: ToolNamespace | None = None
    version: str = "1"
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    implementation: str | None = None
    permissions: list[str] = Field(default_factory=list)
    side_effect_level: Literal["none", "read_only", "write_local", "write_external"] = "none"
    parallel_safe: bool = False
    max_concurrency: int | None = Field(default=None, ge=1)
    timeout_seconds: float | None = Field(default=None, gt=0.0)
    max_retries: int = Field(default=0, ge=0)
    requires_approval: bool = False
    idempotency_required: bool = False
    rate_limit_per_minute: int | None = Field(default=None, ge=1)
    source_refs_required: bool = False
    allowed_callers: list[Literal["main_agent", "subagent", "system"]] = Field(
        default_factory=lambda: ["main_agent", "subagent"]
    )
    resource_key_fields: list[str] = Field(default_factory=list)
    resource_lease_seconds: float = Field(default=30.0, gt=0.0)

    @model_validator(mode="before")
    @classmethod
    def normalize_identifier(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        if not payload.get("name") and payload.get("tool_id"):
            payload["name"] = payload["tool_id"]
        return payload

    @model_validator(mode="after")
    def validate_identifier(self) -> "ToolDefinition":
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("tool name is required")
        self.tool_id = str(self.tool_id or self.name).strip()
        if not self.tool_id:
            raise ValueError("tool id is required")
        if self.tool_id != self.name:
            raise ValueError("tool name and tool_id must identify the same registry entry")
        self.version = self.version.strip()
        if not self.version:
            raise ValueError("tool version is required")
        self.description = self.description.strip()
        if not self.description:
            raise ValueError("tool description is required")
        return self


__all__ = ["ToolDefinition", "ToolNamespace"]
