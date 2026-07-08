from __future__ import annotations

from research_system.runtime.supervision._action_phase_policy import ActionPhaseRulePolicy
from research_system.schemas.planning import ActionPlan, ActionSpec, SupervisorDecision
from research_system.schemas.runtime import BlackboardEntry


class SupervisorStockScreenSupport(ActionPhaseRulePolicy):
    intent = "stock_screen"

    def decide(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries: list[BlackboardEntry],
    ) -> SupervisorDecision:
        pending_by_id = self.pending_by_id(pending_actions)
        _ = (plan, blackboard_entries)

        if "collect" not in completed_actions and "collect" in pending_by_id:
            return self.decision_factory.continue_with(
                action=pending_by_id["collect"],
                completed_actions=completed_actions,
                notes=["start with candidate collection"],
            )

        if "screen" not in completed_actions and "screen" in pending_by_id:
            return self.decision_factory.continue_with(
                action=pending_by_id["screen"],
                completed_actions=completed_actions,
                notes=["screen collected candidates before deciding follow-ups"],
            )

        if "retrieve" not in completed_actions and "retrieve" in pending_by_id:
            return self.decision_factory.continue_with(
                action=pending_by_id["retrieve"],
                completed_actions=completed_actions,
                notes=["retrieve evidence after screening/review"],
            )

        if "report" not in completed_actions and "report" in pending_by_id:
            return self.decision_factory.continue_with(
                action=pending_by_id["report"],
                completed_actions=completed_actions,
                notes=["compose final report after retrieval"],
            )

        return self.decision_factory.stop_with(
            completed_actions=completed_actions,
            notes=["no further stock_screen actions required"],
        )


__all__ = ["SupervisorStockScreenSupport"]
