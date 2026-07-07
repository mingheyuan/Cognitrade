from __future__ import annotations

from research_system.runtime.core.execution_policy import derive_llm_error_reason
from research_system.runtime.providers.provider_registry import LLMProviderError
from research_system.services.routing.router.task_router_llm_support import TaskRouterLlmSupport
from research_system.services.routing.router.task_router_models import ChatDispatchDecision
from research_system.services.routing.router.task_router_rule_support import TaskRouterRuleSupport
from research_system.services.routing.stock_target_resolution import infer_query_intent_hint


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
        runtime_binding: dict | None = None,
    ) -> ChatDispatchDecision:
        runtime_context = dict(runtime_binding or {}).get("context")
        runtime_support = dict(runtime_binding or {}).get("support")
        request_id = str(dict(runtime_binding or {}).get("request_id") or "").strip()
        routing_item = None
        routing_item_resolved = False
        if runtime_context is not None:
            routing_item = runtime_context.agent_control.spawn(
                thread_id=runtime_context.thread.thread_id,
                turn_id=runtime_context.turn.turn_id,
                request_id=request_id or runtime_context.state.request_id,
                agent="router",
                phase="chat_dispatch",
                title="router_chat_dispatch_decider",
                objective="Decide whether chat should answer directly or enter research mode.",
                payload={"query": query},
            )
            runtime_context.agent_control.send(
                routing_item,
                payload={"query": query},
            )
        stripped = query.strip()
        if not stripped:
            if routing_item is not None:
                runtime_context.agent_control.wait(
                    routing_item,
                    status="completed",
                    payload={"source": "empty_query", "mode": "direct_answer"},
                )
            return ChatDispatchDecision(mode="direct_answer", intent=None, reason="empty_query")

        if looks_like_general_definition_or_chat(stripped):
            if routing_item is not None:
                runtime_context.agent_control.wait(
                    routing_item,
                    status="completed",
                    payload={"source": "fallback_basic_chat", "mode": "direct_answer"},
                )
            return ChatDispatchDecision(mode="direct_answer", intent=None, reason="fallback_basic_chat")

        intent_hint = infer_query_intent_hint(stripped)
        if intent_hint is not None:
            if routing_item is not None:
                runtime_context.agent_control.wait(
                    routing_item,
                    status="completed",
                    payload={"source": "query_hint", "mode": "research", "intent": intent_hint},
                )
            return ChatDispatchDecision(mode="research", intent=intent_hint, reason="query_hint")

        rule_decision = self.rule_support.rule_based_identify(query=stripped, route_matcher=route_matcher)
        if rule_decision is not None and rule_decision.intent:
            trace = rule_decision.trace or {}
            reason = "fallback_rule_route"
            if isinstance(trace.get("source"), str):
                reason = f"{reason}:{trace['source']}"
            if routing_item is not None:
                runtime_context.agent_control.wait(
                    routing_item,
                    status="completed",
                    payload={"source": reason, "mode": "research", "intent": rule_decision.intent},
                )
            return ChatDispatchDecision(mode="research", intent=rule_decision.intent, reason=reason)

        llm_runtime_decision = None
        if runtime_context is not None and runtime_support is not None:
            llm_runtime_decision = runtime_support.resolve_llm_execution(
                runtime_context,
                request_id=request_id or runtime_context.state.request_id,
                agent="router",
                phase="chat_dispatch",
                strategy="fallback_direct_answer",
                provider=llm_provider,
            )
        can_use_llm = bool(llm_provider and llm_runtime_decision and llm_runtime_decision.can_use_llm)
        if can_use_llm:
            try:
                llm_decision = self.llm_support.dispatch_chat_with_llm(
                    query=stripped,
                    llm_provider=llm_provider,
                    suppress_errors=False,
                )
                if llm_decision is not None:
                    if routing_item is not None:
                        runtime_context.agent_control.wait(
                            routing_item,
                            status="completed",
                            payload={
                                "source": llm_decision.reason or "llm",
                                "mode": llm_decision.mode,
                                "intent": llm_decision.intent,
                            },
                        )
                        routing_item_resolved = True
                    return llm_decision
            except LLMProviderError as exc:
                if runtime_context is not None and runtime_support is not None:
                    error_reason = derive_llm_error_reason(exc)
                    runtime_support.record_degradation(
                        runtime_context,
                        request_id=request_id or runtime_context.state.request_id,
                        component="router_chat_dispatch_decider",
                        reason=error_reason,
                        phase="chat_dispatch",
                        agent="router",
                        payload={"strategy": "fallback_direct_answer"},
                    )
                    if routing_item is not None:
                        runtime_context.agent_control.wait(
                            routing_item,
                            status="degraded",
                            payload={"source": "fallback_direct_answer", "mode": "direct_answer"},
                            degrade_reason=error_reason,
                        )
                        routing_item_resolved = True
        if routing_item is not None and not routing_item_resolved:
            degrade_reason = (
                llm_runtime_decision.degrade_reason
                if llm_runtime_decision is not None and not llm_runtime_decision.can_use_llm
                else None
            )
            runtime_context.agent_control.wait(
                routing_item,
                status="degraded" if degrade_reason else "completed",
                payload={"source": "fallback_direct_answer", "mode": "direct_answer"},
                degrade_reason=degrade_reason,
            )
        return ChatDispatchDecision(mode="direct_answer", intent=None, reason="fallback_direct_answer")
