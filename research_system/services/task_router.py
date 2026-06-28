from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.provider_registry import LLMProviderError, OpenAIProvider
from research_system.schemas.protocol import ClarificationPayload, RequestEnvelope


@dataclass(frozen=True)
class RoutingDecision:
    intent: str | None
    missing_fields: list[str]
    clarification: ClarificationPayload | None


class IntentRoutePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["stock_check", "sector_research", "distill_kol_logic", "review_thesis", "clarify"]
    missing_fields: list[str] = Field(default_factory=list)
    clarification_question: str | None = None


class TaskRouter:
    def __init__(self, llm_provider: OpenAIProvider | None = None) -> None:
        self.llm_provider = llm_provider

    def identify(self, request: RequestEnvelope) -> RoutingDecision:
        if request.intent in {"stock_check", "sector_research", "distill_kol_logic", "review_thesis"}:
            return RoutingDecision(intent=request.intent, missing_fields=[], clarification=None)

        query = request.user_query.strip()
        if not query:
            clarification = ClarificationPayload(
                question="Please provide a stock, sector, distillation, or review request.",
                missing_fields=["user_query"],
            )
            return RoutingDecision(intent=None, missing_fields=["user_query"], clarification=clarification)

        if self.llm_provider and self.llm_provider.available:
            try:
                payload = self.llm_provider.parse(
                    system_prompt=(
                        "You route Chinese or English A-share research requests.\n"
                        "Return stock_check for individual stock research.\n"
                        "Return sector_research for sector, industry, or theme research.\n"
                        "Return distill_kol_logic for KOL distillation, Xueqiu status distillation, or logic extraction requests.\n"
                        "Return review_thesis for posterior review, thesis review, or validation of earlier research claims.\n"
                        "Return clarify only when the request is too ambiguous to route safely."
                    ),
                    user_prompt=(
                        "User query:\n"
                        f"{query}\n\n"
                        "Decide the intent and list any missing fields. "
                        "If clarify is needed, provide one short clarification question."
                    ),
                    schema=IntentRoutePayload,
                    temperature=0.0,
                    reasoning_effort="low",
                )
                if payload.intent == "clarify":
                    clarification = ClarificationPayload(
                        question=payload.clarification_question or "Do you want stock, sector, distillation, or review?",
                        missing_fields=payload.missing_fields or ["intent"],
                    )
                    return RoutingDecision(intent=None, missing_fields=clarification.missing_fields, clarification=clarification)
                return RoutingDecision(intent=payload.intent, missing_fields=payload.missing_fields, clarification=None)
            except LLMProviderError:
                pass

        lowered = query.lower()
        stock_markers = ("股票", "个股", "stock", "ticker", "代码")
        sector_markers = ("行业", "板块", "主题", "sector", "theme", "industry")
        distill_markers = ("蒸馏", "kol", "xueqiu", "雪球", "logic", "观点", "status", "jsonl")
        review_markers = ("复核", "后验", "review", "review_thesis", "验证")

        if any(marker in query for marker in stock_markers[:2]):
            return RoutingDecision(intent="stock_check", missing_fields=[], clarification=None)
        if any(marker in query for marker in sector_markers[:3]):
            return RoutingDecision(intent="sector_research", missing_fields=[], clarification=None)
        if any(marker in query for marker in distill_markers[:4]):
            return RoutingDecision(intent="distill_kol_logic", missing_fields=[], clarification=None)
        if any(marker in query for marker in review_markers[:2]):
            return RoutingDecision(intent="review_thesis", missing_fields=[], clarification=None)
        if any(marker in lowered for marker in ("stock", "ticker")):
            return RoutingDecision(intent="stock_check", missing_fields=[], clarification=None)
        if any(marker in lowered for marker in ("sector", "theme", "industry")):
            return RoutingDecision(intent="sector_research", missing_fields=[], clarification=None)
        if any(marker in lowered for marker in ("kol", "xueqiu", "logic", "status")):
            return RoutingDecision(intent="distill_kol_logic", missing_fields=[], clarification=None)
        if any(marker in lowered for marker in ("review", "review_thesis", "posterior", "validate")):
            return RoutingDecision(intent="review_thesis", missing_fields=[], clarification=None)

        clarification = ClarificationPayload(
            question="Do you want stock, sector, distillation, or review?",
            missing_fields=["intent"],
        )
        return RoutingDecision(intent=None, missing_fields=["intent"], clarification=clarification)
