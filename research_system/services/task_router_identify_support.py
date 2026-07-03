from __future__ import annotations

from research_system.schemas.protocol import ClarificationPayload, RequestEnvelope
from research_system.services.stock_target_resolution import infer_query_intent_hint
from research_system.services.task_router_llm_support import TaskRouterLlmSupport
from research_system.services.task_router_models import RESEARCH_INTENTS, RoutingDecision
from research_system.services.task_router_rule_support import TaskRouterRuleSupport


class TaskRouterIdentifySupport:
    def __init__(
        self,
        *,
        rule_support: TaskRouterRuleSupport | None = None,
        llm_support: TaskRouterLlmSupport | None = None,
    ) -> None:
        self.rule_support = rule_support or TaskRouterRuleSupport()
        self.llm_support = llm_support or TaskRouterLlmSupport()

    def identify(self, *, request: RequestEnvelope, llm_provider, route_matcher) -> RoutingDecision:
        explicit_intent = self._explicit_intent_decision(request)
        if explicit_intent is not None:
            return explicit_intent

        query = request.user_query.strip()
        if not query:
            return self._empty_query_decision()

        intent_hint = infer_query_intent_hint(query)
        if intent_hint is not None:
            return RoutingDecision(
                intent=intent_hint,
                missing_fields=[],
                clarification=None,
                trace={"source": "query_hint", "intent": intent_hint},
            )

        rule_decision = self.rule_support.rule_based_identify(query=query, route_matcher=route_matcher)
        if rule_decision is not None:
            return rule_decision

        llm_decision = self.llm_support.identify_with_llm(query=query, llm_provider=llm_provider)
        if llm_decision is not None:
            return llm_decision
        return self._clarify_fallback_decision()

    def _explicit_intent_decision(self, request: RequestEnvelope) -> RoutingDecision | None:
        if request.intent not in RESEARCH_INTENTS:
            return None
        return RoutingDecision(
            intent=request.intent,
            missing_fields=[],
            clarification=None,
            trace={"source": "explicit_intent", "intent": request.intent},
        )

    def _empty_query_decision(self) -> RoutingDecision:
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

    def _clarify_fallback_decision(self) -> RoutingDecision:
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
