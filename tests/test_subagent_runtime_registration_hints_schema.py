from __future__ import annotations

import unittest

from research_system.schemas.subagent_runtime_registration_hints import (
    SUPPORTED_RUNTIME_REGISTRATION_HINT_SECTIONS,
    SubagentRuntimeRegistrationHints,
    runtime_registration_hint_payload,
)
from research_system.schemas.subagents import SubagentCatalogEntry


class SubagentRuntimeRegistrationHintsSchemaTests(unittest.TestCase):
    def test_structured_runtime_registration_hints_accept_known_sections(self) -> None:
        hints = SubagentRuntimeRegistrationHints(
            snapshot_skill={
                "skill_name": "market_snapshot_skill",
                "snapshot_type": "market",
            },
            http_json={
                "source_id": "eastmoney",
                "endpoint_path": "/api/market-signal",
                "http_method": "GET",
                "timeout_seconds": 12.5,
            },
            mcp={
                "source_id": "mock_market_tools",
                "tool_name": "market_signal_lookup",
                "timeout_seconds": 11.5,
            },
        )
        self.assertEqual(
            runtime_registration_hint_payload(hints, section="snapshot_skill")["skill_name"],
            "market_snapshot_skill",
        )
        self.assertEqual(
            runtime_registration_hint_payload(hints, section="http_json")["endpoint_path"],
            "/api/market-signal",
        )
        self.assertEqual(
            runtime_registration_hint_payload(hints, section="mcp")["tool_name"],
            "market_signal_lookup",
        )

    def test_structured_runtime_registration_hints_reject_unknown_sections(self) -> None:
        with self.assertRaises(ValueError):
            SubagentRuntimeRegistrationHints.model_validate(
                {
                    "snapshot_skill": {
                        "skill_name": "market_snapshot_skill",
                        "snapshot_type": "market",
                    },
                    "unknown_section": {"foo": "bar"},
                }
            )

    def test_openapi_hints_require_operation_or_spec(self) -> None:
        with self.assertRaises(ValueError):
            SubagentRuntimeRegistrationHints(
                openapi={
                    "source_id": "eastmoney",
                }
            )

    def test_catalog_entry_coerces_runtime_registration_hints_to_structured_model(self) -> None:
        entry = SubagentCatalogEntry(
            subagent_type="candidate_collect_market_signal_openapi_extension",
            capabilities=["market_signal_openapi_extension"],
            runtime_registration_hints={
                "openapi": {
                    "source_id": "eastmoney",
                    "operation_id": "getMarketSignal",
                    "spec": {
                        "openapi": "3.0.0",
                        "paths": {
                            "/api/market-signal": {
                                "get": {
                                    "operationId": "getMarketSignal",
                                }
                            }
                        },
                    },
                }
            },
        )
        self.assertEqual(
            runtime_registration_hint_payload(entry.runtime_registration_hints, section="openapi")["operation_id"],
            "getMarketSignal",
        )

    def test_supported_hint_sections_cover_current_runtime_registration_sources(self) -> None:
        self.assertEqual(
            SUPPORTED_RUNTIME_REGISTRATION_HINT_SECTIONS,
            ("snapshot_skill", "skill", "http_json", "openapi", "mcp"),
        )


if __name__ == "__main__":
    unittest.main()
