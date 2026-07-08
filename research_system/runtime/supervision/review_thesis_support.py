from __future__ import annotations

from research_system.runtime.supervision._action_phase_policy import ActionPhaseRulePolicy
from research_system.schemas.planning import ActionPlan, ActionSpec, SupervisorDecision
from research_system.schemas.runtime import BlackboardEntry


class SupervisorReviewThesisSupport(ActionPhaseRulePolicy):
    intent = "review_thesis"

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
                notes=["load prior run context and any available benchmark snapshots first"],
            )

        if "retrieve" not in completed_actions and "retrieve" in pending_by_id:
            return self.decision_factory.continue_with(
                action=pending_by_id["retrieve"],
                completed_actions=completed_actions,
                notes=["retrieve review evidence after posterior review or minimal collection fallback"],
            )

        if "report" not in completed_actions and "report" in pending_by_id:
            return self.decision_factory.continue_with(
                action=pending_by_id["report"],
                completed_actions=completed_actions,
                notes=["compose final posterior review report after retrieval"],
            )

        return self.decision_factory.stop_with(
            completed_actions=completed_actions,
            notes=["no further review_thesis actions required"],
        )


__all__ = ["SupervisorReviewThesisSupport"]
