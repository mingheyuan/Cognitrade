from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.core.id_utils import generate_ulid


class ActionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(default_factory=generate_ulid)
    node_id: str
    phase: str
    agent: str
    objective: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "completed", "skipped"] = "pending"
    tags: list[str] = Field(default_factory=list)


class ActionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(default_factory=generate_ulid)
    intent: str
    strategy: Literal["static", "dynamic"] = "static"
    actions: list[ActionSpec] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    entry_strategy: Literal["rule", "llm", "explicit_intent"] = "rule"
    entry_reason: str | None = None
    subagent_required: bool = True
    dispatch_trace: dict[str, Any] = Field(default_factory=dict)


class SupervisorDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_type: Literal["continue", "stop"] = "continue"
    next_actions: list[ActionSpec] = Field(default_factory=list)
    completed_action_ids: list[str] = Field(default_factory=list)
    skipped_action_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
