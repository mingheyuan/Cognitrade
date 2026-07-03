from __future__ import annotations

from research_system.runtime.provider_registry import LLMProviderError, OpenAIProvider
from research_system.schemas.protocol import ClarificationPayload
from research_system.services.task_router_models import (
    ChatDispatchDecision,
    ChatDispatchPayload,
    IntentRoutePayload,
    RoutingDecision,
)


class TaskRouterLlmSupport:
    def identify_with_llm(
        self,
        *,
        query: str,
        llm_provider: OpenAIProvider | None,
    ) -> RoutingDecision | None:
        if not llm_provider or not llm_provider.available:
            return None
        try:
            payload = llm_provider.parse(
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
        except LLMProviderError:
            return None

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

    def dispatch_chat_with_llm(
        self,
        *,
        query: str,
        llm_provider: OpenAIProvider | None,
    ) -> ChatDispatchDecision | None:
        if not llm_provider or not llm_provider.available:
            return None
        try:
            payload = llm_provider.parse(
                system_prompt=(
                    "You are the top-level chat dispatcher for an A-share research terminal.\n"
                    "Choose direct_answer when the user wants a normal explanation, definition, identity, usage help, or general non-time-sensitive knowledge.\n"
                    "Choose research when the user wants market analysis, stock or sector research, stock recommendations, distillation, or posterior review.\n"
                    "If mode is research, choose one intent from stock_check, stock_compare, stock_screen, sector_research, distill_kol_logic, or review_thesis.\n"
                    "For definitions like 'what is the Shanghai Composite index', prefer direct_answer.\n"
                    "For time-sensitive questions like recent performance, trend, recommendation, catalyst, or validation, prefer research."
                ),
                user_prompt=f"User query:\n{query}",
                schema=ChatDispatchPayload,
                temperature=0.0,
                reasoning_effort="low",
            )
        except LLMProviderError:
            return None
        return ChatDispatchDecision(mode=payload.mode, intent=payload.intent, reason=payload.reason)
