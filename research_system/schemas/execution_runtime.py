from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from research_system.schemas.artifacts import ArtifactDraft
from research_system.schemas.execution_graph import ExecutionGraph
from research_system.schemas.state_machine import NodeStatus, RunStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ExecutionUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tokens: int = Field(default=0, ge=0)
    cost: float = Field(default=0.0, ge=0.0)
    duration_seconds: float = Field(default=0.0, ge=0.0)


class SubagentControlRequest(BaseModel):
    """A governed suggestion from a leaf Subagent to the Main Agent."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    kind: Literal[
        "request_replan",
        "request_clarification",
        "request_retry",
        "request_tool",
        "stop",
    ]
    reason: str
    requested_artifact_types: list[str] = Field(default_factory=list)
    preferred_capabilities: list[str] = Field(default_factory=list)
    target_node_id: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_request(self) -> "SubagentControlRequest":
        if not self.reason.strip():
            raise ValueError("subagent control request reason is required")
        if self.kind == "request_replan" and not (
            self.requested_artifact_types
            or self.preferred_capabilities
            or self.target_node_id
        ):
            raise ValueError(
                "request_replan requires an artifact, capability, or target node"
            )
        return self


class NodeExecutionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifacts: list[ArtifactDraft] = Field(default_factory=list)
    usage: ExecutionUsage = Field(default_factory=ExecutionUsage)
    notes: list[str] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)
    control_requests: list[SubagentControlRequest] = Field(default_factory=list)


class NodeExecutionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    status: NodeStatus = "pending"
    attempt_count: int = Field(default=0, ge=0)
    input_artifact_ids: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
    usage: ExecutionUsage = Field(default_factory=ExecutionUsage)
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class GateEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gate_id: str
    artifact_id: str
    verdict: Literal["pass", "fail", "needs_review"]
    reason: str
    blocking: bool = True
    evidence_refs: list[dict[str, object]] = Field(default_factory=list)
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    evaluator: str | None = None
    evaluated_at: datetime = Field(default_factory=utc_now)


class GraphPatchImpact(BaseModel):
    """Persisted invalidation scope produced by one graph revision change."""

    model_config = ConfigDict(extra="forbid")

    patch_id: str
    revision: int = Field(ge=1)
    invalidated_node_ids: list[str] = Field(default_factory=list)
    invalidated_artifact_ids: list[str] = Field(default_factory=list)
    reason: str


class ExecutionRunSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    thread_id: str
    request_id: str
    user_id: str | None = None
    tenant_id: str | None = None
    graph: ExecutionGraph
    status: RunStatus = "ready"
    node_states: dict[str, NodeExecutionState] = Field(default_factory=dict)
    usage: ExecutionUsage = Field(default_factory=ExecutionUsage)
    tool_call_count: int = Field(default=0, ge=0)
    gate_evaluations: list[GateEvaluation] = Field(default_factory=list)
    applied_patch_ids: list[str] = Field(default_factory=list)
    pending_control_requests: list[SubagentControlRequest] = Field(default_factory=list)
    clarification_question: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    patch_impacts: list[GraphPatchImpact] = Field(default_factory=list)
    invalidated_artifact_ids: list[str] = Field(default_factory=list)
    pending_approval_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ExecutionRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: ExecutionRunSnapshot
    artifact_ids: list[str] = Field(default_factory=list)
    final_artifact_ids: list[str] = Field(default_factory=list)


class GraphStepOutcome(BaseModel):
    """One scheduler step projected into the enclosing RegularExecutor loop."""

    model_config = ConfigDict(extra="forbid")

    result: ExecutionRunResult
    progressed: bool = False
    safe_point: bool = True
    should_continue: bool = False


__all__ = [
    "ExecutionRunResult",
    "GraphStepOutcome",
    "ExecutionRunSnapshot",
    "ExecutionUsage",
    "GateEvaluation",
    "GraphPatchImpact",
    "NodeExecutionOutput",
    "NodeExecutionState",
    "SubagentControlRequest",
]
