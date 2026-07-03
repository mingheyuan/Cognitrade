from __future__ import annotations

from research_system.runtime.platform.settings import Settings
from research_system.runtime.providers.provider_registry import OpenAIProvider
from research_system.schemas.protocol import RequestEnvelope
from research_system.services.routing.matching.matcher import RouteMatcher
from research_system.services.routing.router.task_router_chat_support import TaskRouterChatSupport
from research_system.services.routing.router.task_router_identify_support import TaskRouterIdentifySupport
from research_system.services.routing.router.task_router_llm_support import TaskRouterLlmSupport
from research_system.services.routing.router.task_router_models import (
    ChatDispatchDecision,
    ChatDispatchPayload,
    IntentRoutePayload,
    RESEARCH_INTENTS,
    RoutingDecision,
)
from research_system.services.routing.router.task_router_rule_support import TaskRouterRuleSupport
from research_system.services.routing.tables.route_table_runtime import (
    IntentRouteTableStore,
    build_intent_route_table_store,
)


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
        self.rule_support = TaskRouterRuleSupport()
        self.llm_support = TaskRouterLlmSupport()
        self.identify_support = TaskRouterIdentifySupport(
            rule_support=self.rule_support,
            llm_support=self.llm_support,
        )
        self.chat_support = TaskRouterChatSupport(
            rule_support=self.rule_support,
            llm_support=self.llm_support,
        )

    def identify(self, request: RequestEnvelope) -> RoutingDecision:
        return self.identify_support.identify(
            request=request,
            llm_provider=self.llm_provider,
            route_matcher=self.route_matcher,
        )

    def decide_chat_dispatch(self, query: str) -> ChatDispatchDecision:
        return self.chat_support.decide_chat_dispatch(
            query=query,
            llm_provider=self.llm_provider,
            route_matcher=self.route_matcher,
            looks_like_general_definition_or_chat=self._looks_like_general_definition_or_chat,
        )

    def _rule_based_identify(self, query: str) -> RoutingDecision | None:
        return self.rule_support.rule_based_identify(query=query, route_matcher=self.route_matcher)

    def _looks_like_general_definition_or_chat(self, query: str) -> bool:
        lowered = query.strip().lower()
        basic_markers = (
            "浣犳槸璋?",
            "浣犺兘鍋氫粈涔?",
            "鎬庝箞鐢?",
            "help",
            "who are you",
            "what can you do",
            "how do i use this",
            "hello",
            "hi",
            "浣犲ソ",
            "鍡?",
            "璋㈣阿",
            "thanks",
        )
        if any(marker in lowered for marker in basic_markers):
            return True

        definition_markers = (
            "鏄粈涔?",
            "浠€涔堟剰鎬?",
            "浠嬬粛涓€涓?",
            "what is",
            "what's",
            "define ",
            "explain ",
        )
        if not any(marker in lowered for marker in definition_markers):
            return False

        time_sensitive_cn = ("鏈€杩?", "浠婂ぉ", "浠婃棩", "鐜板湪", "鏈€鏂?", "杩戞湡", "璧板娍", "琛ㄧ幇", "娑ㄨ穼", "澶氬皯鐐?", "鎬庝箞鐪?")
        time_sensitive_en = ("latest", "today", "recent", "current", "trend", "performance", "price", "level", "move")
        if any(marker in query for marker in time_sensitive_cn):
            return False
        if any(marker in lowered for marker in time_sensitive_en):
            return False
        return True


__all__ = [
    "ChatDispatchDecision",
    "ChatDispatchPayload",
    "IntentRoutePayload",
    "RESEARCH_INTENTS",
    "RoutingDecision",
    "TaskRouter",
]
