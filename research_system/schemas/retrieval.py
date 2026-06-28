from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RetrievalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str
    symbol: str | None = None
    sector: str | None = None
    themes: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    time_range: dict[str, Any] = Field(default_factory=dict)


class RetrievedEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    title: str
    summary: str
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float | None = None


class RetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: RetrievalQuery
    evidences: list[RetrievedEvidence] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utc_now)
