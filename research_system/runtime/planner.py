from __future__ import annotations

from research_system.runtime.planner_action_factory import PlannerActionFactory
from research_system.runtime.planner_context_hints import PlannerContextHints
from research_system.runtime.planner_dynamic_plan_support import PlannerDynamicPlanSupport
from research_system.runtime.planner_entry_decision_support import PlannerEntryDecisionSupport
from research_system.runtime.planner_static_plan_support import PlannerStaticPlanSupport
from research_system.runtime.provider_registry import OpenAIProvider
from research_system.schemas.planning import ActionPlan
from research_system.schemas.protocol import RequestEnvelope


class RuntimePlanner:
    def __init__(self, llm_provider: OpenAIProvider | None = None) -> None:
        self.llm_provider = llm_provider
        self.context_hints = PlannerContextHints()
        self.action_factory = PlannerActionFactory()
        self.entry_decision = PlannerEntryDecisionSupport(self)
        self.dynamic_support = PlannerDynamicPlanSupport(self)
        self.static_support = PlannerStaticPlanSupport(self)

    def build_initial_plan(self, *, intent: str, request: RequestEnvelope) -> ActionPlan:
        context_hints = self.context_hints.extract(request)
        planning_context_hints = self.context_hints.planning_inputs(context_hints)
        if intent in {"stock_check", "stock_compare", "stock_screen", "sector_research"}:
            return self.dynamic_support.build_dynamic_research_plan(
                intent=intent,
                request=request,
                context_hints=context_hints,
                planning_context_hints=planning_context_hints,
            )
        plan = self.static_support.build_static_plan(
            intent=intent,
            request=request,
            context_hints=context_hints,
            planning_context_hints=planning_context_hints,
        )
        if plan is not None:
            return plan
        raise ValueError(f"unsupported intent for planner: {intent}")

    def _decide_research_entry(self, *, intent: str, request: RequestEnvelope):
        return self.entry_decision.decide_research_entry(intent=intent, request=request)
