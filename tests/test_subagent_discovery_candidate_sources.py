from __future__ import annotations

import unittest

from research_system.adapters.vendors.http_json_source_registry import (
    HttpJsonEndpointDefinition,
    HttpJsonSourceRegistry,
)
from research_system.adapters.vendors.mcp_source_registry import (
    McpServerSource,
    McpSourceRegistry,
    McpToolDefinition,
)
from research_system.adapters.vendors.openapi_source_registry import (
    OpenApiOperationDefinition,
    OpenApiSourceRegistry,
)
from research_system.adapters.vendors.skill_source_registry import (
    SkillSourceDefinition,
    SkillSourceRegistry,
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
        self.assertEqual(SUPPORTED_DISCOVERY_CANDIDATE_SOURCE_TYPES, ("mcp", "openapi", "skill", "http_json"))

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

    def test_openapi_source_generates_candidates_only_for_matching_operations(self) -> None:
        registry = OpenApiSourceRegistry(
            operations=[
                OpenApiOperationDefinition(
                    source_id="eastmoney",
                    operation_id="getMarketSignal",
                    summary="Lookup one market signal via OpenAPI.",
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
                    candidate_subagent_type="candidate_collect_market_signal_openapi",
                    spec={
                        "openapi": "3.0.0",
                        "paths": {
                            "/api/market-signal": {
                                "get": {"operationId": "getMarketSignal"}
                            }
                        },
                    },
                ),
                OpenApiOperationDefinition(
                    source_id="eastmoney",
                    operation_id="wrongIntent",
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
                    spec={
                        "openapi": "3.0.0",
                        "paths": {
                            "/api/review-signal": {
                                "get": {"operationId": "wrongIntent"}
                            }
                        },
                    },
                ),
                OpenApiOperationDefinition(
                    source_id="tushare",
                    operation_id="keyedSource",
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
                    suitable_intents=("stock_check",),
                    stage="collect",
                    spec={
                        "openapi": "3.0.0",
                        "paths": {
                            "/api/keyed-signal": {
                                "get": {"operationId": "keyedSource"}
                            }
                        },
                    },
                ),
            ]
        )
        host = type(
            "_Host",
            (),
            {
                "require_openapi_source_registry": lambda self: registry,
            },
        )()
        support = SubagentDiscoveryCandidateSupport(host)
        ticket = SubagentDiscoveryTicket(
            ticket_id="ticket_market_signal_openapi",
            capability_gap=SubagentCapabilityGap(
                intent="stock_check",
                stage="collect",
                capability="market_signal_extension",
                preferred_subagent_type="candidate_collect_market_signal_extension",
                reason="missing openapi market signal extension capability",
            ),
            tool_discovery_request=SubagentToolDiscoveryRequest(
                intent="stock_check",
                stage="collect",
                capability="market_signal_extension",
                search_query="stock_check collect market_signal_extension",
                preferred_protocols=["openapi"],
                reason="discover controlled openapi market signal extension",
            ),
        )

        entries = support.candidate_entries(ticket)

        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.subagent_type, "candidate_collect_market_signal_openapi")
        self.assertEqual(entry.capabilities, ["market_signal_extension"])
        self.assertEqual(entry.stage, "collect")
        self.assertEqual(entry.suitable_intents, ["stock_check"])
        self.assertIn("configured_openapi_operation", entry.tags)
        self.assertIn("openapi_source:eastmoney", entry.tags)
        self.assertEqual(
            entry.runtime_registration_templates[0].template_id,
            "openapi:getMarketSignal",
        )
        self.assertEqual(
            runtime_registration_hint_payload(entry.runtime_registration_hints, section="openapi"),
            {
                "source_id": "eastmoney",
                "operation_id": "getMarketSignal",
                "summary": "Lookup one market signal via OpenAPI.",
                "spec": {
                    "openapi": "3.0.0",
                    "paths": {
                        "/api/market-signal": {
                            "get": {"operationId": "getMarketSignal"}
                        }
                    },
                },
            },
        )

    def test_skill_source_generates_snapshot_and_generic_skill_candidates(self) -> None:
        registry = SkillSourceRegistry(
            sources=[
                SkillSourceDefinition(
                    skill_name="market_snapshot_skill",
                    registration_type="snapshot_skill_runner",
                    summary="Collect market snapshot through local runtime.",
                    snapshot_type="market",
                    capabilities=("market_signal_extension",),
                    suitable_intents=("stock_check",),
                    stage="collect",
                    candidate_subagent_type="candidate_collect_market_snapshot_skill",
                ),
                SkillSourceDefinition(
                    skill_name="analysis_skill",
                    registration_type="skill_runner",
                    summary="Run local report analysis skill.",
                    capabilities=("report_signal_extension",),
                    suitable_intents=("review_thesis",),
                    stage="report",
                    candidate_subagent_type="candidate_report_analysis_skill",
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
                SkillSourceDefinition(
                    skill_name="missing_skill",
                    registration_type="skill_runner",
                    summary="Configured but not installed skill.",
                    capabilities=("report_signal_extension",),
                    suitable_intents=("review_thesis",),
                    stage="report",
                    input_json_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                    output_json_schema={
                        "type": "object",
                        "properties": {"summary": {"type": "string"}},
                    },
                ),
            ]
        )
        host = type(
            "_Host",
            (),
            {
                "require_skill_source_registry": lambda self: registry,
                "require_skill_runtime": lambda self: type(
                    "_Runtime",
                    (),
                    {
                        "backend": type(
                            "_Backend",
                            (),
                            {
                                "registry": {
                                    "market_snapshot_skill": object(),
                                    "analysis_skill": object(),
                                }
                            },
                        )()
                    },
                )(),
            },
        )()
        support = SubagentDiscoveryCandidateSupport(host)

        snapshot_ticket = SubagentDiscoveryTicket(
            ticket_id="ticket_snapshot_skill",
            capability_gap=SubagentCapabilityGap(
                intent="stock_check",
                stage="collect",
                capability="market_signal_extension",
                preferred_subagent_type="candidate_collect_market_signal_extension",
                reason="missing snapshot skill extension capability",
            ),
            tool_discovery_request=SubagentToolDiscoveryRequest(
                intent="stock_check",
                stage="collect",
                capability="market_signal_extension",
                search_query="stock_check collect market_signal_extension",
                preferred_protocols=["skill"],
                reason="discover controlled snapshot skill extension",
            ),
        )
        generic_ticket = SubagentDiscoveryTicket(
            ticket_id="ticket_generic_skill",
            capability_gap=SubagentCapabilityGap(
                intent="review_thesis",
                stage="report",
                capability="report_signal_extension",
                preferred_subagent_type="candidate_report_signal_extension",
                reason="missing generic skill extension capability",
            ),
            tool_discovery_request=SubagentToolDiscoveryRequest(
                intent="review_thesis",
                stage="report",
                capability="report_signal_extension",
                search_query="review_thesis report report_signal_extension",
                preferred_protocols=["skill"],
                reason="discover controlled generic skill extension",
            ),
        )

        snapshot_entries = support.candidate_entries(snapshot_ticket)
        generic_entries = support.candidate_entries(generic_ticket)

        self.assertEqual(len(snapshot_entries), 1)
        snapshot_entry = snapshot_entries[0]
        self.assertEqual(snapshot_entry.subagent_type, "candidate_collect_market_snapshot_skill")
        self.assertEqual(
            snapshot_entry.runtime_registration_templates[0].template_id,
            "snapshot:market:market_snapshot_skill",
        )
        self.assertEqual(
            runtime_registration_hint_payload(snapshot_entry.runtime_registration_hints, section="snapshot_skill"),
            {
                "skill_name": "market_snapshot_skill",
                "snapshot_type": "market",
                "summary": "Collect market snapshot through local runtime.",
            },
        )

        self.assertEqual(len(generic_entries), 1)
        generic_entry = generic_entries[0]
        self.assertEqual(generic_entry.subagent_type, "candidate_report_analysis_skill")
        self.assertEqual(
            generic_entry.runtime_registration_templates[0].template_id,
            "skill:analysis_skill",
        )
        self.assertEqual(
            runtime_registration_hint_payload(generic_entry.runtime_registration_hints, section="skill"),
            {
                "skill_name": "analysis_skill",
                "summary": "Run local report analysis skill.",
            },
        )

    def test_http_json_source_generates_candidates_only_for_matching_endpoints(self) -> None:
        registry = HttpJsonSourceRegistry(
            endpoints=[
                HttpJsonEndpointDefinition(
                    source_id="eastmoney",
                    endpoint_path="/api/market-signal",
                    http_method="GET",
                    summary="Lookup one market signal via HTTP JSON.",
                    timeout_seconds=8.5,
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
                    candidate_subagent_type="candidate_collect_market_signal_http",
                ),
                HttpJsonEndpointDefinition(
                    source_id="eastmoney",
                    endpoint_path="/api/wrong-intent",
                    http_method="GET",
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
                HttpJsonEndpointDefinition(
                    source_id="tushare",
                    endpoint_path="/api/keyed",
                    http_method="GET",
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
            ]
        )
        host = type(
            "_Host",
            (),
            {
                "require_http_json_source_registry": lambda self: registry,
            },
        )()
        support = SubagentDiscoveryCandidateSupport(host)
        ticket = SubagentDiscoveryTicket(
            ticket_id="ticket_market_signal_http_json",
            capability_gap=SubagentCapabilityGap(
                intent="stock_check",
                stage="collect",
                capability="market_signal_extension",
                preferred_subagent_type="candidate_collect_market_signal_extension",
                reason="missing http json market signal extension capability",
            ),
            tool_discovery_request=SubagentToolDiscoveryRequest(
                intent="stock_check",
                stage="collect",
                capability="market_signal_extension",
                search_query="stock_check collect market_signal_extension",
                preferred_protocols=["http_json"],
                reason="discover controlled http json market signal extension",
            ),
        )

        entries = support.candidate_entries(ticket)

        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.subagent_type, "candidate_collect_market_signal_http")
        self.assertIn("configured_http_json_endpoint", entry.tags)
        self.assertIn("http_json_source:eastmoney", entry.tags)
        self.assertEqual(
            entry.runtime_registration_templates[0].template_id,
            "http:eastmoney:get:api_market_signal",
        )
        self.assertEqual(
            runtime_registration_hint_payload(entry.runtime_registration_hints, section="http_json"),
            {
                "source_id": "eastmoney",
                "endpoint_path": "/api/market-signal",
                "http_method": "GET",
                "timeout_seconds": 8.5,
                "summary": "Lookup one market signal via HTTP JSON.",
            },
        )


if __name__ == "__main__":
    unittest.main()
