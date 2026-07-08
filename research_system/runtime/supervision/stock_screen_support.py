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
            and view.enrichment_pending
            and view.enrichment_target_count > 0
            and ("screen_enrich" not in completed_actions)
        ):
            enrich_action = self.decision_factory.screen_enrich_action(
                plan=plan,
                existing=pending_by_id.get("screen_enrich"),
            )
            patch_operations = []
            if enrich_action.node_id not in {action.node_id for action in plan.actions}:
                patch_operations.append(
                    self.decision_factory.insert_action(
                        action=enrich_action,
                        reason="insert screening enrichment before downstream retrieval",
                    )
                )
            patch_operations.append(
                self.decision_factory.rewire_dependency(
                    target_node_id="retrieve",
                    new_depends_on=["screen_enrich"],
                    remove_depends_on=["screen"],
                    reason="route retrieval through screen enrichment when deeper checks are pending",
                )
            )
            if any(action.node_id == "screen_review" for action in plan.actions):
                patch_operations.append(
                    self.decision_factory.rewire_dependency(
                        target_node_id="screen_review",
                        new_depends_on=["screen_enrich"],
                        remove_depends_on=["screen"],
                        reason="route screen review through screen enrichment when deeper checks are pending",
                    )
                )
            return self.decision_factory.continue_with(
                action=enrich_action,
                completed_actions=completed_actions,
                notes=["insert screening enrichment before review/retrieval when deep checks are still missing"],
                plan_patch=self.decision_factory.build_patch(
                    notes=[
                        "insert_screen_enrich_before_retrieval",
                        "rewire_retrieve_to_screen_enrich",
                    ],
                    operations=patch_operations,
                ),
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
            plan_patch = None
            if review_action.node_id not in {action.node_id for action in plan.actions}:
                plan_patch = self.decision_factory.build_patch(
                    notes=["insert_screen_review_after_screening"],
                    operations=[
                        self.decision_factory.insert_action(
                            action=review_action,
                            reason="insert screen reviewer dynamically after screening",
                        )
                    ],
                )
            return self.decision_factory.continue_with(
                action=review_action,
                completed_actions=completed_actions,
                notes=["insert screen reviewer dynamically after screening"],
                plan_patch=plan_patch,
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
