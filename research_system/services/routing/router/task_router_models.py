from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from research_system.schemas.protocol import ClarificationPayload


RESEARCH_INTENTS = {
    "stock_check",
    "stock_compare",
    "stock_screen",
    "sector_research",
    "distill_kol_logic",
    "review_thesis",
    "methodology_evolution",
}


@dataclass(frozen=True)
class RoutingDecision:
    intent: str | None
    missing_fields: list[str]
    clarification: ClarificationPayload | None
    trace: dict | None = None


@dataclass(frozen=True)
class ChatDispatchDecision:
    mode: Literal["direct_answer", "research"]
    intent: str | None
    reason: str | None = None


class IntentRoutePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal[
        "stock_check",
        "stock_compare",
        "stock_screen",
        "sector_research",
        "distill_kol_logic",
        "review_thesis",
        "methodology_evolution",
        "clarify",
    ]
    missing_fields: list[str] = Field(default_factory=list)
    clarification_question: str | None = None


class ChatDispatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["direct_answer", "research"]
    intent: Literal[
        "stock_check",
        "stock_compare",
        "stock_screen",
        "sector_research",
        "distill_kol_logic",
        "review_thesis",
        "methodology_evolution",
    ] | None = None
    reason: str | None = None
