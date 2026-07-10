from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.core.id_utils import generate_ulid


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class HumanApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(default_factory=generate_ulid)
    run_id: str
    node_id: str
    capability: str
    approved_by: str
    reason: str
    status: Literal["approved", "revoked"] = "approved"
    created_at: datetime = Field(default_factory=utc_now)


__all__ = ["HumanApproval"]
