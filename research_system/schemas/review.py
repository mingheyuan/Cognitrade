from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.core.id_utils import generate_ulid


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str = Field(default_factory=generate_ulid)
    claim_id: str
    claim_type: Literal["direction", "catalyst", "time_window"] | None = None
    source_run_id: str | None = None
    target_entry_id: str | None = None
    window_type: Literal["trading_days", "calendar_days"]
    window_size: int
    anchor_date: str
    evaluation_start_rule: str
    early_invalidation_rule: str | None = None
    benchmark_scope: str
    benchmark_symbol: str | None = None
    benchmark_name: str | None = None
    expected_direction: Literal["outperform", "underperform", "neutral"] | None = None
    observed_metric: float | None = None
    observed_benchmark_metric: float | None = None
    excess_metric: float | None = None
    verdict: Literal["confirmed", "rejected", "inconclusive"]
    notes: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ClaimVerdictBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: int = 0
    rejected: int = 0
    inconclusive: int = 0


class PosteriorReviewSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_required: bool = False
    finding_count: int = 0
    reviewable_claim_count: int = 0
    claim_type_counts: dict[str, int] = Field(default_factory=dict)
    benchmark_scope_counts: dict[str, int] = Field(default_factory=dict)
    benchmark_scope_explanations: dict[str, str] = Field(default_factory=dict)
    verdict_by_claim_type: dict[str, ClaimVerdictBreakdown] = Field(default_factory=dict)
    overall_verdict: str | None = None
    summary_text: str | None = None
    confirmed: int = 0
    rejected: int = 0
    inconclusive: int = 0
    notes: list[str] = Field(default_factory=list)


class CriticReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(default_factory=generate_ulid)
    target_entry_id: str
    target_kind: Literal["logic_block", "distilled_dimension", "distillation_summary"]
    finding_type: Literal[
        "fallback_distillation",
        "llm_timeout",
        "low_confidence",
        "quoted_attribution",
        "low_information_density",
        "sparse_dimension_coverage",
        "duplicate_claim_cluster",
    ]
    severity: Literal["low", "medium", "high"]
    verdict: Literal["needs_review", "pass"] = "needs_review"
    reason: str
    suggested_action: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class CriticReviewSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_required: bool = False
    finding_count: int = 0
    high_severity_count: int = 0
    medium_severity_count: int = 0
    low_severity_count: int = 0
    finding_types: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
