from __future__ import annotations

import unittest

from research_system.runtime.supervision._action_control_controller import ActionControlController
from research_system.runtime.supervision.decision_factory import SupervisorDecisionFactory
from research_system.schemas.planning import ActionPlan, ActionSpec


class _PhaseControllerStub:
    def __init__(self, decision) -> None:
        self.decision = decision
        self.calls: list[dict] = []

    def decide(self, *, plan, completed_actions, pending_actions, blackboard_entries):
        self.calls.append(
            {
                "plan": plan,
                "completed_actions": completed_actions,
                "pending_actions": pending_actions,
                "blackboard_entries": blackboard_entries,
            }
        )
        return self.decision


class _LLMPolicySourceStub:
    def __init__(self, decision) -> None:
        self.decision = decision
        self.calls: list[dict] = []

    def apply(self, *, plan, completed_actions, pending_actions, blackboard_entries, current_decision):
        self.calls.append(
            {
                "plan": plan,
                "completed_actions": completed_actions,
                "pending_actions": pending_actions,
                "blackboard_entries": blackboard_entries,
                "current_decision": current_decision,
            }
        )
        return self.decision


class _RecorderStub:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def record(self, **kwargs) -> None:
        self.calls.append(dict(kwargs))


class ActionControlControllerTests(unittest.TestCase):
    def test_decide_records_final_action_control_decision(self) -> None:
        decision_factory = SupervisorDecisionFactory()
        retrieve = ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever")
        review = ActionSpec(node_id="review", phase="review", agent="reviewer")
        rule_decision = decision_factory.continue_with(
            action=retrieve,
            completed_actions=["collect"],
            notes=["rule_selected_retrieve"],
        )
        final_decision = decision_factory.continue_with(
            action=review,
            completed_actions=["collect"],
            notes=["llm_selected_review"],
        )
        phase_controller = _PhaseControllerStub(rule_decision)
        llm_policy_source = _LLMPolicySourceStub(final_decision)
        recorder = _RecorderStub()
        controller = ActionControlController(
            phase_controller=phase_controller,
            llm_policy_source=llm_policy_source,
            control_stage_recorder=recorder,
        )
        plan = ActionPlan(
            intent="review_thesis",
            actions=[retrieve, review],
        )

        decision = controller.decide(
            plan=plan,
            completed_actions=["collect"],
            pending_actions=[retrieve, review],
            blackboard_entries=[],
        )

        self.assertIs(decision, final_decision)
        self.assertEqual(len(phase_controller.calls), 1)
        self.assertEqual(len(llm_policy_source.calls), 1)
        self.assertEqual(len(recorder.calls), 1)
        self.assertEqual(recorder.calls[0]["event_type"], "action_phase_decided")
        self.assertEqual(recorder.calls[0]["payload"]["control_domain"], "action")
        self.assertEqual(recorder.calls[0]["payload"]["control_stage"], "action_phase")
        self.assertEqual(recorder.calls[0]["payload"]["rule_action_node_id"], "retrieve")
        self.assertEqual(recorder.calls[0]["payload"]["final_action_node_id"], "review")
        self.assertTrue(recorder.calls[0]["payload"]["override_rule_action"])


if __name__ == "__main__":
    unittest.main()
