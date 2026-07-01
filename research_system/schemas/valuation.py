from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ValuationPeer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    name: str | None = None
    sector: str | None = None
    theme: str | None = None
    latest_price: float | None = None
    latest_pct_change: float | None = None
    latest_amount: float | None = None
    revenue_proxy: float | None = None
    peer_score: float | None = None
    percentile: float | None = None
    notes: list[str] = Field(default_factory=list)


class ValuationBand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    low: float | None = None
    base: float | None = None
    high: float | None = None
    currency: str = "CNY"


class CompsModelResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    methodology_type: Literal["comps"] = "comps"
    target_symbol: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    assumptions: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    sensitivity: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    peer_group: list[ValuationPeer] = Field(default_factory=list)
    peer_group_tags: list[str] = Field(default_factory=list)
    assumption_tags: list[str] = Field(default_factory=list)
    sensitivity_tags: list[str] = Field(default_factory=list)
    valuation_band: ValuationBand = Field(default_factory=ValuationBand)
    created_at: datetime = Field(default_factory=utc_now)


class DCFModelResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    methodology_type: Literal["dcf"] = "dcf"
    target_symbol: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    assumptions: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    sensitivity: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    assumption_tags: list[str] = Field(default_factory=list)
    sensitivity_tags: list[str] = Field(default_factory=list)
    valuation_band: ValuationBand = Field(default_factory=ValuationBand)
    created_at: datetime = Field(default_factory=utc_now)


class CashflowResilienceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    methodology_type: Literal["cashflow_resilience"] = "cashflow_resilience"
    target_symbol: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    assumptions: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    sensitivity: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    assumption_tags: list[str] = Field(default_factory=list)
    sensitivity_tags: list[str] = Field(default_factory=list)
    valuation_band: ValuationBand = Field(default_factory=ValuationBand)
    created_at: datetime = Field(default_factory=utc_now)
