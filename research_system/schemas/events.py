from __future__ import annotations

from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, model_validator

from research_system.runtime.core.id_utils import generate_ulid


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EventEnvelope(BaseModel):
    """Canonical event envelope for the Agent OS execution stream.

    Event-specific fields remain at the top level for UI and API consumers.
    ``extra=allow`` makes the envelope additive while the fields
    below are mandatory for every new canonical event.
    """

    model_config = ConfigDict(extra="allow")

    event_id: str = Field(default_factory=generate_ulid)
    event_type: str
    created_at: datetime = Field(default_factory=utc_now)
    run_id: str
    thread_id: str
    request_id: str
    turn_id: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    parent_turn_id: str | None = None
    task_type: str | None = None
    graph_id: str | None = None
    execution_mode: str | None = None
    sequence: int = Field(default=0, ge=0)
    graph_revision: int | None = None
    actor: str = "agent_os"
    node_id: str | None = None
    tool_call_id: str | None = None
    operation_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    child_turn_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_identity(self) -> "EventEnvelope":
        for field in ("event_id", "event_type", "run_id", "thread_id", "request_id", "actor"):
            value = str(getattr(self, field) or "").strip()
            if not value:
                raise ValueError(f"canonical event {field} is required")
            setattr(self, field, value)
        self.artifact_ids = list(
            dict.fromkeys(str(item) for item in self.artifact_ids if str(item).strip())
        )
        return self


__all__ = ["EventEnvelope"]
