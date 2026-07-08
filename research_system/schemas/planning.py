from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    depends_on: list[str] = Field(default_factory=list)
    max_retries: int = Field(default=0, ge=0)
    failure_mode: Literal["halt", "isolate"] = "halt"
    cache_scope: Literal["run", "thread", "global"] | None = None
    cache_key: str | None = None
    replayable: bool = True


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


class ActionPlanPatchOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(default_factory=generate_ulid)
    op_type: Literal[
        "insert_action",
        "remove_action",
        "rewire_dependency",
        "replace_subgraph",
        "rerun_from",
        "mark_terminal",
        "request_replan",
    ]
    target_node_id: str | None = None
    action: ActionSpec | None = None
    replacement_actions: list[ActionSpec] = Field(default_factory=list)
    new_depends_on: list[str] = Field(default_factory=list)
    remove_depends_on: list[str] = Field(default_factory=list)
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_operation_shape(self) -> "ActionPlanPatchOperation":
        if self.op_type == "insert_action" and self.action is None:
            raise ValueError("insert_action requires action")
        if self.op_type == "remove_action" and not self.target_node_id:
            raise ValueError("remove_action requires target_node_id")
        if self.op_type == "rewire_dependency" and not self.target_node_id:
            raise ValueError("rewire_dependency requires target_node_id")
        if self.op_type == "replace_subgraph":
            if not self.target_node_id:
                raise ValueError("replace_subgraph requires target_node_id")
            if not self.replacement_actions:
                raise ValueError("replace_subgraph requires replacement_actions")
        if self.op_type in {"rerun_from", "mark_terminal"} and not self.target_node_id:
            raise ValueError(f"{self.op_type} requires target_node_id")
        return self


class ActionPlanPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch_id: str = Field(default_factory=generate_ulid)
    decision_type: Literal["continue", "stop", "replan"] = "continue"
    operations: list[ActionPlanPatchOperation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    policy_source: Literal["rule", "llm", "hybrid", "explicit"] = "rule"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    termination_reason: str | None = None


class SupervisorDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_type: Literal["continue", "stop", "replan"] = "continue"
    next_actions: list[ActionSpec] = Field(default_factory=list)
    completed_action_ids: list[str] = Field(default_factory=list)
    skipped_action_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    plan_patch: ActionPlanPatch | None = None
