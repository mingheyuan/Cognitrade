from __future__ import annotations

from research_system.services.stock_target_resolution import infer_query_intent_hint
from research_system.services.task_router_llm_support import TaskRouterLlmSupport
from research_system.services.task_router_models import ChatDispatchDecision
from research_system.services.task_router_rule_support import TaskRouterRuleSupport


class TaskRouterChatSupport:
    def __init__(
        self,
        *,
        rule_support: TaskRouterRuleSupport | None = None,
        llm_support: TaskRouterLlmSupport | None = None,
    ) -> None:
        self.rule_support = rule_support or TaskRouterRuleSupport()
        self.llm_support = llm_support or TaskRouterLlmSupport()

    def decide_chat_dispatch(
        self,
        *,
        query: str,
        llm_provider,
        route_matcher,
        looks_like_general_definition_or_chat,
    ) -> ChatDispatchDecision:
        stripped = query.strip()
        if not stripped:
            return ChatDispatchDecision(mode="direct_answer", intent=None, reason="empty_query")

        if looks_like_general_definition_or_chat(stripped):
            return ChatDispatchDecision(mode="direct_answer", intent=None, reason="fallback_basic_chat")

        intent_hint = infer_query_intent_hint(stripped)
        if intent_hint is not None:
            return ChatDispatchDecision(mode="research", intent=intent_hint, reason="query_hint")

        rule_decision = self.rule_support.rule_based_identify(query=stripped, route_matcher=route_matcher)
        if rule_decision is not None and rule_decision.intent:
            trace = rule_decision.trace or {}
            reason = "fallback_rule_route"
            if isinstance(trace.get("source"), str):
                reason = f"{reason}:{trace['source']}"
            return ChatDispatchDecision(mode="research", intent=rule_decision.intent, reason=reason)

        llm_decision = self.llm_support.dispatch_chat_with_llm(query=stripped, llm_provider=llm_provider)
        if llm_decision is not None:
            return llm_decision
        return ChatDispatchDecision(mode="direct_answer", intent=None, reason="fallback_direct_answer")
