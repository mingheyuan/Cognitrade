from __future__ import annotations

import unittest

from research_system.runtime.orchestration.subagent_runtime_templates import (
    SUPPORTED_RUNTIME_TEMPLATE_TYPES,
    SubagentRuntimeTemplateSupport,
)
from research_system.schemas.subagents import (
    SubagentCatalogEntry,
    SubagentRuntimeRegistrationTemplate,
)


class SubagentRuntimeTemplateTests(unittest.TestCase):
    def test_template_types_cover_current_runtime_registration_families(self) -> None:
        self.assertEqual(
            SUPPORTED_RUNTIME_TEMPLATE_TYPES,
            (
                "snapshot_skill_runner",
                "skill_runner",
                "http_json_runner",
                "openapi_http_runner",
                "mcp_tool_runner",
            ),
        )

    def test_candidate_templates_deduplicate_generated_templates_against_explicit_entries(self) -> None:
        support = SubagentRuntimeTemplateSupport()
        candidate = SubagentCatalogEntry(
            subagent_type="candidate_report_signal_extension",
            stage="report",
            capabilities=["report_signal_extension"],
            runtime_registration_templates=[
                SubagentRuntimeRegistrationTemplate(
                    template_id="skill:analysis_skill",
                    registration_type="skill_runner",
                    summary="Explicit template should win",
                    defaults={"skill_name": "analysis_skill"},
                )
            ],
            runtime_registration_hints={
                "skill": {
                    "skill_name": "analysis_skill",
                    "summary": "Generated duplicate should be ignored",
                }
            },
        )

        templates = support.candidate_templates(candidate)

        self.assertEqual(len(templates), 1)
        self.assertEqual(templates[0].template_id, "skill:analysis_skill")
        self.assertEqual(templates[0].summary, "Explicit template should win")

    def test_openapi_template_can_infer_single_operation_id_from_hint_spec(self) -> None:
        support = SubagentRuntimeTemplateSupport()
        candidate = SubagentCatalogEntry(
            subagent_type="candidate_collect_market_signal_openapi_extension",
            stage="collect",
            side_effect_level="read_only",
            capabilities=["market_signal_openapi_extension"],
            runtime_registration_hints={
                "openapi": {
                    "source_id": "eastmoney",
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

        template = support.build_template(
            registration_type="openapi_http_runner",
            candidate_entry=candidate,
        )

        self.assertIsNotNone(template)
        assert template is not None
        self.assertEqual(template.template_id, "openapi:getMarketSignal")
        self.assertEqual(template.defaults["openapi_operation_id"], "getMarketSignal")
        self.assertEqual(template.defaults["source_id"], "eastmoney")

    def test_mcp_template_uses_structured_mcp_hints(self) -> None:
        support = SubagentRuntimeTemplateSupport()
        candidate = SubagentCatalogEntry(
            subagent_type="candidate_collect_market_signal_mcp_extension",
            stage="collect",
            capabilities=["market_signal_mcp_extension"],
            runtime_registration_hints={
                "mcp": {
                    "source_id": "mock_market_tools",
                    "tool_name": "market_signal_lookup",
                    "timeout_seconds": 11.5,
                }
            },
        )

        template = support.build_template(
            registration_type="mcp_tool_runner",
            candidate_entry=candidate,
        )

        self.assertIsNotNone(template)
        assert template is not None
        self.assertEqual(template.template_id, "mcp:mock_market_tools:market_signal_lookup")
        self.assertEqual(template.defaults["source_id"], "mock_market_tools")
        self.assertEqual(template.defaults["tool_name"], "market_signal_lookup")


if __name__ == "__main__":
    unittest.main()
