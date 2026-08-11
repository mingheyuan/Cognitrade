from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
