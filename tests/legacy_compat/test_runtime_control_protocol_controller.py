from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.agents._subagent_control_escalation import (
    SubagentControlEscalationDecision,
    SubagentControlEscalationError,
)
from research_system.runtime.execution.core.runtime_control_protocol_controller import (
    OrchestratorRuntimeControlProtocolController,
)
from research_system.schemas.planning import (
    ActionPlan,
    ActionPlanPatch,
    ActionPlanPatchOperation,
    ActionSpec,
    SupervisorDecision,
)
from research_system.schemas.subagents import SubagentCapabilityGap


class _BlackboardStub:
    def __init__(self, entries=None) -> None:
        self.entries = list(entries or [])
        self.calls: list[str] = []

    def list_entries(self, *, thread_id: str):
        self.calls.append(thread_id)
        return list(self.entries)


class _SupervisorStub:
    def __init__(self, decision: SupervisorDecision) -> None:
        self.decision = decision
        self.calls: list[dict] = []

    def decide(self, *, plan, completed_actions, pending_actions, blackboard_entries):
        self.calls.append(
            {
                "plan": plan,
                "completed_actions": list(completed_actions),
                "pending_actions": list(pending_actions),
                "blackboard_entries": list(blackboard_entries),
            }
        )
        return self.decision


class _TransitionControllerStub:
    def __init__(self, rows=None) -> None:
        self.rows = list(rows or [])
        self.supervisor_calls: list[dict] = []
        self.escalation_calls: list[dict] = []

    def handle_supervisor_plan_patch(self, **kwargs):
        self.supervisor_calls.append(dict(kwargs))
        return list(self.rows)

    def handle_subagent_escalation(self, **kwargs):
        self.escalation_calls.append(dict(kwargs))
        return list(self.rows)


class _HostStub:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def _emit_event(self, context, **kwargs) -> None:
        self.events.append(dict(kwargs))


class RuntimeControlProtocolControllerTests(unittest.TestCase):
    def _build_context(self):
        return SimpleNamespace(
            thread=SimpleNamespace(thread_id="thread-runtime-control"),
            blackboard=_BlackboardStub(entries=[{"kind": "source_record"}]),
            runtime_control_stage_decisions=[],
            dynamic_trace=[],
        )

    def test_supervisor_cycle_without_plan_patch_does_not_record_protocol_row(self) -> None:
        collect = ActionSpec(node_id="collect", phase="collect", agent="collector")
        decision = SupervisorDecision(
            decision_type="continue",
            next_actions=[collect],
            completed_action_ids=[],
            notes=[],
        )
        host = _HostStub()
        supervisor = _SupervisorStub(decision)
        transition_controller = _TransitionControllerStub(rows=[])
        controller = OrchestratorRuntimeControlProtocolController(
            host,
            supervisor=supervisor,
            transition_controller=transition_controller,
        )
        context = self._build_context()
        request = SimpleNamespace(request_id="req-runtime-protocol")
        plan = ActionPlan(intent="stock_check", actions=[collect])

        resolution = controller.resolve_supervisor_cycle(
            context=context,
            request=request,
            plan=plan,
            ready_actions=[collect],
            pending_actions=[collect],
            completed_node_ids=[],
            skipped_node_ids=[],
        )

        self.assertIs(resolution.decision, decision)
        self.assertEqual(resolution.plan_patch_rows, [])
        self.assertEqual(len(supervisor.calls), 1)
        self.assertEqual(len(transition_controller.supervisor_calls), 0)
        self.assertEqual(context.runtime_control_stage_decisions, [])
        self.assertEqual(context.dynamic_trace, [])
        self.assertEqual(host.events, [])

    def test_supervisor_cycle_with_plan_patch_records_protocol_row(self) -> None:
        review = ActionSpec(node_id="review", phase="review", agent="reviewer")
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
        decision = SupervisorDecision(
            decision_type="continue",
            next_actions=[review],
            completed_action_ids=["collect"],
            notes=["insert review"],
            plan_patch=patch,
        )
        host = _HostStub()
        supervisor = _SupervisorStub(decision)
        transition_controller = _TransitionControllerStub(rows=[{"patch_id": patch.patch_id}])
        controller = OrchestratorRuntimeControlProtocolController(
            host,
            supervisor=supervisor,
            transition_controller=transition_controller,
        )
        context = self._build_context()
        request = SimpleNamespace(request_id="req-runtime-protocol")
        collect = ActionSpec(node_id="collect", phase="collect", agent="collector")
        plan = ActionPlan(intent="review_thesis", actions=[collect, review])

        resolution = controller.resolve_supervisor_cycle(
            context=context,
            request=request,
            plan=plan,
            ready_actions=[review],
            pending_actions=[review],
            completed_node_ids=["collect"],
            skipped_node_ids=[],
        )

        self.assertIs(resolution.decision, decision)
        self.assertEqual(resolution.plan_patch_rows, [{"patch_id": patch.patch_id}])
        self.assertEqual(len(transition_controller.supervisor_calls), 1)
        self.assertEqual(len(context.runtime_control_stage_decisions), 1)
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["event"],
            "runtime_control_protocol_resolved",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["resolution_source"],
            "supervisor_cycle",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["transition_type"],
            "apply_plan_patch",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["next_action_node_ids"],
            ["review"],
        )
        self.assertEqual(host.events[0]["event_type"], "runtime_control_protocol_resolved")
        self.assertEqual(context.dynamic_trace[0]["event"], "runtime_control_protocol_resolved")

    def test_subagent_escalation_records_protocol_row(self) -> None:
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
                notes=("skip_optional_review_phase",),
            ),
            plan_patch=patch,
        )
        host = _HostStub()
        supervisor = _SupervisorStub(
            SupervisorDecision(decision_type="stop", completed_action_ids=[], notes=[])
        )
        transition_controller = _TransitionControllerStub(rows=[{"patch_id": patch.patch_id}])
        controller = OrchestratorRuntimeControlProtocolController(
            host,
            supervisor=supervisor,
            transition_controller=transition_controller,
        )
        context = self._build_context()
        request = SimpleNamespace(request_id="req-runtime-protocol")
        review = ActionSpec(node_id="review", phase="review", agent="reviewer")
        plan = ActionPlan(intent="review_thesis", actions=[review])

        rows = controller.handle_subagent_escalation(
            context=context,
            request=request,
            plan=plan,
            pending_actions=[review],
            completed_node_ids=["collect"],
            skipped_node_ids=[],
            action=review,
            escalation_error=escalation_error,
        )

        self.assertEqual(rows, [{"patch_id": patch.patch_id}])
        self.assertEqual(len(transition_controller.escalation_calls), 1)
        self.assertEqual(len(context.runtime_control_stage_decisions), 1)
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["resolution_source"],
            "subagent_escalation",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["transition_type"],
            "request_action_replan",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["trigger_action_node_id"],
            "review",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["capability"],
            "missing_review_capability",
        )
        self.assertEqual(host.events[0]["event_type"], "runtime_control_protocol_resolved")


if __name__ == "__main__":
    unittest.main()
