from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from research_system.runtime.orchestration.subagent_discovery_admin_support import (
    OrchestratorSubagentDiscoveryAdminSupport,
)
from research_system.runtime.orchestration.subagent_discovery_store import SubagentDiscoveryStore
from research_system.schemas.subagents import (
    SubagentCapabilityGap,
    SubagentCatalogEntry,
    SubagentToolDiscoveryRequest,
)


class _DiscoveryCandidateProviderStub:
    available = True

    def __init__(self, *, selected_candidate_subagent_type: str) -> None:
        self.selected_candidate_subagent_type = selected_candidate_subagent_type
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        schema = kwargs["schema"]
        return schema.model_validate(
            {
                "candidate_subagent_type": self.selected_candidate_subagent_type,
                "reason": "prefer the external research bridge for this approval",
                "confidence": 0.93,
                "notes": ["llm_select_discovery_candidate"],
            }
        )


class SubagentDiscoveryCandidatePolicyTests(unittest.TestCase):
    def test_review_request_persists_rule_candidate_recommendation_and_activation_uses_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SubagentDiscoveryStore(Path(temp_dir))
            gap = SubagentCapabilityGap(
                intent="review_thesis",
                stage="report",
                capability="report_signal_extension",
                preferred_subagent_type="candidate_report_signal_extension",
                reason="missing report signal extension capability",
            )
            request = SubagentToolDiscoveryRequest(
                intent="review_thesis",
                stage="report",
                capability="report_signal_extension",
                search_query="review_thesis report report_signal_extension",
                preferred_protocols=["skill", "mcp"],
                reason=gap.reason,
            )
            ticket = store.create_request(gap=gap, request=request)
            host = type(
                "_Host",
                (),
                {
                    "settings": SimpleNamespace(),
                    "orchestrator_error_cls": RuntimeError,
                    "require_subagent_discovery_store": lambda self: store,
                },
            )()
            admin = OrchestratorSubagentDiscoveryAdminSupport(host)

            reviewed = admin.review_request(
                ticket.ticket_id,
                decision="approve",
                reviewer="tester",
                reason="approve multi-candidate discovery ticket",
                candidate_catalog_entries=[
                    SubagentCatalogEntry(
                        subagent_type="candidate_report_signal_extension__skill",
                        summary="Local skill-backed report extension.",
                        capabilities=["report_signal_extension"],
                        stage="report",
                        tags=["approved_discovery_candidate", "skill_registration:skill_runner"],
                        suitable_intents=["review_thesis"],
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
                    ).model_dump(mode="json"),
                    SubagentCatalogEntry(
                        subagent_type="candidate_report_signal_extension__mcp",
                        summary="External MCP-backed report extension.",
                        capabilities=["report_signal_extension"],
                        stage="report",
                        tags=["approved_discovery_candidate", "mcp_source:akshare_mcp"],
                        suitable_intents=["review_thesis"],
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
                    ).model_dump(mode="json"),
                ],
            )

            recommendation = reviewed["result"]["candidate_recommendation"]
            self.assertEqual(
                recommendation["candidate_subagent_type"],
                "candidate_report_signal_extension__skill",
            )
            self.assertEqual(recommendation["source_protocol"], "skill")
            self.assertEqual(recommendation["policy_source"], "rule")

            activated = admin.activate_request(
                ticket.ticket_id,
                delegate_subagent_type="reporter_delegate",
                activated_by="tester",
                reason="activate recommended discovery alias",
            )

            self.assertEqual(
                activated["result"]["activated_entries"][0]["subagent_type"],
                "candidate_report_signal_extension__skill",
            )

    def test_review_request_can_apply_llm_candidate_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SubagentDiscoveryStore(Path(temp_dir))
            gap = SubagentCapabilityGap(
                intent="review_thesis",
                stage="report",
                capability="report_signal_extension",
                preferred_subagent_type="candidate_report_signal_extension",
                reason="missing report signal extension capability",
            )
            request = SubagentToolDiscoveryRequest(
                intent="review_thesis",
                stage="report",
                capability="report_signal_extension",
                search_query="review_thesis report report_signal_extension",
                preferred_protocols=["skill", "mcp"],
                reason=gap.reason,
            )
            ticket = store.create_request(gap=gap, request=request)
            provider = _DiscoveryCandidateProviderStub(
                selected_candidate_subagent_type="candidate_report_signal_extension__mcp"
            )
            host = type(
                "_Host",
                (),
                {
                    "settings": SimpleNamespace(
                        subagent_discovery_shadow_llm_enabled=False,
                        subagent_discovery_shadow_llm_reasoning_effort="low",
                        subagent_discovery_llm_policy_enabled=True,
                        subagent_discovery_llm_policy_min_confidence=0.8,
                        subagent_discovery_hybrid_llm_enabled=False,
                        subagent_discovery_hybrid_llm_min_confidence=0.8,
                    ),
                    "llm_provider": provider,
                    "orchestrator_error_cls": RuntimeError,
                    "require_subagent_discovery_store": lambda self: store,
                },
            )()
            admin = OrchestratorSubagentDiscoveryAdminSupport(host)

            reviewed = admin.review_request(
                ticket.ticket_id,
                decision="approve",
                reviewer="tester",
                reason="approve multi-candidate discovery ticket with llm policy",
                candidate_catalog_entries=[
                    SubagentCatalogEntry(
                        subagent_type="candidate_report_signal_extension__skill",
                        summary="Local skill-backed report extension.",
                        capabilities=["report_signal_extension"],
                        stage="report",
                        tags=["approved_discovery_candidate", "skill_registration:skill_runner"],
                        suitable_intents=["review_thesis"],
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
                    ).model_dump(mode="json"),
                    SubagentCatalogEntry(
                        subagent_type="candidate_report_signal_extension__mcp",
                        summary="External MCP-backed report extension.",
                        capabilities=["report_signal_extension"],
                        stage="report",
                        tags=["approved_discovery_candidate", "mcp_source:akshare_mcp"],
                        suitable_intents=["review_thesis"],
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
                    ).model_dump(mode="json"),
                ],
            )

            self.assertEqual(provider.calls, 1)
            shadow_recommendation = reviewed["result"]["candidate_shadow_recommendation"]
            self.assertEqual(
                shadow_recommendation["candidate_subagent_type"],
                "candidate_report_signal_extension__mcp",
            )
            self.assertEqual(shadow_recommendation["policy_source"], "llm_shadow")
            recommendation = reviewed["result"]["candidate_recommendation"]
            self.assertEqual(
                recommendation["candidate_subagent_type"],
                "candidate_report_signal_extension__mcp",
            )
            self.assertEqual(recommendation["policy_source"], "llm")

            activated = admin.activate_request(
                ticket.ticket_id,
                delegate_subagent_type="reporter_delegate",
                activated_by="tester",
                reason="activate llm-recommended discovery alias",
            )

            self.assertEqual(
                activated["result"]["activated_entries"][0]["subagent_type"],
                "candidate_report_signal_extension__mcp",
            )


if __name__ == "__main__":
    unittest.main()
