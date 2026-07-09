from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.runtime.execution.core.runtime_protocol_controller import (
    OrchestratorRuntimeProtocolController,
)


class _SupervisorStub:
    def __init__(self) -> None:
        self.bind_calls: list[dict] = []
        self.clear_calls = 0

    def bind_runtime(self, **kwargs) -> None:
        self.bind_calls.append(dict(kwargs))

    def clear_runtime(self) -> None:
        self.clear_calls += 1


class _ActionProtocolStub:
    def __init__(self) -> None:
        self.resolve_calls: list[dict] = []
        self.escalation_calls: list[dict] = []

    def resolve_supervisor_cycle(self, **kwargs):
        self.resolve_calls.append(dict(kwargs))
        return "action-resolution"

    def handle_subagent_escalation(self, **kwargs):
        self.escalation_calls.append(dict(kwargs))
        return [{"patch_id": "patch-action"}]


class _SubagentProtocolStub:
    def __init__(self) -> None:
        self.catalog_calls: list[dict] = []
        self.select_calls: list[dict] = []
        self.phase_calls: list[dict] = []
        self.escalation_calls: list[dict] = []

    def resolve_catalog_entry(self, **kwargs):
        self.catalog_calls.append(dict(kwargs))
        return "catalog-resolution"

    def select_catalog_entry(self, **kwargs):
        self.select_calls.append(dict(kwargs))
        return "selected-entry"

    def execute_patch_cycle(self, **kwargs):
        self.phase_calls.append(dict(kwargs))
        return "phase-resolution"

    def resolve_capability_gap_escalation(self, **kwargs):
        self.escalation_calls.append(dict(kwargs))
        return "gap-resolution"


class RuntimeProtocolControllerTests(unittest.TestCase):
    def test_register_execution_callbacks_shares_transition_and_policy_sources(self) -> None:
        controller = OrchestratorRuntimeProtocolController(
            host=SimpleNamespace(),
            supervisor=_SupervisorStub(),
        )

        self.assertIs(
            controller.subagent_transition_controller.decision_source,
            controller.transition_decision_source,
        )
        self.assertIs(
            controller.subagent_protocol_controller.decision_source,
            controller.decision_source,
        )

        controller.register_execution_callbacks(
            apply_plan_patch=lambda **kwargs: [],
            request_action_plan_replan=lambda **kwargs: [],
        )

        self.assertIsNotNone(controller.action_transition_controller)
        self.assertIsNotNone(controller.action_protocol_controller)
        self.assertIs(
            controller.action_transition_controller.decision_source,
            controller.transition_decision_source,
        )
        self.assertIs(
            controller.action_protocol_controller.decision_source,
            controller.decision_source,
        )

    def test_action_resolution_requires_registered_execution_callbacks(self) -> None:
        controller = OrchestratorRuntimeProtocolController(
            host=SimpleNamespace(),
            supervisor=_SupervisorStub(),
        )

        with self.assertRaises(RuntimeError):
            controller.resolve_supervisor_cycle(
                context="context",
                request="request",
                plan="plan",
                ready_actions=[],
                pending_actions=[],
                completed_node_ids=[],
                skipped_node_ids=[],
            )

    def test_runtime_protocol_controller_delegates_to_action_and_subagent_protocols(self) -> None:
        controller = OrchestratorRuntimeProtocolController(
            host=SimpleNamespace(),
            supervisor=_SupervisorStub(),
        )
        controller.action_protocol_controller = _ActionProtocolStub()
        controller.subagent_protocol_controller = _SubagentProtocolStub()

        self.assertEqual(
            controller.resolve_supervisor_cycle(
                context="context",
                request="request",
                plan="plan",
                ready_actions=["ready"],
                pending_actions=["pending"],
                completed_node_ids=["collect"],
                skipped_node_ids=[],
            ),
            "action-resolution",
        )
        self.assertEqual(
            controller.handle_subagent_escalation(
                context="context",
                request="request",
                plan="plan",
                pending_actions=["pending"],
                completed_node_ids=["collect"],
                skipped_node_ids=[],
                action="review",
                escalation_error="error",
            ),
            [{"patch_id": "patch-action"}],
        )
        self.assertEqual(
            controller.resolve_subagent_capability_gap_escalation(gap="gap"),
            "gap-resolution",
        )
        self.assertEqual(
            controller.resolve_subagent_catalog_entry(catalog_controller="catalog"),
            "catalog-resolution",
        )
        self.assertEqual(
            controller.select_subagent_catalog_entry(catalog_controller="catalog"),
            "selected-entry",
        )
        self.assertEqual(
            controller.execute_subagent_phase_patch_cycle(phase_execution_controller="phase"),
            "phase-resolution",
        )


if __name__ == "__main__":
    unittest.main()
