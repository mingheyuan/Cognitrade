from __future__ import annotations

from abc import ABC, abstractmethod

from research_system.schemas.planning import ActionPlan, ActionSpec, SupervisorDecision
from research_system.schemas.runtime import BlackboardEntry


class ActionPhaseRulePolicy(ABC):
    intent: str | None = None

    def __init__(self, *, blackboard_view, decision_factory) -> None:
        self.blackboard_view = blackboard_view
        self.decision_factory = decision_factory

    @abstractmethod
    def decide(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries: list[BlackboardEntry],
    ) -> SupervisorDecision:
        raise NotImplementedError

    def pending_by_id(self, pending_actions: list[ActionSpec]) -> dict[str, ActionSpec]:
        return self.decision_factory.pending_by_id(pending_actions)

    def continue_pending(
        self,
        *,
        node_id: str,
        pending_actions: list[ActionSpec],
        completed_actions: list[str],
        notes: list[str] | None = None,
    ) -> SupervisorDecision | None:
        pending_by_id = self.pending_by_id(pending_actions)
        if node_id not in pending_by_id:
            return None
        return self.decision_factory.continue_with(
            action=pending_by_id[node_id],
            completed_actions=completed_actions,
            notes=notes,
        )

    def request_replan(
        self,
        *,
        completed_actions: list[str],
        reason: str,
        notes: list[str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> SupervisorDecision:
        return self.decision_factory.request_replan(
            completed_actions=completed_actions,
            reason=reason,
            notes=notes,
            metadata=metadata,
        )

    def continue_with_replan(
        self,
        *,
        action: ActionSpec,
        completed_actions: list[str],
        reason: str,
        notes: list[str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> SupervisorDecision:
        replan_decision = self.request_replan(
            completed_actions=completed_actions,
            reason=reason,
            notes=notes,
            metadata=metadata,
        )
        return self.decision_factory.continue_with(
            action=action,
            completed_actions=completed_actions,
            notes=list(notes or [reason]),
            plan_patch=replan_decision.plan_patch,
        )


__all__ = ["ActionPhaseRulePolicy"]
