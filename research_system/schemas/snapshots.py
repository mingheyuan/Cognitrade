from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


SnapshotType = Literal[
    "market",
    "fundamental",
    "notice",
    "sector",
    "lhb",
    "capital_flow",
    "sentiment",
    "events",
    "policy",
    "kline",
    "macro",
    "peers",
    "chain",
    "research",
    "industry",
    "materials",
    "futures",
    "valuation",
    "governance",
    "moat",
    "trap",
    "contests",
    "fund_holders",
    "similar_stocks",
]


class SnapshotSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill: str
    vendor: str
    from_cache: bool = False
    provider_id: str | None = None
    source_id: str | None = None
    chain_position: int | None = None
    dim: str | None = None


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
    primary_source: SnapshotSource | None = None
    fallback_sources: list[SnapshotSource] = Field(default_factory=list)
    field_sources: dict[str, str] = Field(default_factory=dict)
    fallback_applied: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    source_confidence: str = "medium"
    source_trace: list[dict[str, Any]] = Field(default_factory=list)
    degraded: bool = False
    degrade_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
