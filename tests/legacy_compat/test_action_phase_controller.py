from __future__ import annotations

import unittest

from research_system.runtime.supervision._action_phase_controller import ActionPhaseController
from research_system.runtime.supervision._action_phase_escalation import (
    ActionPhaseEscalationRequest,
    ActionPhaseReplanRequest,
)
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


class _EscalationPolicy:
    def __init__(self, request: ActionPhaseEscalationRequest | None) -> None:
        self.request = request
        self.calls: list[dict] = []

    def maybe_escalate(self, *, plan, completed_actions, pending_actions, blackboard_entries):
        self.calls.append(
            {
                "plan": plan,
                "completed_actions": completed_actions,
                "pending_actions": pending_actions,
                "blackboard_entries": blackboard_entries,
            }
        )
        return self.request


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

    def test_controller_prioritizes_escalation_request_before_default_policy(self) -> None:
        policy = _Policy()
        request = ActionPhaseEscalationRequest(
            action=ActionSpec(node_id="screen_enrich", phase="screen", agent="screener"),
            action_notes=["run enrichment first"],
        )
        escalation_policy = _EscalationPolicy(request)
        controller = ActionPhaseController(
            policies_by_intent={"stock_screen": policy},
            escalation_policies_by_intent={"stock_screen": escalation_policy},
            decision_factory=SupervisorDecisionFactory(),
        )
        plan = ActionPlan(
            intent="stock_screen",
            actions=[ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever")],
        )

        decision = controller.decide(
            plan=plan,
            completed_actions=["collect", "screen"],
            pending_actions=plan.actions,
            blackboard_entries=[],
        )

        self.assertEqual(decision.next_actions[0].node_id, "screen_enrich")
        self.assertEqual(decision.notes, ["run enrichment first"])
        self.assertEqual(len(escalation_policy.calls), 1)
        self.assertEqual(len(policy.calls), 0)

    def test_controller_builds_replan_patch_from_escalation_request(self) -> None:
        request = ActionPhaseEscalationRequest(
            action=ActionSpec(node_id="review", phase="review", agent="reviewer"),
            action_notes=["run posterior review"],
            replan=ActionPhaseReplanRequest(
                reason="review action graph should be rebuilt",
                notes=["replan_review_action_graph_after_collection"],
                metadata={"include_review": "true"},
            ),
        )
        controller = ActionPhaseController(
            policies_by_intent={},
            escalation_policies_by_intent={"review_thesis": _EscalationPolicy(request)},
            decision_factory=SupervisorDecisionFactory(),
        )
        plan = ActionPlan(
            intent="review_thesis",
            actions=[ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever")],
        )

        decision = controller.decide(
            plan=plan,
            completed_actions=["collect"],
            pending_actions=plan.actions,
            blackboard_entries=[],
        )

        self.assertEqual(decision.decision_type, "continue")
        self.assertEqual(decision.next_actions[0].node_id, "review")
        self.assertEqual(decision.notes, ["run posterior review"])
        self.assertIsNotNone(decision.plan_patch)
        self.assertEqual(
            [operation.op_type for operation in list(decision.plan_patch.operations if decision.plan_patch else [])],
            ["request_replan"],
        )
        self.assertEqual(
            str((decision.plan_patch.operations[0].metadata or {}).get("include_review") or ""),
            "true",
        )


if __name__ == "__main__":
    unittest.main()
