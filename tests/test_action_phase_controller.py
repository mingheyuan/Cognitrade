from __future__ import annotations

import unittest

from research_system.runtime.supervision._action_phase_controller import ActionPhaseController
from research_system.runtime.supervision.decision_factory import SupervisorDecisionFactory
from research_system.schemas.planning import ActionPlan, ActionSpec


class _Policy:
    def __init__(self) -> None:
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
        return SupervisorDecisionFactory().continue_with(
            action=pending_actions[0],
            completed_actions=completed_actions,
            notes=["policy_routed"],
        )


class ActionPhaseControllerTests(unittest.TestCase):
    def test_controller_routes_to_intent_policy(self) -> None:
        policy = _Policy()
        controller = ActionPhaseController(
            policies_by_intent={"stock_screen": policy},
            decision_factory=SupervisorDecisionFactory(),
        )
        plan = ActionPlan(
            intent="stock_screen",
            actions=[ActionSpec(node_id="collect", phase="collect", agent="collector")],
        )

        decision = controller.decide(
            plan=plan,
            completed_actions=[],
            pending_actions=plan.actions,
            blackboard_entries=[],
        )

        self.assertEqual(decision.next_actions[0].node_id, "collect")
        self.assertEqual(decision.notes, ["policy_routed"])
        self.assertEqual(len(policy.calls), 1)

    def test_controller_falls_back_to_default_ready_action(self) -> None:
        controller = ActionPhaseController(
            policies_by_intent={},
            decision_factory=SupervisorDecisionFactory(),
        )
        plan = ActionPlan(
            intent="stock_compare",
            actions=[
                ActionSpec(node_id="collect", phase="collect", agent="collector"),
                ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever", depends_on=["collect"]),
            ],
        )

        decision = controller.decide(
            plan=plan,
            completed_actions=[],
            pending_actions=[plan.actions[0]],
            blackboard_entries=[],
        )

        self.assertEqual(decision.decision_type, "continue")
        self.assertEqual(decision.next_actions[0].node_id, "collect")
        self.assertEqual(decision.notes, ["advance default ready action frontier"])


if __name__ == "__main__":
    unittest.main()
