from __future__ import annotations

from research_system.runtime.supervision._action_phase_controller import ActionPhaseController
from research_system.runtime.supervision._action_phase_escalation import (
    ReviewThesisEscalationPolicy,
    SectorResearchEscalationPolicy,
    StockCheckEscalationPolicy,
    StockScreenEscalationPolicy,
)
from research_system.runtime.supervision._action_phase_shadow_policy import ActionPhaseLLMShadowPolicy
from research_system.runtime.supervision.blackboard_view import SupervisorBlackboardView
from research_system.runtime.supervision.decision_factory import SupervisorDecisionFactory
from research_system.runtime.supervision.review_thesis_support import SupervisorReviewThesisSupport
from research_system.runtime.supervision.sector_research_support import SupervisorSectorResearchSupport
from research_system.runtime.supervision.stock_check_support import SupervisorStockCheckSupport
from research_system.runtime.supervision.stock_screen_support import SupervisorStockScreenSupport
from research_system.schemas.planning import ActionPlan, ActionSpec, SupervisorDecision
from research_system.schemas.runtime import BlackboardEntry


class RuntimeSupervisor:
    def __init__(self, llm_provider=None, settings=None) -> None:
        self.llm_provider = llm_provider
        self.settings = settings
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
        self.stock_screen_escalation = StockScreenEscalationPolicy(
            blackboard_view=self.blackboard_view,
            decision_factory=self.decision_factory,
        )
        self.stock_check_escalation = StockCheckEscalationPolicy(
            blackboard_view=self.blackboard_view,
            decision_factory=self.decision_factory,
        )
        self.sector_research_escalation = SectorResearchEscalationPolicy(
            blackboard_view=self.blackboard_view,
            decision_factory=self.decision_factory,
        )
        self.review_thesis_escalation = ReviewThesisEscalationPolicy(
            blackboard_view=self.blackboard_view,
            decision_factory=self.decision_factory,
        )
        self.phase_controller = ActionPhaseController(
            policies_by_intent={
                "stock_screen": self.stock_screen,
                "stock_check": self.stock_check,
                "sector_research": self.sector_research,
                "review_thesis": self.review_thesis,
            },
            escalation_policies_by_intent={
                "stock_screen": self.stock_screen_escalation,
                "stock_check": self.stock_check_escalation,
                "sector_research": self.sector_research_escalation,
                "review_thesis": self.review_thesis_escalation,
            },
            decision_factory=self.decision_factory,
        )
        self.shadow_policy = ActionPhaseLLMShadowPolicy(
            llm_provider=self.llm_provider,
            settings=self.settings,
            blackboard_view=self.blackboard_view,
        )

    def decide(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries: list[BlackboardEntry],
    ) -> SupervisorDecision:
        decision = self.phase_controller.decide(
            plan=plan,
            completed_actions=completed_actions,
            pending_actions=pending_actions,
            blackboard_entries=blackboard_entries,
        )
        self.shadow_policy.observe(
            plan=plan,
            completed_actions=completed_actions,
            pending_actions=pending_actions,
            blackboard_entries=blackboard_entries,
            actual_decision=decision,
        )
        return decision

    def bind_runtime(
        self,
        *,
        context,
        support,
        request_id: str,
        intent: str,
    ) -> None:
        _ = intent
        self.shadow_policy.bind_runtime(
            context=context,
            support=support,
            request_id=request_id,
            intent=intent,
        )

    def clear_runtime(self) -> None:
        self.shadow_policy.clear_runtime()
