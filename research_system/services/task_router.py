from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.provider_registry import LLMProviderError, OpenAIProvider
from research_system.runtime.settings import Settings
from research_system.schemas.protocol import ClarificationPayload, RequestEnvelope
from research_system.services.stock_target_resolution import infer_query_intent_hint
from research_system.services.route_table import (
    IntentRouteTableStore,
    build_intent_route_table_store,
)
from research_system.services.route_matcher import RouteMatcher


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

    intent: Literal["stock_check", "stock_compare", "stock_screen", "sector_research", "distill_kol_logic", "review_thesis", "methodology_evolution", "clarify"]
    missing_fields: list[str] = Field(default_factory=list)
    clarification_question: str | None = None


class ChatDispatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["direct_answer", "research"]
    intent: Literal["stock_check", "stock_compare", "stock_screen", "sector_research", "distill_kol_logic", "review_thesis", "methodology_evolution"] | None = None
    reason: str | None = None


class TaskRouter:
    def __init__(
        self,
        llm_provider: OpenAIProvider | None = None,
        *,
        settings: Settings | None = None,
        route_table_store: IntentRouteTableStore | None = None,
    ) -> None:
        self.llm_provider = llm_provider
        self.route_table_store = route_table_store or build_intent_route_table_store(settings)
        self.route_matcher = RouteMatcher(self.route_table_store)

    def identify(self, request: RequestEnvelope) -> RoutingDecision:
        if request.intent in RESEARCH_INTENTS:
            return RoutingDecision(
                intent=request.intent,
                missing_fields=[],
                clarification=None,
                trace={"source": "explicit_intent", "intent": request.intent},
            )

        query = request.user_query.strip()
        if not query:
            clarification = ClarificationPayload(
                question="Please provide a stock, screening, sector, distillation, or review request.",
                missing_fields=["user_query"],
            )
            return RoutingDecision(
                intent=None,
                missing_fields=["user_query"],
                clarification=clarification,
                trace={"source": "empty_query"},
            )

        intent_hint = infer_query_intent_hint(query)
        if intent_hint is not None:
            return RoutingDecision(
                intent=intent_hint,
                missing_fields=[],
                clarification=None,
                trace={"source": "query_hint", "intent": intent_hint},
            )

        rule_decision = self._rule_based_identify(query)
        if rule_decision is not None:
            return rule_decision

        if self.llm_provider and self.llm_provider.available:
            try:
                payload = self.llm_provider.parse(
                    system_prompt=(
                        "You route Chinese or English A-share research requests.\n"
                        "Return stock_check for individual stock research.\n"
                        "Return stock_compare for requests comparing two or more named stocks or stock codes.\n"
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
                    return RoutingDecision(
                        intent=None,
                        missing_fields=clarification.missing_fields,
                        clarification=clarification,
                        trace={"source": "llm_clarify", "missing_fields": clarification.missing_fields},
                    )
                return RoutingDecision(
                    intent=payload.intent,
                    missing_fields=payload.missing_fields,
                    clarification=None,
                    trace={"source": "llm", "intent": payload.intent, "missing_fields": payload.missing_fields},
                )
            except LLMProviderError:
                pass

        clarification = ClarificationPayload(
            question="Do you want stock, stock screening, sector, distillation, or review?",
            missing_fields=["intent"],
        )
        return RoutingDecision(
            intent=None,
            missing_fields=["intent"],
            clarification=clarification,
            trace={"source": "clarify_fallback", "missing_fields": ["intent"]},
        )

    def decide_chat_dispatch(self, query: str) -> ChatDispatchDecision:
        stripped = query.strip()
        if not stripped:
            return ChatDispatchDecision(mode="direct_answer", intent=None, reason="empty_query")

        if self._looks_like_general_definition_or_chat(stripped):
            return ChatDispatchDecision(mode="direct_answer", intent=None, reason="fallback_basic_chat")

        intent_hint = infer_query_intent_hint(stripped)
        if intent_hint is not None:
            return ChatDispatchDecision(mode="research", intent=intent_hint, reason="query_hint")

        rule_decision = self._rule_based_identify(stripped)
        if rule_decision is not None and rule_decision.intent:
            trace = rule_decision.trace or {}
            reason = "fallback_rule_route"
            if isinstance(trace.get("source"), str):
                reason = f"{reason}:{trace['source']}"
            return ChatDispatchDecision(mode="research", intent=rule_decision.intent, reason=reason)

        if self.llm_provider and self.llm_provider.available:
            try:
                payload = self.llm_provider.parse(
                    system_prompt=(
                        "You are the top-level chat dispatcher for an A-share research terminal.\n"
                        "Choose direct_answer when the user wants a normal explanation, definition, identity, usage help, or general non-time-sensitive knowledge.\n"
                        "Choose research when the user wants market analysis, stock or sector research, stock recommendations, distillation, or posterior review.\n"
                        "If mode is research, choose one intent from stock_check, stock_compare, stock_screen, sector_research, distill_kol_logic, or review_thesis.\n"
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

        return ChatDispatchDecision(mode="direct_answer", intent=None, reason="fallback_direct_answer")

    def _rule_based_identify(self, query: str) -> RoutingDecision | None:
        match = self.route_matcher.match(query)
        if match is not None:
            return RoutingDecision(
                intent=match.intent,
                missing_fields=[],
                clarification=None,
                trace=match.trace,
            )
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
