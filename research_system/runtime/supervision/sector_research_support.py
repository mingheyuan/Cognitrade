from __future__ import annotations

from research_system.runtime.supervision._action_phase_policy import ActionPhaseRulePolicy
from research_system.schemas.planning import ActionPlan, ActionSpec, SupervisorDecision
from research_system.schemas.runtime import BlackboardEntry


class SupervisorSectorResearchSupport(ActionPhaseRulePolicy):
    intent = "sector_research"

    def decide(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries: list[BlackboardEntry],
    ) -> SupervisorDecision:
        pending_by_id = self.pending_by_id(pending_actions)
        view = self.blackboard_view.sector_research_view(blackboard_entries)
        plan_node_ids = {action.node_id for action in plan.actions}

        if "collect" not in completed_actions and "collect" in pending_by_id:
            return self.decision_factory.continue_with(
                action=pending_by_id["collect"],
                completed_actions=completed_actions,
                notes=["collect sector snapshot first"],
            )

        if "collect" in completed_actions and view.missing_sector_evidence and view.request_query:
            replenish_action = self.decision_factory.sector_replenish_action(
                query=view.request_query,
                missing_sector_evidence=view.missing_sector_evidence,
            )
            if replenish_action.node_id not in completed_actions:
                if replenish_action.node_id not in plan_node_ids:
                    return self.continue_with_replan(
                        action=replenish_action,
                        completed_actions=completed_actions,
                        reason="sector_research action graph should be rebuilt for missing sector evidence",
                        notes=["replan_sector_research_action_graph_for_missing_sector_evidence"],
                        metadata={
                            "trigger": "collect_completed",
                            "missing_sector_evidence": ",".join(view.missing_sector_evidence),
                        },
                    )
                if replenish_action.node_id not in pending_by_id:
                    return self.decision_factory.continue_with(
                        action=replenish_action,
                        completed_actions=completed_actions,
                        notes=[f"replenish missing sector evidence: {','.join(view.missing_sector_evidence)}"],
                    )

        if "retrieve" not in completed_actions and "retrieve" in pending_by_id:
            return self.decision_factory.continue_with(
                action=pending_by_id["retrieve"],
                completed_actions=completed_actions,
                notes=["retrieve sector evidence after collection"],
            )

        if "report" not in completed_actions and "report" in pending_by_id:
            return self.decision_factory.continue_with(
                action=pending_by_id["report"],
                completed_actions=completed_actions,
                notes=["compose final sector report after retrieval"],
            )

        return self.decision_factory.stop_with(
            completed_actions=completed_actions,
            notes=["no further sector_research actions required"],
        )


__all__ = ["SupervisorSectorResearchSupport"]
