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
from research_system.agents._subagent_discovery_policy_controller import (
    SubagentDiscoveryPolicyController,
)
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
from research_system.runtime.orchestration.subagent_runtime_packaging import (
    SubagentRuntimePackagingSupport,
)
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.schemas.subagents import SubagentCapabilityGap


class SubagentDiscoveryPolicyControllerTests(unittest.TestCase):
    def test_policy_controller_can_apply_runtime_preview_with_bound_auto_activation(self) -> None:
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
            policy_controller = SubagentDiscoveryPolicyController(
                decision_source=decision_source,
                stage_policy_source=decision_source.stage_policy_source,
                runtime_packaging=SubagentRuntimePackagingSupport(host),
                error_factory=RuntimeError,
            )
            gap = SubagentCapabilityGap(
                intent="stock_check",
                stage="collect",
                capability="market_signal_extension",
                preferred_subagent_type="candidate_collect_market_snapshot_skill",
                reason="missing market signal extension capability",
            )

            protocol_decision = decision_source.resolve_protocols(gap=gap)
            ticket = store.create_request(gap=gap, request=protocol_decision.request)

            preview_application = policy_controller.apply_runtime_preview(
                ticket=ticket,
                store=store,
            )

            self.assertTrue(preview_application.review_application.persisted)
            self.assertIsNotNone(preview_application.activation_application)
            self.assertTrue(preview_application.activation_application.created)
            self.assertEqual(
                preview_application.preview_resolution.candidate_result.decision.recommendation.candidate_subagent_type,
                "candidate_collect_market_snapshot_skill",
            )
            self.assertEqual(preview_application.preview_resolution.review_decision.decision, "approved")
            self.assertTrue(preview_application.activation_application.activation_resolution.activation_decision.activate)
            self.assertIsNone(preview_application.activation_application.activation_resolution.resolution_error)
            self.assertEqual(
                preview_application.activation_application.activation_resolution.activation_target.candidate_entry.subagent_type,
                "candidate_collect_market_snapshot_skill",
            )
            self.assertEqual(
                preview_application.activation_application.activation_resolution.activation_target.runtime_registration.registration_type,
                "snapshot_skill_runner",
            )
            self.assertEqual(ticket.status, "approved")
            self.assertEqual(len(ticket.activated_entries), 1)

    def test_policy_controller_can_apply_manual_review_and_activation(self) -> None:
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
            policy_controller = SubagentDiscoveryPolicyController(
                decision_source=decision_source,
                stage_policy_source=decision_source.stage_policy_source,
                runtime_packaging=SubagentRuntimePackagingSupport(host),
                error_factory=RuntimeError,
            )
            gap = SubagentCapabilityGap(
                intent="stock_check",
                stage="collect",
                capability="market_signal_extension",
                preferred_subagent_type="candidate_collect_market_snapshot_skill",
                reason="missing market signal extension capability",
            )

            protocol_decision = decision_source.resolve_protocols(gap=gap)
            ticket = store.create_request(gap=gap, request=protocol_decision.request)

            review_application = policy_controller.apply_manual_review(
                ticket=ticket,
                decision="approve",
                reviewer="tester",
                reason="manual approval for controlled alias",
                candidate_catalog_entries=[
                    entry.model_dump(mode="json")
                    for entry in candidate_support.candidate_entries(ticket)
                ],
                store=store,
            )
            self.assertTrue(review_application.persisted)
            self.assertIsNotNone(review_application.review_resolution.candidate_result)
            self.assertEqual(review_application.review_resolution.review_decision.policy_source, "explicit")
            self.assertEqual(ticket.status, "approved")

            activation_application = policy_controller.apply_manual_activation(
                ticket=ticket,
                registration_template_id="snapshot:market:market_snapshot_skill",
                activated_by="tester",
                reason="activate runtime alias from approved ticket",
                store=store,
            )

            self.assertTrue(activation_application.created)
            self.assertIsNotNone(activation_application.activation_resolution.activation_target)
            self.assertEqual(activation_application.activation_resolution.activation_decision.policy_source, "explicit")
            self.assertEqual(
                activation_application.activation_resolution.activation_target.candidate_entry.subagent_type,
                "candidate_collect_market_snapshot_skill",
            )
            self.assertEqual(
                activation_application.activation_resolution.activation_target.runtime_registration.registration_template_id,
                "snapshot:market:market_snapshot_skill",
            )
            self.assertEqual(len(ticket.activated_entries), 1)


if __name__ == "__main__":
    unittest.main()
