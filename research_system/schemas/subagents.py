from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from research_system.runtime.core.id_utils import generate_ulid


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
    invocation_id: str | None = None
    status: Literal["completed", "skipped", "degraded", "failed"] = "completed"
    objective: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    output_keys: list[str] = Field(default_factory=list)
    degrade_reason: str | None = None
    degrade_reasons: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SubagentCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subagent_type: str
    summary: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    stage: str | None = None
    cost_class: Literal["low", "medium", "high"] | None = None
    latency_class: Literal["low", "medium", "high"] | None = None
    side_effect_level: Literal["none", "read_only", "write_local", "write_external"] = "none"
    suitable_intents: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    requires_llm: bool = False
    allow_parallel: bool = False
    max_concurrency: int | None = None
    fallback_subagent_type: str | None = None
    error_fallback_subagent_type: str | None = None
    input_schema_name: str | None = None
    output_schema_name: str | None = None
    input_json_schema: dict[str, Any] | None = None
    output_json_schema: dict[str, Any] | None = None


class SubagentPlanNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    subagent_type: str
    objective: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    failure_mode: Literal["raise", "isolate"] = "raise"
    allow_partial_dependencies: bool = False
    max_attempts: int = Field(default=1, ge=1)
    replayable: bool = True
    cache_scope: Literal["run", "thread", "global"] | None = None
    cache_key: str | None = None
    notes: list[str] = Field(default_factory=list)


class SubagentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(default_factory=generate_ulid)
    intent: str | None = None
    phase: str | None = None
    parent_action_id: str | None = None
    parent_node_id: str | None = None
    strategy: Literal["catalog", "rule", "llm", "hybrid"] = "catalog"
    nodes: list[SubagentPlanNode] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    catalog_trace: dict[str, Any] = Field(default_factory=dict)


class SubagentGraphPatchOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(default_factory=generate_ulid)
    op_type: Literal[
        "insert_subagent",
        "remove_node",
        "rewire_dependency",
        "replace_subgraph",
        "rerun_from",
        "mark_terminal",
        "request_replan",
    ]
    target_node_id: str | None = None
    node: SubagentPlanNode | None = None
    replacement_nodes: list[SubagentPlanNode] = Field(default_factory=list)
    new_depends_on: list[str] = Field(default_factory=list)
    remove_depends_on: list[str] = Field(default_factory=list)
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_operation_shape(self) -> "SubagentGraphPatchOperation":
        if self.op_type == "insert_subagent" and self.node is None:
            raise ValueError("insert_subagent requires node")
        if self.op_type == "remove_node" and not self.target_node_id:
            raise ValueError("remove_node requires target_node_id")
        if self.op_type == "rewire_dependency" and not self.target_node_id:
            raise ValueError("rewire_dependency requires target_node_id")
        if self.op_type == "replace_subgraph":
            if not self.target_node_id:
                raise ValueError("replace_subgraph requires target_node_id")
            if not self.replacement_nodes:
                raise ValueError("replace_subgraph requires replacement_nodes")
        if self.op_type in {"rerun_from", "mark_terminal"} and not self.target_node_id:
            raise ValueError(f"{self.op_type} requires target_node_id")
        return self


class SubagentGraphPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch_id: str = Field(default_factory=generate_ulid)
    decision_type: Literal["continue", "stop", "replan"] = "continue"
    operations: list[SubagentGraphPatchOperation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    policy_source: Literal["rule", "llm", "hybrid", "explicit"] = "rule"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    termination_reason: str | None = None
