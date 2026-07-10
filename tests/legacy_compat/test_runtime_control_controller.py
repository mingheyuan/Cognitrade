from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.agents._subagent_control_escalation import (
    SubagentControlEscalationDecision,
    SubagentControlEscalationError,
)
from research_system.runtime.execution.core.runtime_control_controller import (
    OrchestratorRuntimeControlController,
)
from research_system.schemas.planning import (
    ActionPlan,
    ActionPlanPatch,
    ActionPlanPatchOperation,
    ActionSpec,
)
from research_system.schemas.subagents import SubagentCapabilityGap


class _HostStub:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def _emit_event(self, context, **kwargs) -> None:
        self.events.append(dict(kwargs))


class RuntimeControlControllerTests(unittest.TestCase):
    def test_supervisor_plan_patch_records_transition_before_applying_patch(self) -> None:
        host = _HostStub()
        apply_calls: list[dict] = []
        replan_calls: list[dict] = []

        def apply_plan_patch(**kwargs):
            apply_calls.append(dict(kwargs))
            return [{"patch_id": kwargs["plan_patch"].patch_id}]

        def request_action_plan_replan(**kwargs):
            replan_calls.append(dict(kwargs))
            return [{"patch_id": kwargs["plan_patch"].patch_id}]

        controller = OrchestratorRuntimeControlController(
            host,
            apply_plan_patch=apply_plan_patch,
            request_action_plan_replan=request_action_plan_replan,
        )
        review = ActionSpec(node_id="review", phase="review", agent="reviewer")
        plan = ActionPlan(
            plan_id="plan_initial",
            intent="review_thesis",
            actions=[
                ActionSpec(node_id="collect", phase="collect", agent="collector"),
                ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever", depends_on=["collect"]),
            ],
        )
        patch = ActionPlanPatch(
            operations=[
                ActionPlanPatchOperation(
                    op_type="insert_action",
                    action=review,
                    reason="insert review after collect",
                )
            ],
            notes=["insert_review_after_collect"],
        )
        context = SimpleNamespace(runtime_control_stage_decisions=[], dynamic_trace=[])
        request = SimpleNamespace(request_id="req-runtime-control")

        rows = controller.handle_supervisor_plan_patch(
            context=context,
            request=request,
            plan=plan,
            pending_actions=[],
            completed_node_ids=["collect"],
            skipped_node_ids=[],
            plan_patch=patch,
            next_actions=[review],
        )

        self.assertEqual(rows, [{"patch_id": patch.patch_id}])
        self.assertEqual(len(apply_calls), 1)
        self.assertEqual(len(replan_calls), 0)
        self.assertEqual(len(context.runtime_control_stage_decisions), 1)
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["event"],
            "runtime_control_transition_decided",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["transition_type"],
            "apply_plan_patch",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["source_domain"],
            "action_supervisor",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["source_action_node_id"],
            "review",
        )
        self.assertEqual(context.dynamic_trace[0]["event"], "runtime_control_transition_decided")
        self.assertEqual(host.events[0]["event_type"], "runtime_control_transition_decided")

    def test_subagent_escalation_records_transition_before_requesting_replan(self) -> None:
        host = _HostStub()
        apply_calls: list[dict] = []
        replan_calls: list[dict] = []

        def apply_plan_patch(**kwargs):
            apply_calls.append(dict(kwargs))
            return [{"patch_id": kwargs["plan_patch"].patch_id}]

        def request_action_plan_replan(**kwargs):
            replan_calls.append(dict(kwargs))
            return [{"patch_id": kwargs["plan_patch"].patch_id}]

        controller = OrchestratorRuntimeControlController(
            host,
            apply_plan_patch=apply_plan_patch,
            request_action_plan_replan=request_action_plan_replan,
        )
        review = ActionSpec(node_id="review", phase="review", agent="reviewer", depends_on=["collect"])
        plan = ActionPlan(
            plan_id="plan_initial",
            intent="review_thesis",
            actions=[
                ActionSpec(node_id="collect", phase="collect", agent="collector"),
                review,
            ],
        )
        patch = ActionPlanPatch(
            decision_type="replan",
            operations=[
                ActionPlanPatchOperation(
                    op_type="request_replan",
                    reason="review capability gap requires action replan",
                )
            ],
            notes=["request_action_plan_replan"],
        )
        escalation_error = SubagentControlEscalationError(
            gap=SubagentCapabilityGap(
                intent="review_thesis",
                stage="review",
                capability="missing_review_capability",
                reason="missing review capability",
            ),
            escalation_decision=SubagentControlEscalationDecision(
                strategy="action_replan",
                rule_id="review_optional_phase",
                reason="subagent_control_escalation=action_replan",
            ),
            plan_patch=patch,
        )
        context = SimpleNamespace(runtime_control_stage_decisions=[], dynamic_trace=[])
        request = SimpleNamespace(request_id="req-runtime-control")

        rows = controller.handle_subagent_escalation(
            context=context,
            request=request,
            plan=plan,
            pending_actions=[],
            completed_node_ids=["collect"],
            skipped_node_ids=[],
            action=review,
            escalation_error=escalation_error,
        )

        self.assertEqual(rows, [{"patch_id": patch.patch_id}])
        self.assertEqual(len(apply_calls), 0)
        self.assertEqual(len(replan_calls), 1)
        self.assertEqual(len(context.runtime_control_stage_decisions), 1)
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["transition_type"],
            "request_action_replan",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["source_domain"],
            "subagent_capability_gap",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["source_action_node_id"],
            "review",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["capability"],
            "missing_review_capability",
        )
        self.assertTrue(context.runtime_control_stage_decisions[0]["request_replan"])
        self.assertEqual(host.events[0]["event_type"], "runtime_control_transition_decided")


if __name__ == "__main__":
    unittest.main()
