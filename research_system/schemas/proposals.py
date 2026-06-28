from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.id_utils import generate_ulid


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


ProposalType = Literal[
    "DimensionPoolPatch",
    "RetrievalWeightPatch",
    "MemoryLearningUpdate",
    "PromptPatchProposal",
]
ProposalStatus = Literal["pending", "approved", "rejected"]


class ProposalDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(default_factory=generate_ulid)
    proposal_type: ProposalType
    status: ProposalStatus = "pending"
    title: str
    summary: str
    created_from_run_id: str | None = None
    created_from_review_id: str | None = None
    author_agent: str
    sample_size: int
    benchmark_scope: str
    before_metrics: dict[str, Any] = Field(default_factory=dict)
    after_metrics: dict[str, Any] = Field(default_factory=dict)
    evidence_summary: str
    related_entry_ids: list[str] = Field(default_factory=list)
    reviewer: str | None = None
    reviewed_at: datetime | None = None
    review_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class DimensionPoolPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    dimensions_to_add: list[dict[str, Any]] = Field(default_factory=list)
    dimensions_to_deprecate: list[str] = Field(default_factory=list)


class PromptPatchProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    target_prompt: str
    rationale: str
    patch: str


class RetrievalWeightPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    score_weights: dict[str, float] = Field(default_factory=dict)


class MemoryLearningUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    memory_type: str
    content: dict[str, Any] = Field(default_factory=dict)
