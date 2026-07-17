from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.core.id_utils import generate_ulid
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.state_machine import RunStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


TurnTaskType = Literal["regular", "review", "compact"]
TurnExecutionMode = Literal["core", "task", "graph"]
# The transport envelope already contains the complete canonical input. Keep a
# domain name for the Turn layer without introducing a second wire schema.
TurnInput = RequestEnvelope


class TurnConfig(BaseModel):
    """Immutable configuration selected for one root Turn."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    config_version: int = Field(default=1, ge=1)
    task_type: TurnTaskType = "regular"
    execution_mode: TurnExecutionMode = "core"
    task_policy_ref: str | None = None
    graph_config: dict[str, Any] | None = None
    hook_config: dict[str, Any] | None = None
    follow_up_config: dict[str, Any] | None = None
    plugin_refs: list[str] = Field(default_factory=list)


class TurnSnapshot(BaseModel):
    """Canonical root Turn checkpoint shared by Core and Graph execution."""

    model_config = ConfigDict(extra="forbid")

    turn_id: str = Field(default_factory=generate_ulid)
    run_id: str
    thread_id: str
    request_id: str
    task_type: TurnTaskType = "regular"
    execution_mode: TurnExecutionMode = "core"
    status: RunStatus = "received"
    config: TurnConfig = Field(default_factory=TurnConfig)
    config_hash: str = ""
    sequence: int = Field(default=0, ge=0)
    task_state_ref: str | None = None
    graph_state_ref: str | None = None
    last_event_id: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TaskOutcome(BaseModel):
    """Normalized result returned by a RegularTask step executor."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    execution_mode: TurnExecutionMode
    status: str
    result: Any
    graph_state_ref: str | None = None
    continuation: dict[str, Any] | None = None
    error: str | None = None


__all__ = [
    "TaskOutcome",
    "TurnConfig",
    "TurnExecutionMode",
    "TurnInput",
    "TurnSnapshot",
    "TurnTaskType",
]
