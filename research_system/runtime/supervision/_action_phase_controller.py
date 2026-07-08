from __future__ import annotations

from research_system.schemas.planning import ActionPlan, ActionSpec, SupervisorDecision
from research_system.schemas.runtime import BlackboardEntry


class ActionPhaseController:
    def __init__(self, *, policies_by_intent: dict[str, object], decision_factory) -> None:
        self.policies_by_intent = dict(policies_by_intent)
        self.decision_factory = decision_factory

    def decide(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries: list[BlackboardEntry],
    ) -> SupervisorDecision:
        policy = self.policies_by_intent.get(plan.intent)
        if policy is None:
            if pending_actions:
                return self.decision_factory.continue_with(
                    action=pending_actions[:1][0],
                    completed_actions=completed_actions,
                    notes=["advance default ready action frontier"],
                )
            return self.decision_factory.stop_with(
                completed_actions=completed_actions,
                notes=["no further default actions required"],
            )
        return policy.decide(
            plan=plan,
            completed_actions=completed_actions,
            pending_actions=pending_actions,
            blackboard_entries=blackboard_entries,
        )


__all__ = ["ActionPhaseController"]
