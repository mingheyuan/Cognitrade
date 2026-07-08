from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from research_system.adapters.vendors.skill_source_registry import SkillSourceRegistry
from research_system.agents._subagent_discovery_decision_source import (
    SubagentDiscoveryDecisionSource,
)
from research_system.agents._subagent_discovery_policy import SubagentDiscoveryPolicyService
from research_system.agents._subagent_discovery_policy_source import (
    SubagentDiscoveryLLMPolicySource,
)
from research_system.agents._subagent_discovery_shadow_policy import (
    SubagentDiscoveryLLMShadowPolicy,
)
from research_system.runtime.orchestration._subagent_discovery_activation_policy import (
    SubagentDiscoveryActivationPolicyService,
)
from research_system.runtime.orchestration._subagent_discovery_approval_policy import (
    SubagentDiscoveryApprovalPolicyService,
)
from research_system.runtime.orchestration._subagent_discovery_candidate_policy import (
    SubagentDiscoveryCandidatePolicyService,
)
from research_system.runtime.orchestration.subagent_discovery_candidate_sources import (
    SubagentDiscoveryCandidateSupport,
)
from research_system.runtime.orchestration.subagent_discovery_store import (
    SubagentDiscoveryStore,
)
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.schemas.subagents import SubagentCapabilityGap


class SubagentDiscoveryStagePolicySourceTests(unittest.TestCase):
    def test_stage_policy_source_resolves_safe_skill_bridge_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings.from_env(root)
            ensure_workspace_layout(settings)
            settings.skill_sources_path.write_text(
                "\n".join(
                    [
                        "sources:",
                        "  - skill_name: market_snapshot_skill",
                        "    registration_type: snapshot_skill_runner",
                        "    summary: Collect market snapshot through local runtime.",
                        "    snapshot_type: market",
                        "    capabilities: [market_signal_extension]",
                        "    suitable_intents: [stock_check]",
                        "    stage: collect",
                        "    candidate_subagent_type: candidate_collect_market_snapshot_skill",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            store = SubagentDiscoveryStore(root / "discovery")
            host = type(
                "_Host",
                (),
                {
                    "settings": settings,
                    "llm_provider": None,
                    "emit_event": None,
                    "runtime_binding_getter": lambda self: {},
                    "orchestrator_error_cls": RuntimeError,
                    "require_subagent_discovery_store": lambda self: store,
                    "require_skill_source_registry": lambda self: SkillSourceRegistry(settings=settings),
                    "require_skill_runtime": lambda self: SimpleNamespace(
                        backend=SimpleNamespace(registry={"market_snapshot_skill": object()})
                    ),
                },
            )()
            candidate_support = SubagentDiscoveryCandidateSupport(host)
            decision_source = SubagentDiscoveryDecisionSource(
                discovery_policy=SubagentDiscoveryPolicyService(settings=settings),
                discovery_llm_policy_source=SubagentDiscoveryLLMPolicySource(
                    shadow_policy=SubagentDiscoveryLLMShadowPolicy(settings=settings),
                    settings=settings,
                ),
                discovery_candidate_policy=SubagentDiscoveryCandidatePolicyService(settings=settings),
                discovery_approval_policy=SubagentDiscoveryApprovalPolicyService(settings=settings),
                discovery_activation_policy=SubagentDiscoveryActivationPolicyService(settings=settings),
                discovery_candidate_support=candidate_support,
            )
            stage_policy_source = decision_source.stage_policy_source
            gap = SubagentCapabilityGap(
                intent="stock_check",
                stage="collect",
                capability="market_signal_extension",
                preferred_subagent_type="candidate_collect_market_snapshot_skill",
                reason="missing market signal extension capability",
            )

            protocol_decision = stage_policy_source.resolve_protocols(gap=gap)
            ticket = store.create_request(gap=gap, request=protocol_decision.request)
            candidate_result = stage_policy_source.select_candidate(ticket=ticket)
            decision_source.apply_candidate_selection(ticket=ticket, candidate_result=candidate_result)
            review_decision = stage_policy_source.evaluate_review(ticket=ticket)
            decision_source.apply_review(ticket=ticket, review_decision=review_decision)
            activation_decision = stage_policy_source.evaluate_activation(ticket=ticket)

            self.assertEqual(protocol_decision.policy_source, "rule")
            self.assertEqual(
                protocol_decision.selected_protocols,
                ["skill", "mcp", "openapi", "http_json"],
            )
            self.assertEqual(
                candidate_result.decision.recommendation.candidate_subagent_type,
                "candidate_collect_market_snapshot_skill",
            )
            self.assertEqual(review_decision.decision, "approved")
            self.assertTrue(activation_decision.activate)
            self.assertEqual(
                activation_decision.registration_template_id,
                "snapshot:market:market_snapshot_skill",
            )


if __name__ == "__main__":
    unittest.main()
