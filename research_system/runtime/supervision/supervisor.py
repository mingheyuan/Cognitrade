from __future__ import annotations

from research_system.runtime.supervision.blackboard_view import SupervisorBlackboardView
from research_system.runtime.supervision.decision_factory import SupervisorDecisionFactory
from research_system.runtime.supervision.review_thesis_support import SupervisorReviewThesisSupport
from research_system.runtime.supervision.sector_research_support import SupervisorSectorResearchSupport
from research_system.runtime.supervision.stock_check_support import SupervisorStockCheckSupport
from research_system.runtime.supervision.stock_screen_support import SupervisorStockScreenSupport
from research_system.schemas.planning import ActionPlan, ActionSpec, SupervisorDecision
from research_system.schemas.runtime import BlackboardEntry


class RuntimeSupervisor:
    def __init__(self) -> None:
        self.blackboard_view = SupervisorBlackboardView()
        self.decision_factory = SupervisorDecisionFactory()
        self.stock_screen = SupervisorStockScreenSupport(
            blackboard_view=self.blackboard_view,
            decision_factory=self.decision_factory,
        )
        self.stock_check = SupervisorStockCheckSupport(
            blackboard_view=self.blackboard_view,
            decision_factory=self.decision_factory,
        )
        self.sector_research = SupervisorSectorResearchSupport(
            blackboard_view=self.blackboard_view,
            decision_factory=self.decision_factory,
        )
        self.review_thesis = SupervisorReviewThesisSupport(
            blackboard_view=self.blackboard_view,
            decision_factory=self.decision_factory,
        )

    def decide(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries: list[BlackboardEntry],
    ) -> SupervisorDecision:
        if plan.intent == "stock_check":
            return self.stock_check.decide(
                completed_actions=completed_actions,
                pending_actions=pending_actions,
                blackboard_entries=blackboard_entries,
            )
        if plan.intent == "sector_research":
            return self.sector_research.decide(
                completed_actions=completed_actions,
                pending_actions=pending_actions,
                blackboard_entries=blackboard_entries,
            )
        if plan.intent == "review_thesis":
            return self.review_thesis.decide(
                plan=plan,
                completed_actions=completed_actions,
                pending_actions=pending_actions,
                blackboard_entries=blackboard_entries,
            )
        if plan.intent != "stock_screen":
            return SupervisorDecision(
                decision_type="continue" if pending_actions else "stop",
                next_actions=pending_actions[:1],
                completed_action_ids=completed_actions,
            )
        return self.stock_screen.decide(
            plan=plan,
            completed_actions=completed_actions,
            pending_actions=pending_actions,
            blackboard_entries=blackboard_entries,
        )
