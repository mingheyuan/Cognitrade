from __future__ import annotations

from research_system.schemas.planning import ActionSpec, SupervisorDecision
from research_system.schemas.runtime import BlackboardEntry


class SupervisorStockCheckSupport:
    def __init__(self, *, blackboard_view, decision_factory) -> None:
        self.blackboard_view = blackboard_view
        self.decision_factory = decision_factory

    def decide(
        self,
        *,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries: list[BlackboardEntry],
    ) -> SupervisorDecision:
        pending_by_id = self.decision_factory.pending_by_id(pending_actions)
        view = self.blackboard_view.stock_check_view(blackboard_entries)

        if "collect" not in completed_actions and "collect" in pending_by_id:
            return self.decision_factory.continue_with(
                action=pending_by_id["collect"],
                completed_actions=completed_actions,
                notes=["collect baseline stock snapshots first"],
            )

        if "collect" in completed_actions and view.missing_snapshot_types:
            replenish_action = self.decision_factory.stock_check_replenish_action(
                query=view.request_query,
                symbol=view.symbol,
                missing_snapshot_types=view.missing_snapshot_types,
            )
            if replenish_action.node_id not in completed_actions and replenish_action.node_id not in pending_by_id:
                plan_patch = None
                if replenish_action.node_id not in {action.node_id for action in pending_actions}:
                    plan_patch = self.decision_factory.build_patch(
                        notes=["insert_stock_check_replenish_action"],
                        operations=[
                            self.decision_factory.insert_action(
                                action=replenish_action,
                                reason="insert replenishment collect action for missing stock snapshots",
                            )
                        ],
                    )
                return self.decision_factory.continue_with(
                    action=replenish_action,
                    completed_actions=completed_actions,
                    notes=[f"replenish missing stock snapshots: {','.join(view.missing_snapshot_types)}"],
                    plan_patch=plan_patch,
                )

        if "retrieve" not in completed_actions and "retrieve" in pending_by_id:
            return self.decision_factory.continue_with(
                action=pending_by_id["retrieve"],
                completed_actions=completed_actions,
                notes=["retrieve stock evidence after collection is sufficient"],
            )

        if "report" not in completed_actions and "report" in pending_by_id:
            return self.decision_factory.continue_with(
                action=pending_by_id["report"],
                completed_actions=completed_actions,
                notes=["compose final stock report after retrieval"],
            )

        return self.decision_factory.stop_with(
            completed_actions=completed_actions,
            notes=["no further stock_check actions required"],
        )


__all__ = ["SupervisorStockCheckSupport"]
