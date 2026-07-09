from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.runtime.execution.core.runtime_orchestration_controller import (
    OrchestratorRuntimeOrchestrationController,
)


class _BindingControllerStub:
    def __init__(self) -> None:
        self.bind_calls: list[dict] = []
        self.clear_calls = 0

    def bind_runtime(self, **kwargs) -> None:
        self.bind_calls.append(dict(kwargs))

    def clear_runtime(self) -> None:
        self.clear_calls += 1


class _ActionControlStub:
    def __init__(self) -> None:
        self.register_calls: list[dict] = []
        self.resolve_calls: list[dict] = []
        self.escalation_calls: list[dict] = []

    def register_execution_callbacks(self, **kwargs) -> None:
        self.register_calls.append(dict(kwargs))

    def resolve_supervisor_cycle(self, **kwargs):
        self.resolve_calls.append(dict(kwargs))
        return "action-resolution"

    def handle_subagent_escalation(self, **kwargs):
        self.escalation_calls.append(dict(kwargs))
        return [{"patch_id": "patch-1"}]


class _ServiceBindingControllerStub:
    def __init__(self) -> None:
        self.bind_calls: list[dict] = []
        self.clear_calls: list[dict] = []

    def bind_agent_service_runtime(self, **kwargs) -> None:
        self.bind_calls.append(dict(kwargs))

    def clear_agent_service_runtime(self, **kwargs) -> None:
        self.clear_calls.append(dict(kwargs))


class _SubagentControlStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def resolve_subagent_capability_gap_escalation(self, **kwargs):
        self.calls.append(("resolve_subagent_capability_gap_escalation", dict(kwargs)))
        return "gap-escalation"

    def resolve_subagent_catalog_entry(self, **kwargs):
        self.calls.append(("resolve_subagent_catalog_entry", dict(kwargs)))
        return "catalog-resolution"

    def select_subagent_catalog_entry(self, **kwargs):
        self.calls.append(("select_subagent_catalog_entry", dict(kwargs)))
        return "catalog-selection"

    def execute_subagent_phase_patch_cycle(self, **kwargs):
        self.calls.append(("execute_subagent_phase_patch_cycle", dict(kwargs)))
        return "patch-cycle"


class RuntimeOrchestrationControllerTests(unittest.TestCase):
    def test_controller_delegates_to_split_runtime_services(self) -> None:
        binding = _BindingControllerStub()
        action = _ActionControlStub()
        service_binding = _ServiceBindingControllerStub()
        subagent = _SubagentControlStub()
        controller = OrchestratorRuntimeOrchestrationController(
            control_plane=SimpleNamespace(
                host="host",
                protocol_controller="protocol",
                transition_decision_source="transition-source",
                protocol_decision_source="protocol-source",
                subagent_controller="subagent-controller",
                subagent_transition_controller="subagent-transition-controller",
                subagent_protocol_controller="subagent-protocol-controller",
                runtime_binding_controller=binding,
                action_runtime_control=action,
                subagent_runtime_control=subagent,
                service_binding_controller=service_binding,
            )
        )

        controller.bind_runtime(
            context="context",
            support="support",
            request_id="req-1",
            intent="stock_screen",
        )
        controller.clear_runtime()
        controller.register_execution_callbacks(
            apply_plan_patch="apply",
            request_action_plan_replan="replan",
        )
        self.assertEqual(controller.resolve_supervisor_cycle(plan="plan"), "action-resolution")
        self.assertEqual(
            controller.handle_subagent_escalation(action="review"),
            [{"patch_id": "patch-1"}],
        )
        controller.bind_agent_service_runtime(service="service")
        controller.clear_agent_service_runtime(service="service")
        self.assertEqual(
            controller.resolve_subagent_capability_gap_escalation(gap="gap"),
            "gap-escalation",
        )
        self.assertEqual(
            controller.resolve_subagent_catalog_entry(capability="collect"),
            "catalog-resolution",
        )
        self.assertEqual(
            controller.select_subagent_catalog_entry(capability="collect"),
            "catalog-selection",
        )
        self.assertEqual(
            controller.execute_subagent_phase_patch_cycle(plan="plan"),
            "patch-cycle",
        )

        self.assertEqual(len(binding.bind_calls), 1)
        self.assertEqual(binding.clear_calls, 1)
        self.assertEqual(len(action.register_calls), 1)
        self.assertEqual(len(action.resolve_calls), 1)
        self.assertEqual(len(action.escalation_calls), 1)
        self.assertEqual(len(service_binding.bind_calls), 1)
        self.assertEqual(len(service_binding.clear_calls), 1)
        self.assertEqual(
            [name for name, _ in subagent.calls],
            [
                "resolve_subagent_capability_gap_escalation",
                "resolve_subagent_catalog_entry",
                "select_subagent_catalog_entry",
                "execute_subagent_phase_patch_cycle",
            ],
        )


if __name__ == "__main__":
    unittest.main()
