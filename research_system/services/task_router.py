from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.provider_registry import LLMProviderError, OpenAIProvider
from research_system.schemas.protocol import ClarificationPayload, RequestEnvelope


RESEARCH_INTENTS = {
    "stock_check",
    "stock_screen",
    "sector_research",
    "distill_kol_logic",
    "review_thesis",
}


@dataclass(frozen=True)
class RoutingDecision:
    intent: str | None
    missing_fields: list[str]
    clarification: ClarificationPayload | None


@dataclass(frozen=True)
class ChatDispatchDecision:
    mode: Literal["direct_answer", "research"]
    intent: str | None
    reason: str | None = None


class IntentRoutePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["stock_check", "stock_screen", "sector_research", "distill_kol_logic", "review_thesis", "clarify"]
    missing_fields: list[str] = Field(default_factory=list)
    clarification_question: str | None = None


class ChatDispatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["direct_answer", "research"]
    intent: Literal["stock_check", "stock_screen", "sector_research", "distill_kol_logic", "review_thesis"] | None = None
    reason: str | None = None


class TaskRouter:
    def __init__(self, llm_provider: OpenAIProvider | None = None) -> None:
        self.llm_provider = llm_provider

    def identify(self, request: RequestEnvelope) -> RoutingDecision:
        if request.intent in RESEARCH_INTENTS:
            return RoutingDecision(intent=request.intent, missing_fields=[], clarification=None)

        query = request.user_query.strip()
        if not query:
            clarification = ClarificationPayload(
                question="Please provide a stock, screening, sector, distillation, or review request.",
                missing_fields=["user_query"],
            )
            return RoutingDecision(intent=None, missing_fields=["user_query"], clarification=clarification)

        if self.llm_provider and self.llm_provider.available:
            try:
                payload = self.llm_provider.parse(
                    system_prompt=(
                        "You route Chinese or English A-share research requests.\n"
                        "Return stock_check for individual stock research.\n"
                        "Return stock_screen for stock recommendations, watchlists, candidate screening, or requests asking which stocks are worth following.\n"
                        "Return sector_research for sector, industry, theme, market index, or broad market research.\n"
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
                        question=payload.clarification_question or "Do you want stock, stock screening, sector, distillation, or review?",
                        missing_fields=payload.missing_fields or ["intent"],
                    )
                    return RoutingDecision(intent=None, missing_fields=clarification.missing_fields, clarification=clarification)
                return RoutingDecision(intent=payload.intent, missing_fields=payload.missing_fields, clarification=None)
            except LLMProviderError:
                pass

        rule_decision = self._rule_based_identify(query)
        if rule_decision is not None:
            return rule_decision

        clarification = ClarificationPayload(
            question="Do you want stock, stock screening, sector, distillation, or review?",
            missing_fields=["intent"],
        )
        return RoutingDecision(intent=None, missing_fields=["intent"], clarification=clarification)

    def decide_chat_dispatch(self, query: str) -> ChatDispatchDecision:
        stripped = query.strip()
        if not stripped:
            return ChatDispatchDecision(mode="direct_answer", intent=None, reason="empty_query")

        if self.llm_provider and self.llm_provider.available:
            try:
                payload = self.llm_provider.parse(
                    system_prompt=(
                        "You are the top-level chat dispatcher for an A-share research terminal.\n"
                        "Choose direct_answer when the user wants a normal explanation, definition, identity, usage help, or general non-time-sensitive knowledge.\n"
                        "Choose research when the user wants market analysis, stock or sector research, stock recommendations, distillation, or posterior review.\n"
                        "If mode is research, choose one intent from stock_check, stock_screen, sector_research, distill_kol_logic, or review_thesis.\n"
                        "For definitions like 'what is the Shanghai Composite index', prefer direct_answer.\n"
                        "For time-sensitive questions like recent performance, trend, recommendation, catalyst, or validation, prefer research."
                    ),
                    user_prompt=f"User query:\n{stripped}",
                    schema=ChatDispatchPayload,
                    temperature=0.0,
                    reasoning_effort="low",
                )
                return ChatDispatchDecision(mode=payload.mode, intent=payload.intent, reason=payload.reason)
            except LLMProviderError:
                pass

        if self._looks_like_general_definition_or_chat(stripped):
            return ChatDispatchDecision(mode="direct_answer", intent=None, reason="fallback_basic_chat")

        rule_decision = self._rule_based_identify(stripped)
        if rule_decision is not None and rule_decision.intent:
            return ChatDispatchDecision(mode="research", intent=rule_decision.intent, reason="fallback_rule_route")

        return ChatDispatchDecision(mode="direct_answer", intent=None, reason="fallback_direct_answer")

    def _rule_based_identify(self, query: str) -> RoutingDecision | None:
        lowered = query.lower()
        if re.search(r"\b\d{6}\b", query):
            return RoutingDecision(intent="stock_check", missing_fields=[], clarification=None)

        if (
            any(token in query for token in ("推荐", "值得", "关注", "选股", "什么股票", "哪些股票"))
            or any(token in lowered for token in ("watchlist", "screen", "candidate", "idea"))
        ):
            return RoutingDecision(intent="stock_screen", missing_fields=[], clarification=None)
        if any(marker in query for marker in ("股票", "个股")) or any(marker in lowered for marker in ("stock", "ticker")):
            return RoutingDecision(intent="stock_check", missing_fields=[], clarification=None)
        if any(marker in query for marker in ("行业", "板块", "主题", "指数", "大盘")) or any(marker in lowered for marker in ("sector", "theme", "industry", "index", "market")):
            return RoutingDecision(intent="sector_research", missing_fields=[], clarification=None)
        if any(marker in query for marker in ("蒸馏", "雪球")) or any(marker in lowered for marker in ("kol", "xueqiu", "logic", "status", "jsonl")):
            return RoutingDecision(intent="distill_kol_logic", missing_fields=[], clarification=None)
        if any(marker in query for marker in ("复核", "后验")) or any(marker in lowered for marker in ("review", "review_thesis", "posterior", "validate")):
            return RoutingDecision(intent="review_thesis", missing_fields=[], clarification=None)
        return None

    def _looks_like_general_definition_or_chat(self, query: str) -> bool:
        lowered = query.strip().lower()
        basic_markers = (
            "你是谁",
            "你能做什么",
            "怎么用",
            "help",
            "who are you",
            "what can you do",
            "how do i use this",
            "hello",
            "hi",
            "你好",
            "嗨",
            "谢谢",
            "thanks",
        )
        if any(marker in lowered for marker in basic_markers):
            return True

        definition_markers = (
            "是什么",
            "什么意思",
            "介绍一下",
            "what is",
            "what's",
            "define ",
            "explain ",
        )
        if not any(marker in lowered for marker in definition_markers):
            return False

        time_sensitive_cn = ("最近", "今天", "今日", "现在", "最新", "近期", "走势", "表现", "涨跌", "多少点", "怎么看")
        time_sensitive_en = ("latest", "today", "recent", "current", "trend", "performance", "price", "level", "move")
        if any(marker in query for marker in time_sensitive_cn):
            return False
        if any(marker in lowered for marker in time_sensitive_en):
            return False
        return True
