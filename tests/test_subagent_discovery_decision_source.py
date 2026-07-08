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


class SubagentDiscoveryDecisionSourceTests(unittest.TestCase):
    def test_decision_source_emits_structured_stage_outputs_for_safe_skill_bridge(self) -> None:
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
            decision_source = SubagentDiscoveryDecisionSource(
                discovery_policy=SubagentDiscoveryPolicyService(settings=settings),
                discovery_llm_policy_source=SubagentDiscoveryLLMPolicySource(
                    shadow_policy=SubagentDiscoveryLLMShadowPolicy(settings=settings),
                    settings=settings,
                ),
                discovery_candidate_policy=SubagentDiscoveryCandidatePolicyService(settings=settings),
                discovery_approval_policy=SubagentDiscoveryApprovalPolicyService(settings=settings),
                discovery_activation_policy=SubagentDiscoveryActivationPolicyService(settings=settings),
            )
            candidate_support = SubagentDiscoveryCandidateSupport(host)
            gap = SubagentCapabilityGap(
                intent="stock_check",
                stage="collect",
                capability="market_signal_extension",
                preferred_subagent_type="candidate_collect_market_snapshot_skill",
                reason="missing market signal extension capability",
            )

            protocol_decision = decision_source.resolve_protocols(gap=gap)
            ticket = store.create_request(gap=gap, request=protocol_decision.request)
            candidate_result = decision_source.select_candidate(
                ticket=ticket,
                candidate_entries=candidate_support.candidate_entries(ticket),
            )
            ticket.candidate_catalog_entries = list(candidate_result.candidate_entries)
            ticket.candidate_recommendation = candidate_result.recommendation
            ticket.candidate_shadow_recommendation = candidate_result.shadow_recommendation
            ticket.notes = list(candidate_result.preview_notes)
            review_decision = decision_source.evaluate_review(ticket=ticket)
            ticket.status = review_decision.decision
            activation_decision = decision_source.evaluate_activation(ticket=ticket)

            self.assertEqual(protocol_decision.policy_source, "rule")
            self.assertEqual(protocol_decision.request.policy_source, "rule")
            self.assertEqual(
                protocol_decision.event_payload()["selected_protocols"],
                ["skill", "mcp", "openapi", "http_json"],
            )
            self.assertEqual(
                candidate_result.decision.recommendation.candidate_subagent_type,
                "candidate_collect_market_snapshot_skill",
            )
            self.assertEqual(candidate_result.decision.policy_source, "rule")
            self.assertEqual(review_decision.decision, "approved")
            self.assertTrue(review_decision.approved)
            self.assertEqual(
                review_decision.selected_candidate_subagent_type,
                "candidate_collect_market_snapshot_skill",
            )
            self.assertTrue(activation_decision.activate)
            self.assertEqual(
                activation_decision.candidate_subagent_type,
                "candidate_collect_market_snapshot_skill",
            )
            self.assertEqual(
                activation_decision.registration_template_id,
                "snapshot:market:market_snapshot_skill",
            )

    def test_decision_source_can_apply_explicit_manual_review_to_ticket(self) -> None:
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
            decision_source = SubagentDiscoveryDecisionSource(
                discovery_policy=SubagentDiscoveryPolicyService(settings=settings),
                discovery_llm_policy_source=SubagentDiscoveryLLMPolicySource(
                    shadow_policy=SubagentDiscoveryLLMShadowPolicy(settings=settings),
                    settings=settings,
                ),
                discovery_candidate_policy=SubagentDiscoveryCandidatePolicyService(settings=settings),
                discovery_approval_policy=SubagentDiscoveryApprovalPolicyService(settings=settings),
                discovery_activation_policy=SubagentDiscoveryActivationPolicyService(settings=settings),
            )
            candidate_support = SubagentDiscoveryCandidateSupport(host)
            gap = SubagentCapabilityGap(
                intent="stock_check",
                stage="collect",
                capability="market_signal_extension",
                preferred_subagent_type="candidate_collect_market_snapshot_skill",
                reason="missing market signal extension capability",
            )

            protocol_decision = decision_source.resolve_protocols(gap=gap)
            ticket = store.create_request(gap=gap, request=protocol_decision.request)
            candidate_result = decision_source.select_candidate(
                ticket=ticket,
                candidate_entries=candidate_support.candidate_entries(ticket),
            )
            decision_source.apply_candidate_selection(
                ticket=ticket,
                candidate_result=candidate_result,
            )
            review_decision = decision_source.build_manual_review(
                ticket=ticket,
                decision="approve",
                reviewer="tester",
                reason="manual approval for controlled alias",
            )
            previous_status = decision_source.apply_review(
                ticket=ticket,
                review_decision=review_decision,
            )

            self.assertEqual(previous_status, "pending")
            self.assertEqual(review_decision.policy_source, "explicit")
            self.assertEqual(ticket.status, "approved")
            self.assertEqual(ticket.reviewer, "tester")
            self.assertEqual(ticket.review_reason, "manual approval for controlled alias")
            self.assertEqual(len(ticket.review_history), 1)
            self.assertEqual(ticket.review_history[0].source, "manual_cli_review")
            self.assertEqual(
                ticket.candidate_recommendation.candidate_subagent_type,
                "candidate_collect_market_snapshot_skill",
            )

    def test_decision_source_can_apply_manual_activation_to_ticket(self) -> None:
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
            decision_source = SubagentDiscoveryDecisionSource(
                discovery_policy=SubagentDiscoveryPolicyService(settings=settings),
                discovery_llm_policy_source=SubagentDiscoveryLLMPolicySource(
                    shadow_policy=SubagentDiscoveryLLMShadowPolicy(settings=settings),
                    settings=settings,
                ),
                discovery_candidate_policy=SubagentDiscoveryCandidatePolicyService(settings=settings),
                discovery_approval_policy=SubagentDiscoveryApprovalPolicyService(settings=settings),
                discovery_activation_policy=SubagentDiscoveryActivationPolicyService(settings=settings),
            )
            candidate_support = SubagentDiscoveryCandidateSupport(host)
            gap = SubagentCapabilityGap(
                intent="stock_check",
                stage="collect",
                capability="market_signal_extension",
                preferred_subagent_type="candidate_collect_market_snapshot_skill",
                reason="missing market signal extension capability",
            )

            protocol_decision = decision_source.resolve_protocols(gap=gap)
            ticket = store.create_request(gap=gap, request=protocol_decision.request)
            candidate_result = decision_source.select_candidate(
                ticket=ticket,
                candidate_entries=candidate_support.candidate_entries(ticket),
            )
            decision_source.apply_candidate_selection(
                ticket=ticket,
                candidate_result=candidate_result,
            )
            review_decision = decision_source.build_manual_review(
                ticket=ticket,
                decision="approve",
                reviewer="tester",
                reason="manual approval for activation",
            )
            decision_source.apply_review(
                ticket=ticket,
                review_decision=review_decision,
            )
            candidate_entry = ticket.candidate_catalog_entries[0]
            activation_decision = decision_source.build_manual_activation(
                ticket=ticket,
                candidate_entry=candidate_entry,
                delegate_subagent_type="collector_delegate",
                runtime_registration=None,
                activated_by="tester",
                reason="activate explicit delegate alias",
            )
            activation_entry, created = decision_source.apply_activation(
                ticket=ticket,
                activation_decision=activation_decision,
                candidate_entry=candidate_entry,
            )
            duplicate_entry, duplicate_created = decision_source.apply_activation(
                ticket=ticket,
                activation_decision=activation_decision,
                candidate_entry=candidate_entry,
            )

            self.assertTrue(created)
            self.assertIsNotNone(activation_entry)
            self.assertEqual(activation_decision.policy_source, "explicit")
            self.assertEqual(
                activation_entry.delegate_subagent_type,
                "collector_delegate",
            )
            self.assertEqual(len(ticket.activated_entries), 1)
            self.assertFalse(duplicate_created)
            self.assertEqual(duplicate_entry.activation_id, activation_entry.activation_id)


if __name__ == "__main__":
    unittest.main()
