from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.agents._subagent_runtime_control_gateway import (
    SubagentRuntimeControlGateway,
)


class _CatalogControllerStub:
    def __init__(self) -> None:
        self.select_calls: list[dict] = []
        self.resolve_calls: list[dict] = []

    def select_catalog_entry(self, **kwargs):
        self.select_calls.append(dict(kwargs))
        return "selected-locally"

    def resolve_catalog_entry(self, **kwargs):
        self.resolve_calls.append(dict(kwargs))
        return "resolved-locally"


class _PhaseExecutionControllerStub:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute_patch_cycle(self, **kwargs):
        self.calls.append(dict(kwargs))
        return "phase-locally"


class _EscalationControllerStub:
    def __init__(self) -> None:
        self.resolve_local_calls: list[dict] = []

    def resolve_local(self, **kwargs):
        self.resolve_local_calls.append(dict(kwargs))
        return "gap-locally"


class SubagentRuntimeControlGatewayTests(unittest.TestCase):
    def test_select_catalog_entry_delegates_to_runtime_control_with_binding(self) -> None:
        delegated_calls: list[dict] = []

        class _RuntimeControlStub:
            def select_subagent_catalog_entry(self, **kwargs):
                delegated_calls.append(dict(kwargs))
                return "selected-remotely"

        gateway = SubagentRuntimeControlGateway(
            runtime_control_resolver=lambda: _RuntimeControlStub(),
            runtime_binding_getter=lambda: {
                "request_id": "req-subagent-gateway",
                "intent": "review_thesis",
            },
        )
        catalog_controller = _CatalogControllerStub()

        selected = gateway.select_catalog_entry(
            catalog_controller=catalog_controller,
            stage="review",
            intent="review_thesis",
            capability="review_strategy",
        )

        self.assertEqual(selected, "selected-remotely")
        self.assertEqual(len(delegated_calls), 1)
        self.assertIs(delegated_calls[0]["catalog_controller"], catalog_controller)
        self.assertEqual(delegated_calls[0]["runtime_binding"]["request_id"], "req-subagent-gateway")
        self.assertEqual(catalog_controller.select_calls, [])

    def test_execute_patch_cycle_falls_back_to_local_phase_controller(self) -> None:
        gateway = SubagentRuntimeControlGateway()
        phase_execution_controller = _PhaseExecutionControllerStub()

        resolved = gateway.execute_patch_cycle(
            phase_execution_controller=phase_execution_controller,
            dag_id="review_execution",
        )

        self.assertEqual(resolved, "phase-locally")
        self.assertEqual(len(phase_execution_controller.calls), 1)
        self.assertEqual(phase_execution_controller.calls[0]["dag_id"], "review_execution")

    def test_resolve_capability_gap_escalation_delegates_or_falls_back_to_local(self) -> None:
        delegated_calls: list[dict] = []

        class _RuntimeControlStub:
            def resolve_subagent_capability_gap_escalation(self, **kwargs):
                delegated_calls.append(dict(kwargs))
                return "gap-remotely"

        gateway = SubagentRuntimeControlGateway(
            runtime_control_resolver=lambda: _RuntimeControlStub(),
            runtime_binding_getter=lambda: {"request_id": "req-gap"},
        )
        escalation_controller = _EscalationControllerStub()

        resolved = gateway.resolve_capability_gap_escalation(
            escalation_controller=escalation_controller,
            gap=SimpleNamespace(capability="missing_review_capability"),
            preferred_subagent_type=None,
            requires_llm=None,
        )

        self.assertEqual(resolved, "gap-remotely")
        self.assertEqual(len(delegated_calls), 1)
        self.assertIs(delegated_calls[0]["escalation_controller"], escalation_controller)
        self.assertEqual(delegated_calls[0]["runtime_binding"]["request_id"], "req-gap")
        self.assertEqual(escalation_controller.resolve_local_calls, [])

        fallback_gateway = SubagentRuntimeControlGateway()
        fallback_resolved = fallback_gateway.resolve_capability_gap_escalation(
            escalation_controller=escalation_controller,
            gap=SimpleNamespace(capability="missing_review_capability"),
            preferred_subagent_type=None,
            requires_llm=None,
        )

        self.assertEqual(fallback_resolved, "gap-locally")
        self.assertEqual(len(escalation_controller.resolve_local_calls), 1)


if __name__ == "__main__":
    unittest.main()
