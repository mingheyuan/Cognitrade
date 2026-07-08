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
        view = self.blackboard_view.review_thesis_view(blackboard_entries)

        if "collect" not in completed_actions and "collect" in pending_by_id:
            return self.decision_factory.continue_with(
                action=pending_by_id["collect"],
                completed_actions=completed_actions,
                notes=["load prior run context and any available benchmark snapshots first"],
            )

        if (
            "collect" in completed_actions
            and not view.review_summary_present
            and ("review" not in completed_actions)
            and view.needs_review
        ):
            review_action = self.decision_factory.review_action(
                plan=plan,
                existing=pending_by_id.get("review"),
            )
            plan_patch = None
            if review_action.node_id not in {action.node_id for action in plan.actions}:
                plan_patch = self.decision_factory.build_patch(
                    notes=["insert_review_action_after_collection"],
                    operations=[
                        self.decision_factory.insert_action(
                            action=review_action,
                            reason="insert posterior review once review target and snapshots are ready",
                        )
                    ],
                )
            return self.decision_factory.continue_with(
                action=review_action,
                completed_actions=completed_actions,
                notes=["insert posterior review dynamically once review target and snapshots are ready"],
                plan_patch=plan_patch,
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
