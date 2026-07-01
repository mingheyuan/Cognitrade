from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SubagentInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subagent_type: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    objective: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class SubagentExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subagent_type: str
    status: Literal["completed", "skipped", "failed"] = "completed"
    objective: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    output_keys: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
