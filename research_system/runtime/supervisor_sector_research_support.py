from __future__ import annotations

from research_system.schemas.planning import ActionSpec, SupervisorDecision
from research_system.schemas.runtime import BlackboardEntry


class SupervisorSectorResearchSupport:
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
        view = self.blackboard_view.sector_research_view(blackboard_entries)

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
            if replenish_action.node_id not in completed_actions and replenish_action.node_id not in pending_by_id:
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
