from __future__ import annotations

from research_system.runtime.core.execution_policy import derive_llm_error_reason
from research_system.runtime.planning.models import EntryPlanDecision, EntryPlanPayload
from research_system.runtime.providers.provider_registry import LLMProviderError


class PlannerEntryDecisionSupport:
    def __init__(self, host) -> None:
        self.host = host

    def decide_research_entry(self, *, intent: str, request) -> EntryPlanDecision:
        runtime_binding = dict(getattr(self.host, "_runtime_binding", {}) or {})
        runtime_context = runtime_binding.get("context")
        runtime_support = runtime_binding.get("support")
        request_id = str(runtime_binding.get("request_id") or request.request_id)
        default_initial_actions = self.host.action_factory.default_initial_actions(intent)
        planner_item = None
        planner_item_resolved = False
        if runtime_context is not None:
            planner_item = runtime_context.agent_control.spawn(
                thread_id=runtime_context.thread.thread_id,
                turn_id=runtime_context.turn.turn_id,
                request_id=request_id,
                agent="planner",
                phase="planning",
                title="planner_entry_decider",
                objective="Decide whether research subagents are needed and choose entry actions.",
                payload={"intent": intent},
            )
            runtime_context.agent_control.send(
                planner_item,
                payload={"intent": intent, "query": request.user_query.strip()},
            )
        if request.intent:
            if planner_item is not None:
                runtime_context.agent_control.wait(
                    planner_item,
                    status="completed",
                    payload={
                        "source": "explicit_intent",
                        "initial_actions": default_initial_actions,
                    },
                )
            return EntryPlanDecision(
                source="explicit_intent",
                use_subagents=True,
                initial_actions=default_initial_actions,
                reason="explicit intent provided by caller",
            )

        query = request.user_query.strip()
        llm_decision = None
        if runtime_context is not None and runtime_support is not None:
            llm_decision = runtime_support.resolve_llm_execution(
                runtime_context,
                request_id=request_id,
                agent="planner",
                phase="planning",
                strategy="rule_fallback_entry_plan",
                provider=self.host.llm_provider,
            )
        can_use_llm = bool(self.host.llm_provider) and bool(llm_decision and llm_decision.can_use_llm)
        if can_use_llm:
            try:
                payload = self.host.llm_provider.parse(
                    system_prompt=(
                        "You are the research entry planner for an A-share research terminal.\n"
                        "The broad intent is already fixed. Decide whether research subagents are still needed, and if they are, which initial actions should run first.\n"
                        "Allowed initial actions are collect, screen, retrieve, and report.\n"
                        "Use stock_screen -> collect,screen,... when the task is recommendation or watchlist building.\n"
                        "Use stock_compare -> collect,retrieve,report for multi-stock comparison requests.\n"
                        "Use stock_check or sector_research -> collect,retrieve,report for normal evidence-backed research.\n"
                        "Only choose use_subagents=false when the answer could safely be produced directly without research collection."
                    ),
                    user_prompt=(
                        f"Intent: {intent}\n"
                        f"User query: {query}\n"
                        f"Current constraints: {request.constraints}\n"
                        "Return a compact plan decision."
                    ),
                    schema=EntryPlanPayload,
                    temperature=0.0,
                    reasoning_effort="low",
                )
                initial_actions = self.host.action_factory.normalize_initial_actions(
                    intent=intent,
                    actions=payload.initial_actions,
                )
                if planner_item is not None:
                    runtime_context.agent_control.wait(
                        planner_item,
                        status="completed",
                        payload={
                            "source": "llm",
                            "use_subagents": payload.use_subagents,
                            "initial_actions": initial_actions,
                            "reason": payload.reason or "llm_entry_plan",
                        },
                    )
                    planner_item_resolved = True
                return EntryPlanDecision(
                    source="llm",
                    use_subagents=payload.use_subagents,
                    initial_actions=initial_actions,
                    reason=payload.reason or "llm_entry_plan",
                )
            except LLMProviderError as exc:
                if runtime_context is not None and runtime_support is not None:
                    error_reason = derive_llm_error_reason(exc)
                    runtime_support.record_degradation(
                        runtime_context,
                        request_id=request_id,
                        component="planner_entry_decider",
                        reason=error_reason,
                        phase="planning",
                        agent="planner",
                        payload={"strategy": "rule_fallback_entry_plan"},
                    )
                    if planner_item is not None:
                        runtime_context.agent_control.wait(
                            planner_item,
                            status="degraded",
                            payload={"source": "rule", "reason": "rule_fallback_entry_plan"},
                            degrade_reason=error_reason,
                        )
                        planner_item_resolved = True
                pass

        if planner_item is not None and not planner_item_resolved:
            degrade_reason = llm_decision.degrade_reason if llm_decision is not None and not llm_decision.can_use_llm else None
            runtime_context.agent_control.wait(
                planner_item,
                status="degraded" if degrade_reason else "completed",
                payload={
                    "source": "rule",
                    "use_subagents": True,
                    "initial_actions": default_initial_actions,
                    "reason": "rule_fallback_entry_plan",
                },
                degrade_reason=degrade_reason,
            )
        return EntryPlanDecision(
            source="rule",
            use_subagents=True,
            initial_actions=default_initial_actions,
            reason="rule_fallback_entry_plan",
        )
