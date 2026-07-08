from __future__ import annotations

import unittest

from research_system.runtime.orchestration.subagent_runtime_source_resolution import (
    SUPPORTED_RUNTIME_SOURCE_RESOLUTION_TYPES,
    SubagentRuntimeSourceResolutionSupport,
)
from research_system.schemas.subagents import SubagentCatalogEntry


class SubagentRuntimeSourceResolutionTests(unittest.TestCase):
    def test_resolution_types_cover_current_http_source_families(self) -> None:
        self.assertEqual(
            SUPPORTED_RUNTIME_SOURCE_RESOLUTION_TYPES,
            ("http_json_runner", "openapi_http_runner", "mcp_tool_runner"),
        )

    def test_http_json_resolution_requires_relative_endpoint(self) -> None:
        support = SubagentRuntimeSourceResolutionSupport(type("_Host", (), {"orchestrator_error_cls": RuntimeError})())
        candidate = SubagentCatalogEntry(
            subagent_type="candidate_collect_market_signal_http_extension",
            stage="collect",
            side_effect_level="read_only",
            capabilities=["market_signal_http_extension"],
        )
        with self.assertRaises(RuntimeError):
            support.resolve(
                registration_type="http_json_runner",
                candidate_entry=candidate,
                source_id="eastmoney",
                endpoint_path="https://example.com/api/market-signal",
                http_method="GET",
            )

    def test_openapi_resolution_can_infer_single_operation_id(self) -> None:
        inferred = SubagentRuntimeSourceResolutionSupport.derive_single_openapi_operation_id(
            {
                "openapi": "3.0.0",
                "paths": {
                    "/api/market-signal": {
                        "get": {
                            "operationId": "getMarketSignal",
                        }
                    }
                },
            }
        )
        self.assertEqual(inferred, "getMarketSignal")

    def test_mcp_resolution_requires_known_read_only_tool(self) -> None:
        support = SubagentRuntimeSourceResolutionSupport(
            type(
                "_Host",
                (),
                {
                    "orchestrator_error_cls": RuntimeError,
                    "require_mcp_source_registry": lambda self: __import__(
                        "research_system.adapters.vendors.mcp_source_registry",
                        fromlist=["McpSourceRegistry", "McpServerSource", "McpToolDefinition"],
                    ).McpSourceRegistry(
                        servers=[
                            __import__(
                                "research_system.adapters.vendors.mcp_source_registry",
                                fromlist=["McpServerSource", "McpToolDefinition"],
                            ).McpServerSource(
                                id="mock_market_tools",
                                name="Mock Market Tools",
                                transport="stdio",
                                command="python",
                                tools=(
                                    __import__(
                                        "research_system.adapters.vendors.mcp_source_registry",
                                        fromlist=["McpToolDefinition"],
                                    ).McpToolDefinition(
                                        name="market_signal_lookup",
                                        read_only=True,
                                    ),
                                ),
                            )
                        ]
                    ),
                },
            )()
        )
        candidate = SubagentCatalogEntry(
            subagent_type="candidate_collect_market_signal_mcp_extension",
            stage="collect",
            side_effect_level="read_only",
            capabilities=["market_signal_mcp_extension"],
        )
        resolved = support.resolve(
            registration_type="mcp_tool_runner",
            candidate_entry=candidate,
            source_id="mock_market_tools",
            tool_name="market_signal_lookup",
        )
        self.assertEqual(resolved["source_id"], "mock_market_tools")
        self.assertEqual(resolved["tool_name"], "market_signal_lookup")


if __name__ == "__main__":
    unittest.main()
