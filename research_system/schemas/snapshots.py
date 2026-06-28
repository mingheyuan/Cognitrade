from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


SnapshotType = Literal["market", "fundamental", "notice", "sector"]


class SnapshotSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill: str
    vendor: str
    from_cache: bool = False


class VerifiedSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_type: SnapshotType
    symbol: str | None = None
    sector: str | None = None
    theme: str | None = None
    query: str
    snapshot_date: str
    facts: dict[str, Any] = Field(default_factory=dict)
    source: SnapshotSource
    degraded: bool = False
    degrade_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
