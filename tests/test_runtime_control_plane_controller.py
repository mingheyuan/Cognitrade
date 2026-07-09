from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.runtime.execution.core.runtime_control_plane_controller import (
    OrchestratorRuntimeControlPlaneController,
)


class _SupervisorStub:
    def __init__(self) -> None:
        self.bind_calls: list[dict] = []
        self.clear_calls = 0

    def bind_runtime(self, **kwargs) -> None:
        self.bind_calls.append(dict(kwargs))

    def clear_runtime(self) -> None:
        self.clear_calls += 1


class _ProtocolControllerStub:
    def __init__(self) -> None:
        self.resolve_calls: list[dict] = []
        self.escalation_calls: list[dict] = []

    def resolve_supervisor_cycle(self, **kwargs):
        self.resolve_calls.append(dict(kwargs))
        return "protocol-resolution"

    def handle_subagent_escalation(self, **kwargs):
        self.escalation_calls.append(dict(kwargs))
        return ["escalated"]


class RuntimeControlPlaneControllerTests(unittest.TestCase):
    def test_bind_and_clear_runtime_delegate_to_supervisor(self) -> None:
        supervisor = _SupervisorStub()
        controller = OrchestratorRuntimeControlPlaneController(
            host=SimpleNamespace(),
            supervisor=supervisor,
        )

        controller.bind_runtime(
            context="context",
            support="support",
            request_id="req-1",
            intent="stock_screen",
        )
        controller.clear_runtime()

        self.assertEqual(len(supervisor.bind_calls), 1)
        self.assertEqual(supervisor.bind_calls[0]["request_id"], "req-1")
        self.assertEqual(supervisor.bind_calls[0]["intent"], "stock_screen")
        self.assertEqual(supervisor.clear_calls, 1)

    def test_register_execution_callbacks_and_delegate_resolution(self) -> None:
        supervisor = _SupervisorStub()
        controller = OrchestratorRuntimeControlPlaneController(
            host=SimpleNamespace(),
            supervisor=supervisor,
        )
        protocol = _ProtocolControllerStub()
        controller.protocol_controller = protocol

        result = controller.resolve_supervisor_cycle(
            context="context",
            request="request",
            plan="plan",
            ready_actions=["ready"],
            pending_actions=["pending"],
            completed_node_ids=["collect"],
            skipped_node_ids=[],
        )
        escalation = controller.handle_subagent_escalation(
            context="context",
            request="request",
            plan="plan",
            pending_actions=["pending"],
            completed_node_ids=["collect"],
            skipped_node_ids=[],
            action="review",
            escalation_error="error",
        )

        self.assertEqual(result, "protocol-resolution")
        self.assertEqual(escalation, ["escalated"])
        self.assertEqual(len(protocol.resolve_calls), 1)
        self.assertEqual(len(protocol.escalation_calls), 1)

    def test_resolve_before_registering_execution_callbacks_raises(self) -> None:
        controller = OrchestratorRuntimeControlPlaneController(
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


if __name__ == "__main__":
    unittest.main()
