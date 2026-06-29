from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BaseTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    intent: str
    user_query: str
    slots: dict = Field(default_factory=dict)
    constraints: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class StockCheckRequest(BaseTaskRequest):
    intent: Literal["stock_check"] = "stock_check"
    symbol: str | None = None


class StockScreenRequest(BaseTaskRequest):
    intent: Literal["stock_screen"] = "stock_screen"
    sector: str | None = None
    investment_cycle: str | None = None


class SectorResearchRequest(BaseTaskRequest):
    intent: Literal["sector_research"] = "sector_research"
    sector: str | None = None


class DistillKolLogicRequest(BaseTaskRequest):
    intent: Literal["distill_kol_logic"] = "distill_kol_logic"
    source_ref: str | None = None


class ClarifyRequest(BaseTaskRequest):
    intent: Literal["clarify"] = "clarify"
    missing_fields: list[str] = Field(default_factory=list)


class ReviewThesisRequest(BaseTaskRequest):
    intent: Literal["review_thesis"] = "review_thesis"


class MethodologyEvolutionRequest(BaseTaskRequest):
    intent: Literal["methodology_evolution"] = "methodology_evolution"
