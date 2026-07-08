from __future__ import annotations

from research_system.runtime.supervision._action_phase_escalation import ActionPhaseEscalationRequest
from research_system.schemas.planning import ActionPlan, ActionSpec, SupervisorDecision
from research_system.schemas.runtime import BlackboardEntry


class ActionPhaseController:
    def __init__(
        self,
        *,
        policies_by_intent: dict[str, object],
        escalation_policies_by_intent: dict[str, object] | None = None,
        decision_factory,
    ) -> None:
        self.policies_by_intent = dict(policies_by_intent)
        self.escalation_policies_by_intent = dict(escalation_policies_by_intent or {})
        self.decision_factory = decision_factory

    def decide(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries: list[BlackboardEntry],
    ) -> SupervisorDecision:
        escalation_policy = self.escalation_policies_by_intent.get(plan.intent)
        if escalation_policy is not None:
            request = escalation_policy.maybe_escalate(
                plan=plan,
                completed_actions=completed_actions,
                pending_actions=pending_actions,
                blackboard_entries=blackboard_entries,
            )
            if request is not None:
                return self._apply_escalation_request(
                    plan=plan,
                    completed_actions=completed_actions,
                    pending_actions=pending_actions,
                    request=request,
                )
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

    def _apply_escalation_request(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        request: ActionPhaseEscalationRequest,
    ) -> SupervisorDecision:
        pending_by_id = self.decision_factory.pending_by_id(pending_actions)
        current_action = pending_by_id.get(request.action.node_id, request.action)
        if request.replan is None:
            return self.decision_factory.continue_with(
                action=current_action,
                completed_actions=completed_actions,
                notes=list(request.action_notes),
            )
        replan_decision = self.decision_factory.request_replan(
            completed_actions=completed_actions,
            reason=request.replan.reason,
            notes=list(request.replan.notes),
            metadata=dict(request.replan.metadata),
        )
        return self.decision_factory.continue_with(
            action=current_action,
            completed_actions=completed_actions,
            notes=list(request.action_notes or request.replan.notes or [request.replan.reason]),
            plan_patch=replan_decision.plan_patch,
        )


__all__ = ["ActionPhaseController"]
