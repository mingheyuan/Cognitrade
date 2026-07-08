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
        view = self.blackboard_view.stock_screen_view(blackboard_entries)
        plan_node_ids = {action.node_id for action in plan.actions}

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
            and view.enrichment_pending
            and view.enrichment_target_count > 0
            and ("screen_enrich" not in completed_actions)
        ):
            enrich_action = self.decision_factory.screen_enrich_action(
                plan=plan,
                existing=pending_by_id.get("screen_enrich"),
            )
            if "screen_enrich" not in plan_node_ids or (
                not view.review_summary_present
                and (view.top_candidate_count > 0 or view.watchlist_candidate_count > 0)
                and "screen_review" not in plan_node_ids
            ):
                return self.continue_with_replan(
                    action=enrich_action,
                    completed_actions=completed_actions,
                    reason="stock_screen follow-up action graph should be rebuilt after screening",
                    notes=["replan_screen_follow_up_action_graph_after_screening"],
                    metadata={
                        "trigger": "screen_completed",
                        "include_screen_enrich": "true",
                        "include_screen_review": str(
                            bool(
                                not view.review_summary_present
                                and (view.top_candidate_count > 0 or view.watchlist_candidate_count > 0)
                            )
                        ).lower(),
                    },
                )
            return self.decision_factory.continue_with(
                action=enrich_action,
                completed_actions=completed_actions,
                notes=["run screening enrichment before downstream review/retrieval"],
            )

        if (
            "screen" in completed_actions
            and not view.review_summary_present
            and not view.enrichment_pending
            and ("screen_review" not in completed_actions)
            and (view.top_candidate_count > 0 or view.watchlist_candidate_count > 0)
        ):
            review_action = self.decision_factory.screen_review_action(
                plan=plan,
                existing=pending_by_id.get("screen_review"),
            )
            if "screen_review" not in plan_node_ids:
                return self.continue_with_replan(
                    action=review_action,
                    completed_actions=completed_actions,
                    reason="stock_screen review action graph should be rebuilt after screening",
                    notes=["replan_screen_review_action_graph_after_screening"],
                    metadata={
                        "trigger": "screen_completed",
                        "include_screen_review": "true",
                    },
                )
            return self.decision_factory.continue_with(
                action=review_action,
                completed_actions=completed_actions,
                notes=["run screen reviewer after screening"],
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
