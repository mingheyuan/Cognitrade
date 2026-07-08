from __future__ import annotations

from research_system.runtime.supervision._action_phase_policy import ActionPhaseRulePolicy
from research_system.schemas.planning import ActionPlan, ActionSpec, SupervisorDecision
from research_system.schemas.runtime import BlackboardEntry


class SupervisorStockCheckSupport(ActionPhaseRulePolicy):
    intent = "stock_check"

    def decide(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries: list[BlackboardEntry],
    ) -> SupervisorDecision:
        pending_by_id = self.pending_by_id(pending_actions)
        view = self.blackboard_view.stock_check_view(blackboard_entries)
        plan_node_ids = {action.node_id for action in plan.actions}

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
            if replenish_action.node_id not in completed_actions:
                if replenish_action.node_id not in plan_node_ids:
                    return self.continue_with_replan(
                        action=replenish_action,
                        completed_actions=completed_actions,
                        reason="stock_check action graph should be rebuilt for missing stock snapshots",
                        notes=["replan_stock_check_action_graph_for_missing_snapshots"],
                        metadata={
                            "trigger": "collect_completed",
                            "missing_snapshot_types": ",".join(view.missing_snapshot_types),
                        },
                    )
                if replenish_action.node_id not in pending_by_id:
                    return self.decision_factory.continue_with(
                        action=replenish_action,
                        completed_actions=completed_actions,
                        notes=[f"replenish missing stock snapshots: {','.join(view.missing_snapshot_types)}"],
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
