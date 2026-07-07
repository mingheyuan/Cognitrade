from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.core.id_utils import generate_ulid


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


BlackboardKind = Literal[
    "source_record",
    "verified_snapshot",
    "logic_block",
    "distilled_dimension",
    "retrieved_evidence",
    "report_fragment",
    "review_finding",
    "proposal_draft",
]

BlackboardStatus = Literal["active", "superseded", "needs_review"]
MailboxMessageType = Literal["supplement_request", "challenge", "review_feedback"]
MailboxStatus = Literal["pending", "read", "resolved"]
ThreadStatus = Literal["ready", "running", "waiting", "completed", "failed", "cancelled"]
TurnStatus = Literal["ready", "running", "completed", "failed", "cancelled"]
ItemStatus = Literal["queued", "running", "completed", "failed", "degraded", "cancelled"]
OrchestratorStatus = Literal[
    "received",
    "identifying_intent",
    "needs_clarification",
    "ready",
    "running",
    "waiting_external",
    "gated",
    "completed",
    "cancelled",
    "failed",
    "timed_out",
]
GateVerdictType = Literal["pass", "fail", "needs_review"]


class BlackboardEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str = Field(default_factory=generate_ulid)
    run_id: str
    thread_id: str
    request_id: str
    kind: BlackboardKind
    agent: str
    phase: str
    status: BlackboardStatus = "active"
    payload: dict[str, Any]
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    risk_flags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @classmethod
    def model_validate(cls, obj, *args, **kwargs):
        if isinstance(obj, dict) and "thread_id" not in obj:
            obj = {**obj, "thread_id": str(obj.get("run_id") or "")}
        return super().model_validate(obj, *args, **kwargs)


class MailboxMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(default_factory=generate_ulid)
    run_id: str
    from_agent: str
    to_agent: str
    message_type: MailboxMessageType
    payload: dict[str, Any]
    status: MailboxStatus = "pending"
    created_at: datetime = Field(default_factory=utc_now)


class RuntimeItemRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(default_factory=generate_ulid)
    thread_id: str
    turn_id: str
    run_id: str
    request_id: str
    item_type: Literal["action", "agent", "subagent", "thread", "memory", "degradation"]
    status: ItemStatus = "queued"
    agent: str | None = None
    phase: str | None = None
    title: str
    objective: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    degrade_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RuntimeTurnState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: str = Field(default_factory=generate_ulid)
    thread_id: str
    run_id: str
    request_id: str
    turn_index: int = Field(ge=1)
    status: TurnStatus = "ready"
    intent: str
    objective: str | None = None
    current_item_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RuntimeThreadState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(default_factory=generate_ulid)
    run_id: str
    request_id: str
    intent: str
    status: ThreadStatus = "ready"
    parent_thread_id: str | None = None
    active_turn_id: str | None = None
    total_turns: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RunStateSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    request_id: str
    intent: str
    status: OrchestratorStatus
    thread_id: str | None = None
    active_turn_id: str | None = None
    active_item_id: str | None = None
    current_phase: str | None = None
    active_agent: str | None = None
    pending_nodes: list[str] = Field(default_factory=list)
    completed_nodes: list[str] = Field(default_factory=list)
    last_event_id: str | None = None
    error_summary: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class GateVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gate_id: str = Field(default_factory=generate_ulid)
    run_id: str
    gate_type: str
    verdict: GateVerdictType
    reason: str
    blocking_items: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class RunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=generate_ulid)
    request_id: str
    run_id: str
    intent: str
    event_type: str
    phase: str | None = None
    agent: str | None = None
    message: str
    payload: dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class LogRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    log_id: str = Field(default_factory=generate_ulid)
    run_id: str
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    component: str
    message: str
    context: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
