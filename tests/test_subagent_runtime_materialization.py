from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.runtime.orchestration.subagent_runtime_materialization import (
    SUPPORTED_RUNTIME_MATERIALIZATION_TYPES,
    SubagentRuntimeMaterializationSupport,
)
from research_system.schemas.subagents import (
    SubagentActivationEntry,
    SubagentCatalogEntry,
    SubagentRuntimeRegistration,
)
from research_system.subagents.base import SubagentCatalogMetadata, SubagentRuntimePolicy
from research_system.subagents.runtime_registered import (
    RuntimeRegisteredHttpJsonSubagent,
    RuntimeRegisteredSkillSubagent,
)


class SubagentRuntimeMaterializationTests(unittest.TestCase):
    def test_materialization_types_cover_current_runtime_registration_families(self) -> None:
        self.assertEqual(
            SUPPORTED_RUNTIME_MATERIALIZATION_TYPES,
            (
                "snapshot_skill_runner",
                "skill_runner",
                "http_json_runner",
                "openapi_http_runner",
                "mcp_tool_runner",
            ),
        )

    def test_skill_runner_materialization_returns_runtime_registered_skill_subagent(self) -> None:
        support = SubagentRuntimeMaterializationSupport()
        activation_entry = SubagentActivationEntry(
            ticket_id="ticket-1",
            subagent_type="candidate_report_signal_extension",
            runtime_registration=SubagentRuntimeRegistration(
                registration_type="skill_runner",
                skill_name="analysis_skill",
            ),
            catalog_entry=SubagentCatalogEntry(
                subagent_type="candidate_report_signal_extension",
                stage="report",
                capabilities=["report_signal_extension"],
                input_json_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                output_json_schema={
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                    "required": ["summary"],
                },
            ),
        )

        subagent = support.materialize_runtime_subagent(
            activation_entry=activation_entry,
            skill_runtime=SimpleNamespace(run=lambda *args, **kwargs: {"summary": "ok"}),
            runtime_policy=SubagentRuntimePolicy(),
            catalog_metadata=SubagentCatalogMetadata(summary="runtime skill"),
            runtime_models=(dict, dict),
        )

        self.assertIsInstance(subagent, RuntimeRegisteredSkillSubagent)
        self.assertEqual(subagent.subagent_type, "candidate_report_signal_extension")
        self.assertEqual(subagent.skill_name, "analysis_skill")
        self.assertIs(subagent.input_model, dict)
        self.assertIs(subagent.output_model, dict)

    def test_snapshot_skill_materialization_rejects_non_collect_stage(self) -> None:
        support = SubagentRuntimeMaterializationSupport()
        activation_entry = SubagentActivationEntry(
            ticket_id="ticket-1",
            subagent_type="candidate_report_snapshot_extension",
            runtime_registration=SubagentRuntimeRegistration(
                registration_type="snapshot_skill_runner",
                skill_name="market_snapshot_skill",
                snapshot_type="market",
            ),
            catalog_entry=SubagentCatalogEntry(
                subagent_type="candidate_report_snapshot_extension",
                stage="report",
                capabilities=["report_snapshot_extension"],
            ),
        )

        subagent = support.materialize_runtime_subagent(
            activation_entry=activation_entry,
            skill_runtime=SimpleNamespace(run=lambda *args, **kwargs: {"snapshot": {}}),
            runtime_policy=SubagentRuntimePolicy(),
            catalog_metadata=SubagentCatalogMetadata(summary="runtime snapshot"),
            runtime_models=(None, None),
        )

        self.assertIsNone(subagent)

    def test_openapi_http_materialization_reuses_http_json_runtime_subagent(self) -> None:
        support = SubagentRuntimeMaterializationSupport()
        activation_entry = SubagentActivationEntry(
            ticket_id="ticket-1",
            subagent_type="candidate_collect_market_signal_openapi_extension",
            runtime_registration=SubagentRuntimeRegistration(
                registration_type="openapi_http_runner",
                source_id="eastmoney",
                endpoint_path="/api/market-signal",
                http_method="GET",
                openapi_operation_id="getMarketSignal",
                timeout_seconds=9.5,
            ),
            catalog_entry=SubagentCatalogEntry(
                subagent_type="candidate_collect_market_signal_openapi_extension",
                stage="collect",
                side_effect_level="read_only",
                capabilities=["market_signal_openapi_extension"],
                input_json_schema={
                    "type": "object",
                    "properties": {"symbol": {"type": "string"}},
                    "required": ["symbol"],
                },
                output_json_schema={
                    "type": "object",
                    "properties": {"signal": {"type": "string"}},
                    "required": ["signal"],
                },
            ),
        )

        subagent = support.materialize_runtime_subagent(
            activation_entry=activation_entry,
            skill_runtime=None,
            runtime_policy=SubagentRuntimePolicy(),
            catalog_metadata=SubagentCatalogMetadata(summary="runtime http"),
            runtime_models=(dict, dict),
        )

        self.assertIsInstance(subagent, RuntimeRegisteredHttpJsonSubagent)
        self.assertEqual(subagent.source_id, "eastmoney")
        self.assertEqual(subagent.endpoint_path, "/api/market-signal")
        self.assertEqual(subagent.http_method, "GET")
        self.assertEqual(subagent.timeout_seconds, 9.5)

    def test_mcp_materialization_returns_runtime_registered_mcp_subagent(self) -> None:
        from research_system.adapters.vendors.mcp_source_registry import (
            McpServerSource,
            McpSourceRegistry,
            McpToolDefinition,
        )
        from research_system.subagents.runtime_registered import RuntimeRegisteredMcpToolSubagent

        support = SubagentRuntimeMaterializationSupport(
            type(
                "_Host",
                (),
                {
                    "require_mcp_source_registry": lambda self: McpSourceRegistry(
                        servers=[
                            McpServerSource(
                                id="mock_market_tools",
                                name="Mock Market Tools",
                                transport="stdio",
                                command="python",
                                tools=(McpToolDefinition(name="market_signal_lookup", read_only=True),),
                                timeout_seconds=11.5,
                            )
                        ]
                    )
                },
            )()
        )
        activation_entry = SubagentActivationEntry(
            ticket_id="ticket-1",
            subagent_type="candidate_collect_market_signal_mcp_extension",
            runtime_registration=SubagentRuntimeRegistration(
                registration_type="mcp_tool_runner",
                source_id="mock_market_tools",
                tool_name="market_signal_lookup",
                timeout_seconds=11.5,
            ),
            catalog_entry=SubagentCatalogEntry(
                subagent_type="candidate_collect_market_signal_mcp_extension",
                stage="collect",
                side_effect_level="read_only",
                capabilities=["market_signal_mcp_extension"],
                input_json_schema={
                    "type": "object",
                    "properties": {"symbol": {"type": "string"}},
                    "required": ["symbol"],
                },
                output_json_schema={
                    "type": "object",
                    "properties": {"signal": {"type": "string"}},
                    "required": ["signal"],
                },
            ),
        )

        subagent = support.materialize_runtime_subagent(
            activation_entry=activation_entry,
            skill_runtime=None,
            runtime_policy=SubagentRuntimePolicy(),
            catalog_metadata=SubagentCatalogMetadata(summary="runtime mcp"),
            runtime_models=(dict, dict),
        )

        self.assertIsInstance(subagent, RuntimeRegisteredMcpToolSubagent)
        self.assertEqual(subagent.tool_name, "market_signal_lookup")
        self.assertEqual(subagent.timeout_seconds, 11.5)


if __name__ == "__main__":
    unittest.main()
