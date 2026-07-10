from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from research_system.schemas.artifacts import ArtifactDraft
from research_system.schemas.execution_graph import ExecutionGraph


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ExecutionUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tokens: int = Field(default=0, ge=0)
    cost: float = Field(default=0.0, ge=0.0)
    duration_seconds: float = Field(default=0.0, ge=0.0)


class NodeExecutionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifacts: list[ArtifactDraft] = Field(default_factory=list)
    usage: ExecutionUsage = Field(default_factory=ExecutionUsage)
    notes: list[str] = Field(default_factory=list)


class NodeExecutionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    status: Literal[
        "pending",
        "running",
        "waiting",
        "completed",
        "skipped",
        "failed",
        "cancelled",
    ] = "pending"
    attempt_count: int = Field(default=0, ge=0)
    input_artifact_ids: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
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
    evaluated_at: datetime = Field(default_factory=utc_now)


class ExecutionRunSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    thread_id: str
    request_id: str
    graph: ExecutionGraph
    status: Literal[
        "ready",
        "running",
        "paused",
        "completed",
        "failed",
        "cancelled",
        "gated",
    ] = "ready"
    node_states: dict[str, NodeExecutionState] = Field(default_factory=dict)
    usage: ExecutionUsage = Field(default_factory=ExecutionUsage)
    gate_evaluations: list[GateEvaluation] = Field(default_factory=list)
    applied_patch_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ExecutionRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: ExecutionRunSnapshot
    artifact_ids: list[str] = Field(default_factory=list)
    final_artifact_ids: list[str] = Field(default_factory=list)


__all__ = [
    "ExecutionRunResult",
    "ExecutionRunSnapshot",
    "ExecutionUsage",
    "GateEvaluation",
    "NodeExecutionOutput",
    "NodeExecutionState",
]
