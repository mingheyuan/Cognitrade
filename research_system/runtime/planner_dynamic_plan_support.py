from __future__ import annotations


class PlannerDynamicPlanSupport:
    def __init__(self, host) -> None:
        self.host = host

    def build_dynamic_research_plan(self, *, intent: str, request, context_hints: dict, planning_context_hints: dict):
        decision = self.host.entry_decision.decide_research_entry(intent=intent, request=request)
        actions = self.host.action_factory.build_entry_actions(
            intent=intent,
            action_ids=decision.initial_actions,
            planning_context_hints=planning_context_hints,
        )
        return self.host.action_factory.build_research_plan(
            intent=intent,
            actions=actions,
            notes=[f"{intent} uses dynamic supervisor"],
            entry_strategy=decision.source,
            entry_reason=self.host.context_hints.merge_entry_reason(decision.reason, context_hints),
            subagent_required=decision.use_subagents,
            context_hints=context_hints,
            initial_actions=decision.initial_actions,
            reason=decision.reason,
            strategy="dynamic",
        )
