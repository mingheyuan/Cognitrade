from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.agents._subagent_discovery_policy import SubagentDiscoveryPolicyService
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout


class SubagentDiscoveryPolicyTests(unittest.TestCase):
    def test_service_loads_rule_scoped_protocol_order_from_workspace_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            settings.subagent_discovery_policy_path.write_text(
                "\n".join(
                    [
                        "defaults:",
                        "  preferred_protocols: [mcp, openapi, skill, http_json]",
                        "rules:",
                        "  - rule_id: collect_skill_first",
                        "    stage: collect",
                        "    preferred_protocols: [skill, mcp, openapi, http_json]",
                        "    notes: [prefer_local_skill_bridges_for_collect_stage]",
                        "  - rule_id: review_mcp_first",
                        "    stage: review",
                        "    capability: missing_capability",
                        "    preferred_protocols: [mcp, openapi, skill]",
                        "    notes: [prefer_external_research_tools_for_review_stage]",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            service = SubagentDiscoveryPolicyService(settings=settings)

            collect_protocols, collect_notes = service.resolve(
                intent="stock_check",
                stage="collect",
                capability="market_signal_extension",
            )
            review_protocols, review_notes = service.resolve(
                intent="review_thesis",
                stage="review",
                capability="missing_capability",
            )

            self.assertEqual(collect_protocols, ["skill", "mcp", "openapi", "http_json"])
            self.assertIn("discovery_policy_rule=collect_skill_first", collect_notes)
            self.assertEqual(review_protocols, ["mcp", "openapi", "skill"])
            self.assertIn("discovery_policy_rule=review_mcp_first", review_notes)


if __name__ == "__main__":
    unittest.main()
