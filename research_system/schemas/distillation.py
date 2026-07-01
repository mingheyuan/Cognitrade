from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.id_utils import generate_ulid


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


LogicBlockType = Literal["claim", "evidence", "risk", "catalyst"]
SpeakerType = Literal["author", "reply", "quoted", "repost", "reported", "uncertain"]
DirectionType = Literal["bullish", "bearish", "neutral", "uncertain"]


class DistillSourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    title: str | None = None
    content: str
    source_type: str
    source_url: str | None = None
    author: str | None = None
    platform: str | None = None
    published_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class SourceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    excerpt: str


class LogicBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str = Field(default_factory=generate_ulid)
    block_type: LogicBlockType
    source_record_id: str
    source_span: SourceSpan
    speaker_type: SpeakerType = "author"
    attribution_label: str | None = None
    text: str
    normalized_claim: str | None = None
    symbols: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    time_horizon: str | None = None
    direction: DirectionType | None = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class DistilledDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension_id: str | None = None
    dimension_name: str
    canonical_dimension_name: str | None = None
    dimension_family: str | None = None
    dimension_category: str
    alias_hits: list[str] = Field(default_factory=list)
    supported_claim_block_ids: list[str] = Field(default_factory=list)
    evidence_block_ids: list[str] = Field(default_factory=list)
    is_new_candidate: bool = True
    confidence: float = Field(ge=0.0, le=1.0)
    scenario_tags: list[str] = Field(default_factory=list)
    methodology_tags: list[str] = Field(default_factory=list)
    dimension_tags: list[str] = Field(default_factory=list)
    dimension_key: str | None = None
    normalization_notes: list[str] = Field(default_factory=list)


class DistillationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_type: str
    overall_confidence: float = Field(ge=0.0, le=1.0)
    needs_review: bool = False
    major_risks: list[str] = Field(default_factory=list)
    processing_notes: list[str] = Field(default_factory=list)


class DistillationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_record_id: str
    summary: DistillationSummary
    logic_blocks: list[LogicBlock] = Field(default_factory=list)
    distilled_dimensions: list[DistilledDimension] = Field(default_factory=list)
