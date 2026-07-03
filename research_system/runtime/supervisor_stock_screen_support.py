from __future__ import annotations

from research_system.schemas.planning import ActionPlan, ActionSpec, SupervisorDecision
from research_system.schemas.runtime import BlackboardEntry


class SupervisorStockScreenSupport:
    def __init__(self, *, blackboard_view, decision_factory) -> None:
        self.blackboard_view = blackboard_view
        self.decision_factory = decision_factory

    def decide(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries: list[BlackboardEntry],
    ) -> SupervisorDecision:
        pending_by_id = self.decision_factory.pending_by_id(pending_actions)
        view = self.blackboard_view.stock_screen_view(blackboard_entries)

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

        if (
            "screen" in completed_actions
            and not view.review_summary_present
            and ("screen_review" not in completed_actions)
            and (view.top_candidate_count > 0 or view.watchlist_candidate_count > 0)
        ):
            review_action = self.decision_factory.screen_review_action(
                plan=plan,
                existing=pending_by_id.get("screen_review"),
            )
            return self.decision_factory.continue_with(
                action=review_action,
                completed_actions=completed_actions,
                notes=["insert screen reviewer dynamically after screening"],
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
