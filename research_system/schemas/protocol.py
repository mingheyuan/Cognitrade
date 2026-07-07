from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.core.id_utils import generate_ulid


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


ResponseStatus = Literal[
    "accepted",
    "needs_clarification",
    "running",
    "completed",
    "cancelled",
    "failed",
    "timed_out",
]


class ProgressState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_phase: str | None = None
    active_agent: str | None = None
    latest_event: str | None = None
    status_message: str
    percent: int = Field(ge=0, le=100)
    updated_at: datetime = Field(default_factory=utc_now)


class ClarificationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    missing_fields: list[str] = Field(default_factory=list)


class RequestEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(default_factory=generate_ulid)
    intent: str | None = None
    user_query: str
    conversation_context: list[dict[str, Any]] = Field(default_factory=list)
    slots: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    clarification_status: Literal["not_needed", "pending", "resolved"] = "not_needed"
    created_at: datetime = Field(default_factory=utc_now)


class ResponseEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    run_id: str | None = None
    intent: str | None = None
    status: ResponseStatus
    message: str
    clarification: ClarificationPayload | None = None
    progress: ProgressState
    result: dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
