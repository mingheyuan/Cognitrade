from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.runtime.execution.core.runtime_action_control_service import (
    OrchestratorRuntimeActionControlService,
)


class _ProtocolControllerStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def register_execution_callbacks(self, **kwargs) -> None:
        self.calls.append(("register_execution_callbacks", dict(kwargs)))

    def resolve_supervisor_cycle(self, **kwargs):
        self.calls.append(("resolve_supervisor_cycle", dict(kwargs)))
        return "action-resolution"

    def handle_subagent_escalation(self, **kwargs):
        self.calls.append(("handle_subagent_escalation", dict(kwargs)))
        return [{"patch_id": "patch-1"}]


class RuntimeActionControlServiceTests(unittest.TestCase):
    def test_service_delegates_action_runtime_control_calls(self) -> None:
        protocol_controller = _ProtocolControllerStub()
        service = OrchestratorRuntimeActionControlService(
            controller=SimpleNamespace(protocol_controller=protocol_controller),
        )

        service.register_execution_callbacks(
            apply_plan_patch="apply",
            request_action_plan_replan="replan",
        )
        self.assertEqual(
            service.resolve_supervisor_cycle(plan="plan"),
            "action-resolution",
        )
        self.assertEqual(
            service.handle_subagent_escalation(action="review"),
            [{"patch_id": "patch-1"}],
        )
        self.assertEqual(
            [name for name, _ in protocol_controller.calls],
            [
                "register_execution_callbacks",
                "resolve_supervisor_cycle",
                "handle_subagent_escalation",
            ],
        )


if __name__ == "__main__":
    unittest.main()
