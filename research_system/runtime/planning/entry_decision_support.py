from __future__ import annotations

from research_system.runtime.planning.models import EntryPlanDecision, EntryPlanPayload
from research_system.runtime.providers.provider_registry import LLMProviderError


class PlannerEntryDecisionSupport:
    def __init__(self, host) -> None:
        self.host = host

    def decide_research_entry(self, *, intent: str, request) -> EntryPlanDecision:
        if request.intent:
            return EntryPlanDecision(
                source="explicit_intent",
                use_subagents=True,
                initial_actions=self.host.action_factory.default_initial_actions(intent),
                reason="explicit intent provided by caller",
            )

        query = request.user_query.strip()
        if self.host.llm_provider and self.host.llm_provider.available:
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
                return EntryPlanDecision(
                    source="llm",
                    use_subagents=payload.use_subagents,
                    initial_actions=initial_actions,
                    reason=payload.reason or "llm_entry_plan",
                )
            except LLMProviderError:
                pass

        return EntryPlanDecision(
            source="rule",
            use_subagents=True,
            initial_actions=self.host.action_factory.default_initial_actions(intent),
            reason="rule_fallback_entry_plan",
        )
