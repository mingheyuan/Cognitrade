from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.runtime.orchestration._subagent_discovery_activation_policy import (
    SubagentDiscoveryActivationPolicyService,
)
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.schemas.subagents import (
    SubagentCapabilityGap,
    SubagentCatalogEntry,
    SubagentDiscoveryCandidateRecommendation,
    SubagentDiscoveryTicket,
    SubagentToolDiscoveryRequest,
)


class SubagentDiscoveryActivationPolicyTests(unittest.TestCase):
    def test_policy_auto_activates_safe_single_candidate_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            settings.subagent_discovery_activation_policy_path.write_text(
                "\n".join(
                    [
                        "defaults:",
                        "  auto_activate: false",
                        "  require_approved_ticket: true",
                        "  require_recommended_candidate: true",
                        "  require_single_runtime_registration_template: true",
                        "  max_candidate_count: 1",
                        "  allowed_side_effect_levels: [none, read_only]",
                        "rules:",
                        "  - rule_id: collect_skill_auto_activate",
                        "    stage: collect",
                        "    auto_activate: true",
                        "    allowed_protocols: [skill]",
                        "    notes: [auto_activate_safe_collect_skill_preview]",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            service = SubagentDiscoveryActivationPolicyService(settings=settings)
            ticket = SubagentDiscoveryTicket(
                ticket_id="01DISC",
                status="approved",
                capability_gap=SubagentCapabilityGap(
                    intent="stock_check",
                    stage="collect",
                    capability="market_signal_extension",
                    preferred_subagent_type="candidate_collect_market_snapshot_skill",
                    reason="missing market signal extension capability",
                ),
                tool_discovery_request=SubagentToolDiscoveryRequest(
                    intent="stock_check",
                    stage="collect",
                    capability="market_signal_extension",
                    search_query="stock_check collect market_signal_extension",
                    preferred_protocols=["skill", "mcp"],
                    reason="missing market signal extension capability",
                ),
                candidate_catalog_entries=[
                    SubagentCatalogEntry(
                        subagent_type="candidate_collect_market_snapshot_skill",
                        summary="Collect market snapshot through local runtime.",
                        capabilities=["market_signal_extension"],
                        stage="collect",
                        side_effect_level="read_only",
                        tags=["approved_discovery_candidate", "skill_registration:snapshot_skill_runner"],
                        suitable_intents=["stock_check"],
                        runtime_registration_templates=[
                            {
                                "template_id": "snapshot:market:market_snapshot_skill",
                                "registration_type": "snapshot_skill_runner",
                            }
                        ],
                    )
                ],
                candidate_recommendation=SubagentDiscoveryCandidateRecommendation(
                    candidate_subagent_type="candidate_collect_market_snapshot_skill",
                    source_protocol="skill",
                    reason="preferred protocol skill",
                    policy_source="rule",
                    confidence=1.0,
                ),
            )

            decision = service.evaluate(ticket=ticket)

            self.assertTrue(decision["activate"])
            self.assertEqual(
                decision["candidate_subagent_type"],
                "candidate_collect_market_snapshot_skill",
            )
            self.assertEqual(
                decision["registration_template_id"],
                "snapshot:market:market_snapshot_skill",
            )
            self.assertIn("collect_skill_auto_activate", str(decision["reason"]))

    def test_policy_requires_approved_ticket_before_activation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            settings.subagent_discovery_activation_policy_path.write_text(
                "\n".join(
                    [
                        "defaults:",
                        "  auto_activate: true",
                        "  require_approved_ticket: true",
                        "  require_recommended_candidate: true",
                        "  require_single_runtime_registration_template: true",
                        "  max_candidate_count: 1",
                        "  allowed_side_effect_levels: [none, read_only]",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            service = SubagentDiscoveryActivationPolicyService(settings=settings)
            ticket = SubagentDiscoveryTicket(
                ticket_id="01DISC",
                status="pending",
                capability_gap=SubagentCapabilityGap(
                    intent="review_thesis",
                    stage="review",
                    capability="review_signal_extension",
                    preferred_subagent_type="candidate_review_extension",
                    reason="missing review extension capability",
                ),
                tool_discovery_request=SubagentToolDiscoveryRequest(
                    intent="review_thesis",
                    stage="review",
                    capability="review_signal_extension",
                    search_query="review_thesis review review_signal_extension",
                    preferred_protocols=["mcp", "openapi"],
                    reason="missing review extension capability",
                ),
                candidate_catalog_entries=[
                    SubagentCatalogEntry(
                        subagent_type="candidate_review_extension",
                        capabilities=["review_signal_extension"],
                        stage="review",
                        side_effect_level="read_only",
                        tags=["approved_discovery_candidate", "mcp_source:akshare_mcp"],
                        suitable_intents=["review_thesis"],
                        runtime_registration_templates=[
                            {
                                "template_id": "mcp:akshare_mcp:review_lookup",
                                "registration_type": "mcp_tool_runner",
                            }
                        ],
                    )
                ],
                candidate_recommendation=SubagentDiscoveryCandidateRecommendation(
                    candidate_subagent_type="candidate_review_extension",
                    source_protocol="mcp",
                    reason="prefer mcp",
                    policy_source="rule",
                    confidence=1.0,
                ),
            )

            decision = service.evaluate(ticket=ticket)

            self.assertFalse(decision["activate"])
            self.assertIn("requires_approved_ticket", str(decision["reason"]))


if __name__ == "__main__":
    unittest.main()
