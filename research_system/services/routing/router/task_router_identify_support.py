from __future__ import annotations

from research_system.runtime.core.execution_policy import derive_llm_error_reason
from research_system.runtime.providers.provider_registry import LLMProviderError
from research_system.schemas.protocol import ClarificationPayload, RequestEnvelope
from research_system.services.routing.router.task_router_llm_support import TaskRouterLlmSupport
from research_system.services.routing.router.task_router_models import RESEARCH_INTENTS, RoutingDecision
from research_system.services.routing.router.task_router_rule_support import TaskRouterRuleSupport
from research_system.services.routing.stock_target_resolution import infer_query_intent_hint


class TaskRouterIdentifySupport:
    def __init__(
        self,
        *,
        rule_support: TaskRouterRuleSupport | None = None,
        llm_support: TaskRouterLlmSupport | None = None,
    ) -> None:
        self.rule_support = rule_support or TaskRouterRuleSupport()
        self.llm_support = llm_support or TaskRouterLlmSupport()

    def identify(self, *, request: RequestEnvelope, llm_provider, route_matcher, runtime_binding: dict | None = None) -> RoutingDecision:
        runtime_context = dict(runtime_binding or {}).get("context")
        runtime_support = dict(runtime_binding or {}).get("support")
        request_id = str(dict(runtime_binding or {}).get("request_id") or request.request_id)
        routing_item = None
        routing_item_resolved = False
        if runtime_context is not None:
            routing_item = runtime_context.agent_control.spawn(
                thread_id=runtime_context.thread.thread_id,
                turn_id=runtime_context.turn.turn_id,
                request_id=request_id,
                agent="router",
                phase="routing",
                title="router_identify_decider",
                objective="Identify research intent from the incoming request.",
                payload={"query": request.user_query.strip()},
            )
            runtime_context.agent_control.send(
                routing_item,
                payload={"query": request.user_query.strip()},
            )
        explicit_intent = self._explicit_intent_decision(request)
        if explicit_intent is not None:
            if routing_item is not None:
                runtime_context.agent_control.wait(
                    routing_item,
                    status="completed",
                    payload={"source": "explicit_intent", "intent": request.intent},
                )
            return explicit_intent

        query = request.user_query.strip()
        if not query:
            if routing_item is not None:
                runtime_context.agent_control.wait(
                    routing_item,
                    status="completed",
                    payload={"source": "empty_query"},
                )
            return self._empty_query_decision()

        intent_hint = infer_query_intent_hint(query)
        if intent_hint is not None:
            if routing_item is not None:
                runtime_context.agent_control.wait(
                    routing_item,
                    status="completed",
                    payload={"source": "query_hint", "intent": intent_hint},
                )
            return RoutingDecision(
                intent=intent_hint,
                missing_fields=[],
                clarification=None,
                trace={"source": "query_hint", "intent": intent_hint},
            )

        rule_decision = self.rule_support.rule_based_identify(query=query, route_matcher=route_matcher)
        if rule_decision is not None:
            if routing_item is not None:
                runtime_context.agent_control.wait(
                    routing_item,
                    status="completed",
                    payload={"source": "rule", "intent": rule_decision.intent},
                )
            return rule_decision

        llm_runtime_decision = None
        if runtime_context is not None and runtime_support is not None:
            llm_runtime_decision = runtime_support.resolve_llm_execution(
                runtime_context,
                request_id=request_id,
                agent="router",
                phase="routing",
                strategy="clarify_fallback",
                provider=llm_provider,
            )
        can_use_llm = bool(llm_provider and llm_runtime_decision and llm_runtime_decision.can_use_llm)
        if can_use_llm:
            try:
                llm_decision = self.llm_support.identify_with_llm(
                    query=query,
                    llm_provider=llm_provider,
                    suppress_errors=False,
                )
                if llm_decision is not None:
                    if routing_item is not None:
                        runtime_context.agent_control.wait(
                            routing_item,
                            status="completed",
                            payload={
                                "source": str((llm_decision.trace or {}).get("source") or "llm"),
                                "intent": llm_decision.intent,
                                "missing_fields": llm_decision.missing_fields,
                            },
                        )
                        routing_item_resolved = True
                    return llm_decision
            except LLMProviderError as exc:
                if runtime_context is not None and runtime_support is not None:
                    error_reason = derive_llm_error_reason(exc)
                    runtime_support.record_degradation(
                        runtime_context,
                        request_id=request_id,
                        component="router_identify_decider",
                        reason=error_reason,
                        phase="routing",
                        agent="router",
                        payload={"strategy": "clarify_fallback"},
                    )
                    if routing_item is not None:
                        runtime_context.agent_control.wait(
                            routing_item,
                            status="degraded",
                            payload={"source": "clarify_fallback"},
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
                payload={"source": "clarify_fallback"},
                degrade_reason=degrade_reason,
            )
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
