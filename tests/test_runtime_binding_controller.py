from __future__ import annotations

import unittest

from research_system.runtime.execution.core.runtime_binding_controller import (
    OrchestratorRuntimeBindingController,
)


class _SupervisorStub:
    def __init__(self) -> None:
        self.bind_calls: list[dict] = []
        self.clear_calls = 0

    def bind_runtime(self, **kwargs) -> None:
        self.bind_calls.append(dict(kwargs))

    def clear_runtime(self) -> None:
        self.clear_calls += 1


class RuntimeBindingControllerTests(unittest.TestCase):
    def test_controller_binds_and_clears_supervisor_runtime(self) -> None:
        supervisor = _SupervisorStub()
        controller = OrchestratorRuntimeBindingController(supervisor=supervisor)

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


if __name__ == "__main__":
    unittest.main()
