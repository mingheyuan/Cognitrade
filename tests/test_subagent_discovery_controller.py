from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from research_system.adapters.vendors.skill_source_registry import SkillSourceRegistry
from research_system.agents._subagent_discovery_controller import SubagentDiscoveryController
from research_system.agents._subagent_discovery_decision_source import SubagentDiscoveryDecisionSource
from research_system.agents._subagent_discovery_policy import SubagentDiscoveryPolicyService
from research_system.agents._subagent_discovery_policy_source import SubagentDiscoveryLLMPolicySource
from research_system.agents._subagent_discovery_shadow_policy import SubagentDiscoveryLLMShadowPolicy
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
from research_system.runtime.orchestration.subagent_discovery_store import SubagentDiscoveryStore
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.schemas.subagents import SubagentCapabilityGap


class SubagentDiscoveryControllerTests(unittest.TestCase):
    def test_resolve_runtime_gap_previews_approves_and_activates_safe_candidate(self) -> None:
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
            runtime_context = SimpleNamespace(
                subagent_discovery_stage_decisions=[],
                dynamic_trace=[],
            )
            support = SimpleNamespace(emit_event=lambda *_args, **_kwargs: None)
            host = type(
                "_Host",
                (),
                {
                    "settings": settings,
                    "llm_provider": None,
                    "emit_event": None,
                    "runtime_binding_getter": lambda self: {
                        "context": runtime_context,
                        "support": support,
                        "request_id": "req-discovery-controller",
                        "intent": "stock_check",
                    },
                    "orchestrator_error_cls": RuntimeError,
                    "require_subagent_discovery_store": lambda self: store,
                    "require_skill_source_registry": lambda self: SkillSourceRegistry(settings=settings),
                    "require_skill_runtime": lambda self: SimpleNamespace(
                        backend=SimpleNamespace(registry={"market_snapshot_skill": object()})
                    ),
                },
            )()
            candidate_support = SubagentDiscoveryCandidateSupport(host)
            controller = SubagentDiscoveryController(
                discovery_candidate_support=candidate_support,
                discovery_decision_source=SubagentDiscoveryDecisionSource(
                    discovery_policy=SubagentDiscoveryPolicyService(settings=settings),
                    discovery_llm_policy_source=SubagentDiscoveryLLMPolicySource(
                        shadow_policy=SubagentDiscoveryLLMShadowPolicy(settings=settings),
                        settings=settings,
                    ),
                    discovery_candidate_policy=SubagentDiscoveryCandidatePolicyService(settings=settings),
                    discovery_approval_policy=SubagentDiscoveryApprovalPolicyService(settings=settings),
                    discovery_activation_policy=SubagentDiscoveryActivationPolicyService(settings=settings),
                    discovery_candidate_support=candidate_support,
                ),
                activation_host=host,
                runtime_binding_getter=host.runtime_binding_getter,
                service_name="collector",
            )

            resolution = controller.resolve_runtime_gap(
                gap=SubagentCapabilityGap(
                    intent="stock_check",
                    stage="collect",
                    capability="market_signal_extension",
                    preferred_subagent_type="candidate_collect_market_snapshot_skill",
                    reason="missing market signal extension capability",
                ),
                store=store,
            )

            self.assertEqual(
                resolution.tool_discovery_request.preferred_protocols,
                ["skill", "mcp", "openapi", "http_json"],
            )
            self.assertEqual(resolution.tool_discovery_request.policy_source, "rule")
            self.assertIsNotNone(resolution.ticket)
            self.assertEqual(resolution.ticket.status, "approved")
            self.assertEqual(
                resolution.ticket.candidate_recommendation.candidate_subagent_type,
                "candidate_collect_market_snapshot_skill",
            )
            self.assertEqual(len(resolution.ticket.activated_entries), 1)
            self.assertEqual(
                resolution.ticket.activated_entries[0].subagent_type,
                "candidate_collect_market_snapshot_skill",
            )
            self.assertEqual(
                [row["event"] for row in runtime_context.subagent_discovery_stage_decisions],
                [
                    "subagent_tool_discovery_protocol_decided",
                    "subagent_tool_discovery_candidate_decided",
                    "subagent_tool_discovery_review_decided",
                    "subagent_tool_discovery_activation_decided",
                ],
            )


if __name__ == "__main__":
    unittest.main()
