from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.id_utils import generate_ulid


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


class RunStateSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    request_id: str
    intent: str
    status: OrchestratorStatus
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
