from __future__ import annotations

import unittest

from research_system.adapters.vendors.mcp_source_registry import (
    McpServerSource,
    McpSourceRegistry,
    McpToolDefinition,
)
from research_system.runtime.orchestration.subagent_discovery_candidate_sources import (
    SUPPORTED_DISCOVERY_CANDIDATE_SOURCE_TYPES,
    SubagentDiscoveryCandidateSupport,
)
from research_system.schemas.subagent_runtime_registration_hints import (
    runtime_registration_hint_payload,
)
from research_system.schemas.subagents import (
    SubagentCapabilityGap,
    SubagentDiscoveryTicket,
    SubagentToolDiscoveryRequest,
)


class SubagentDiscoveryCandidateSourceTests(unittest.TestCase):
    def test_support_reports_supported_source_types(self) -> None:
        support = SubagentDiscoveryCandidateSupport()
        self.assertEqual(
            support.source_types(),
            SUPPORTED_DISCOVERY_CANDIDATE_SOURCE_TYPES,
        )
        self.assertEqual(SUPPORTED_DISCOVERY_CANDIDATE_SOURCE_TYPES, ("mcp",))

    def test_mcp_source_generates_candidates_only_for_matching_tools(self) -> None:
        registry = McpSourceRegistry(
            servers=[
                McpServerSource(
                    id="mock_market_tools",
                    name="Mock Market Tools",
                    transport="stdio",
                    command="python",
                    timeout_seconds=9.5,
                    tools=(
                        McpToolDefinition(
                            name="market_signal_lookup",
                            summary="Lookup market signal details.",
                            read_only=True,
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
                            capabilities=("market_signal_extension",),
                            suitable_intents=("stock_check",),
                            stage="collect",
                            candidate_subagent_type="candidate_collect_market_signal_lookup",
                        ),
                        McpToolDefinition(
                            name="wrong_capability",
                            read_only=True,
                            input_json_schema={
                                "type": "object",
                                "properties": {"symbol": {"type": "string"}},
                            },
                            output_json_schema={
                                "type": "object",
                                "properties": {"signal": {"type": "string"}},
                            },
                            capabilities=("peer_review",),
                            suitable_intents=("stock_check",),
                            stage="collect",
                        ),
                        McpToolDefinition(
                            name="wrong_intent",
                            read_only=True,
                            input_json_schema={
                                "type": "object",
                                "properties": {"symbol": {"type": "string"}},
                            },
                            output_json_schema={
                                "type": "object",
                                "properties": {"signal": {"type": "string"}},
                            },
                            capabilities=("market_signal_extension",),
                            suitable_intents=("review_thesis",),
                            stage="collect",
                        ),
                        McpToolDefinition(
                            name="missing_output_contract",
                            read_only=True,
                            input_json_schema={
                                "type": "object",
                                "properties": {"symbol": {"type": "string"}},
                            },
                            capabilities=("market_signal_extension",),
                            suitable_intents=("stock_check",),
                            stage="collect",
                        ),
                        McpToolDefinition(
                            name="write_tool",
                            read_only=False,
                            input_json_schema={
                                "type": "object",
                                "properties": {"symbol": {"type": "string"}},
                            },
                            output_json_schema={
                                "type": "object",
                                "properties": {"signal": {"type": "string"}},
                            },
                            capabilities=("market_signal_extension",),
                            suitable_intents=("stock_check",),
                            stage="collect",
                        ),
                    ),
                )
            ]
        )
        host = type(
            "_Host",
            (),
            {
                "require_mcp_source_registry": lambda self: registry,
            },
        )()
        support = SubagentDiscoveryCandidateSupport(host)
        ticket = SubagentDiscoveryTicket(
            ticket_id="ticket_market_signal_extension",
            capability_gap=SubagentCapabilityGap(
                intent="stock_check",
                stage="collect",
                capability="market_signal_extension",
                preferred_subagent_type="candidate_collect_market_signal_extension",
                reason="missing market signal extension capability",
            ),
            tool_discovery_request=SubagentToolDiscoveryRequest(
                intent="stock_check",
                stage="collect",
                capability="market_signal_extension",
                search_query="stock_check collect market_signal_extension",
                preferred_protocols=["mcp"],
                reason="discover controlled market signal extension",
            ),
        )

        entries = support.candidate_entries(ticket)

        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.subagent_type, "candidate_collect_market_signal_lookup")
        self.assertEqual(entry.capabilities, ["market_signal_extension"])
        self.assertEqual(entry.stage, "collect")
        self.assertEqual(entry.suitable_intents, ["stock_check"])
        self.assertEqual(entry.side_effect_level, "read_only")
        self.assertIn("configured_mcp_tool", entry.tags)
        self.assertIn("mcp_source:mock_market_tools", entry.tags)
        self.assertIn("pending_runtime_registration", entry.tags)
        self.assertEqual(
            entry.runtime_registration_templates[0].template_id,
            "mcp:mock_market_tools:market_signal_lookup",
        )
        self.assertEqual(
            runtime_registration_hint_payload(entry.runtime_registration_hints, section="mcp"),
            {
                "source_id": "mock_market_tools",
                "tool_name": "market_signal_lookup",
                "timeout_seconds": 9.5,
                "summary": "Lookup market signal details.",
            },
        )


if __name__ == "__main__":
    unittest.main()
