from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.core.id_utils import generate_ulid


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


OrchestratorStatus = Literal[
    "received",
    "accepted",
    "identifying_intent",
    "needs_clarification",
    "ready",
    "running",
    "paused",
    "waiting_external",
    "waiting_approval",
    "gated",
    "completed",
    "completed_partial",
    "cancelled",
    "failed",
    "timed_out",
]


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
