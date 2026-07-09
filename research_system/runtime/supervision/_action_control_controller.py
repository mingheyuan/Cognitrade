from __future__ import annotations

from research_system.runtime.orchestration._runtime_control_stage_decisions import (
    ActionPhaseControlStageDecision,
)
from research_system.schemas.planning import ActionPlan, ActionSpec, SupervisorDecision
from research_system.schemas.runtime import BlackboardEntry


class ActionControlController:
    def __init__(
        self,
        *,
        phase_controller,
        llm_policy_source,
        control_stage_recorder,
    ) -> None:
        self.phase_controller = phase_controller
        self.llm_policy_source = llm_policy_source
        self.control_stage_recorder = control_stage_recorder

    def decide(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries: list[BlackboardEntry],
    ) -> SupervisorDecision:
        rule_decision = self.phase_controller.decide(
            plan=plan,
            completed_actions=completed_actions,
            pending_actions=pending_actions,
            blackboard_entries=blackboard_entries,
        )
        final_decision = self.llm_policy_source.apply(
            plan=plan,
            completed_actions=completed_actions,
            pending_actions=pending_actions,
            blackboard_entries=blackboard_entries,
            current_decision=rule_decision,
        )
        self._record_stage_decision(
            plan=plan,
            completed_actions=completed_actions,
            pending_actions=pending_actions,
            rule_decision=rule_decision,
            final_decision=final_decision,
        )
        return final_decision

    def _record_stage_decision(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        rule_decision: SupervisorDecision,
        final_decision: SupervisorDecision,
    ) -> None:
        stage_decision = ActionPhaseControlStageDecision(
            intent=plan.intent,
            plan_id=plan.plan_id,
            policy_source=self._decision_policy_source(final_decision),
            decision_type=final_decision.decision_type,
            rule_action_node_id=rule_decision.next_actions[0].node_id if rule_decision.next_actions else None,
            final_action_node_id=final_decision.next_actions[0].node_id if final_decision.next_actions else None,
            rule_request_replan=self._decision_requests_replan(rule_decision),
            final_request_replan=self._decision_requests_replan(final_decision),
            override_rule_action=bool(
                rule_decision.next_actions
                and final_decision.next_actions
                and rule_decision.next_actions[0].node_id != final_decision.next_actions[0].node_id
            ),
            override_replan=self._decision_requests_replan(rule_decision)
            != self._decision_requests_replan(final_decision),
            completed_actions=list(completed_actions),
            pending_action_node_ids=[action.node_id for action in pending_actions],
            notes=list(final_decision.notes or []),
        )
        self.control_stage_recorder.record(
            event_type=stage_decision.event_type,
            message=stage_decision.message(),
            payload=stage_decision.event_payload(),
            phase="supervision",
            agent="supervisor",
        )

    @staticmethod
    def _decision_requests_replan(decision: SupervisorDecision) -> bool:
        patch = decision.plan_patch
        if patch is None:
            return False
        if patch.decision_type == "replan":
            return True
        return any(operation.op_type == "request_replan" for operation in patch.operations)

    @staticmethod
    def _decision_policy_source(decision: SupervisorDecision) -> str:
        patch = decision.plan_patch
        if patch is not None and str(getattr(patch, "policy_source", "")).strip():
            return str(patch.policy_source)
        return "rule"


__all__ = ["ActionControlController"]
