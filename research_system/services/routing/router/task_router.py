from __future__ import annotations

from typing import Any

from research_system.runtime.local_runtime import bind_local_runtime
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
from research_system.services.routing.tables.route_table_store import IntentRouteTableStore
from research_system.services.routing.tables.route_table_runtime import build_intent_route_table_store


class TaskRouter:
    def __init__(
        self,
        llm_provider: OpenAIProvider | None = None,
        *,
        settings: Settings | None = None,
        route_table_store: IntentRouteTableStore | None = None,
    ) -> None:
        self.llm_provider = llm_provider
        self.settings = settings
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
        self._runtime_binding: dict[str, Any] | None = None

    def identify(self, request: RequestEnvelope) -> RoutingDecision:
        if not self._runtime_binding:
            with bind_local_runtime(
                self,
                request=request,
                intent="routing",
                phase="routing",
                runs_root=self._runs_root(),
                run_timeout_seconds=self._run_timeout_seconds(),
                max_llm_calls_per_turn=self._max_llm_calls_per_turn(),
                max_subagent_calls_per_turn=self._max_subagent_calls_per_turn(),
                max_child_threads_per_turn=self._max_child_threads_per_turn(),
            ):
                return self.identify(request)
        return self.identify_support.identify(
            request=request,
            llm_provider=self.llm_provider,
            route_matcher=self.route_matcher,
            runtime_binding=dict(self._runtime_binding or {}),
        )

    def decide_chat_dispatch(self, query: str) -> ChatDispatchDecision:
        if not self._runtime_binding:
            with bind_local_runtime(
                self,
                request=RequestEnvelope(intent="chat_dispatch", user_query=query),
                intent="chat_dispatch",
                phase="chat_dispatch",
                runs_root=self._runs_root(),
                run_timeout_seconds=self._run_timeout_seconds(),
                max_llm_calls_per_turn=self._max_llm_calls_per_turn(),
                max_subagent_calls_per_turn=self._max_subagent_calls_per_turn(),
                max_child_threads_per_turn=self._max_child_threads_per_turn(),
            ):
                return self.decide_chat_dispatch(query)
        return self.chat_support.decide_chat_dispatch(
            query=query,
            llm_provider=self.llm_provider,
            route_matcher=self.route_matcher,
            looks_like_general_definition_or_chat=self._looks_like_general_definition_or_chat,
            runtime_binding=dict(self._runtime_binding or {}),
        )

    def _rule_based_identify(self, query: str) -> RoutingDecision | None:
        return self.rule_support.rule_based_identify(query=query, route_matcher=self.route_matcher)

    def bind_runtime(
        self,
        *,
        context,
        support,
        request_id: str,
    ) -> None:
        self._runtime_binding = {
            "context": context,
            "support": support,
            "request_id": request_id,
        }

    def clear_runtime(self) -> None:
        self._runtime_binding = None

    def _run_timeout_seconds(self) -> int:
        if self.settings is None:
            return 120
        return int(self.settings.run_timeout_seconds)

    def _runs_root(self):
        if self.settings is None:
            return None
        return self.settings.runs_root

    def _max_llm_calls_per_turn(self) -> int:
        if self.settings is None:
            return 1
        return int(self.settings.max_llm_calls_per_turn)

    def _max_subagent_calls_per_turn(self) -> int:
        if self.settings is None:
            return 32
        return int(self.settings.max_subagent_calls_per_turn)

    def _max_child_threads_per_turn(self) -> int:
        if self.settings is None:
            return 8
        return int(self.settings.max_child_threads_per_turn)

    def _looks_like_general_definition_or_chat(self, query: str) -> bool:
        query_text = query.strip()
        lowered = query_text.lower()
        basic_markers_cn = (
            "你是谁",
            "你能做什么",
            "怎么用",
            "你好",
            "嗨",
            "谢谢",
        )
        basic_markers_en = (
            "help",
            "who are you",
            "what can you do",
            "how do i use this",
            "hello",
            "hi",
            "thanks",
        )
        if any(marker in query_text for marker in basic_markers_cn) or any(marker in lowered for marker in basic_markers_en):
            return True

        definition_markers_cn = (
            "什么是",
            "什么意思",
            "介绍一下",
        )
        definition_markers_en = (
            "what is",
            "what's",
            "define ",
            "explain ",
        )
        if not any(marker in query_text for marker in definition_markers_cn) and not any(
            marker in lowered for marker in definition_markers_en
        ):
            return False

        time_sensitive_cn = ("最近", "今天", "今日", "现在", "最新", "近期", "走势", "表现", "涨跌", "多少点", "怎么看")
        time_sensitive_en = ("latest", "today", "recent", "current", "trend", "performance", "price", "level", "move")
        if any(marker in query_text for marker in time_sensitive_cn):
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
