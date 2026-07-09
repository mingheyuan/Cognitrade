from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.runtime.execution.core.runtime_service_binding_controller import (
    OrchestratorRuntimeServiceBindingController,
)


class _ServiceStub:
    def __init__(self) -> None:
        self.bind_calls: list[dict] = []
        self.clear_calls = 0

    def bind_runtime(self, **kwargs) -> None:
        self.bind_calls.append(dict(kwargs))

    def clear_runtime(self) -> None:
        self.clear_calls += 1


class RuntimeServiceBindingControllerTests(unittest.TestCase):
    def test_controller_binds_and_clears_agent_service_runtime(self) -> None:
        host = SimpleNamespace(
            subagent_discovery_store="store",
            skill_runtime="skill-runtime",
            mcp_source_registry="mcp-registry",
            http_json_source_registry="http-registry",
            openapi_source_registry="openapi-registry",
            skill_source_registry="skill-source-registry",
            require_subagent_discovery_store=lambda: "store",
            require_skill_runtime=lambda: "skill-runtime",
            require_mcp_source_registry=lambda: "mcp-registry",
            require_http_json_source_registry=lambda: "http-registry",
            require_openapi_source_registry=lambda: "openapi-registry",
            require_skill_source_registry=lambda: "skill-source-registry",
        )
        controller = OrchestratorRuntimeServiceBindingController(
            host,
            subagent_runtime_control="subagent-runtime-control",
        )
        context = SimpleNamespace(
            agent_control="agent-control",
            execution_policy="execution-policy",
            thread=SimpleNamespace(thread_id="thread-1"),
            turn=SimpleNamespace(turn_id="turn-1"),
        )
        service = _ServiceStub()

        controller.bind_agent_service_runtime(
            service=service,
            context=context,
            support="support",
            request_id="req-1",
            intent="stock_screen",
            agent="collector",
            phase="collect",
        )
        controller.clear_agent_service_runtime(service=service)

        self.assertEqual(len(service.bind_calls), 1)
        bind_call = service.bind_calls[0]
        self.assertEqual(bind_call["runtime_control"], "subagent-runtime-control")
        self.assertEqual(bind_call["thread_id"], "thread-1")
        self.assertEqual(bind_call["turn_id"], "turn-1")
        self.assertEqual(bind_call["request_id"], "req-1")
        self.assertEqual(bind_call["agent"], "collector")
        self.assertEqual(bind_call["phase"], "collect")
        self.assertEqual(bind_call["require_mcp_source_registry"](), "mcp-registry")
        self.assertEqual(bind_call["require_http_json_source_registry"](), "http-registry")
        self.assertEqual(bind_call["require_openapi_source_registry"](), "openapi-registry")
        self.assertEqual(bind_call["require_skill_source_registry"](), "skill-source-registry")
        self.assertEqual(service.clear_calls, 1)


if __name__ == "__main__":
    unittest.main()
