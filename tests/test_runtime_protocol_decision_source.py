from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.runtime.execution.core.runtime_protocol_decision_source import (
    OrchestratorRuntimeProtocolDecisionSource,
)
from research_system.schemas.planning import ActionPlanPatch, ActionPlanPatchOperation


class RuntimeProtocolDecisionSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = OrchestratorRuntimeProtocolDecisionSource()

    def test_build_action_supervisor_protocol_maps_request_action_replan(self) -> None:
        decision = self.source.build_action_supervisor_protocol(
            plan=SimpleNamespace(intent="review_thesis", plan_id="plan-1"),
            decision=SimpleNamespace(
                decision_type="replan",
                next_actions=[],
                notes=["need fresh review path"],
                plan_patch=ActionPlanPatch(
                    decision_type="replan",
                    operations=[
                        ActionPlanPatchOperation(
                            op_type="request_replan",
                            reason="review path should be inserted before retrieval",
                        )
                    ],
                    policy_source="llm",
                ),
            ),
            ready_actions=[SimpleNamespace(node_id="review")],
            completed_node_ids=["collect"],
            skipped_node_ids=[],
        )

        self.assertEqual(decision.resolution_source, "supervisor_cycle")
        self.assertEqual(decision.transition_type, "request_action_replan")
        self.assertEqual(decision.policy_source, "llm")
        self.assertEqual(decision.ready_action_node_ids, ["review"])

    def test_build_action_subagent_escalation_protocol_maps_capability(self) -> None:
        decision = self.source.build_action_subagent_escalation_protocol(
            plan=SimpleNamespace(intent="review_thesis", plan_id="plan-1"),
            action=SimpleNamespace(node_id="review"),
            escalation_error=SimpleNamespace(
                gap=SimpleNamespace(capability="missing_review_capability"),
                escalation_decision=SimpleNamespace(notes=("skip_optional_review_phase",)),
                plan_patch=ActionPlanPatch(
                    decision_type="replan",
                    operations=[
                        ActionPlanPatchOperation(
                            op_type="request_replan",
                            reason="review capability gap requires action replan",
                        )
                    ],
                    policy_source="rule",
                ),
            ),
            completed_node_ids=["collect"],
            skipped_node_ids=[],
        )

        self.assertEqual(decision.resolution_source, "subagent_escalation")
        self.assertEqual(decision.transition_type, "request_action_replan")
        self.assertEqual(decision.trigger_action_node_id, "review")
        self.assertEqual(decision.capability, "missing_review_capability")

    def test_build_subagent_catalog_protocol_maps_discovery_recovery(self) -> None:
        decision = self.source.build_subagent_catalog_protocol(
            stage="review",
            intent="review_thesis",
            capability="review_strategy",
            preferred_subagent_type=None,
            requires_llm=None,
            resolution=SimpleNamespace(
                selected_entry=SimpleNamespace(subagent_type="candidate_review_bridge"),
                capability_gap_resolution=SimpleNamespace(
                    ticket=SimpleNamespace(ticket_id="ticket-1"),
                    escalation_decision=SimpleNamespace(
                        strategy="discovery",
                        policy_source="hybrid",
                        notes=("preview_candidate_bridge",),
                    ),
                ),
            ),
        )

        self.assertEqual(decision.resolution_source, "catalog_selection")
        self.assertEqual(decision.decision_type, "selected")
        self.assertEqual(decision.selected_subagent_type, "candidate_review_bridge")
        self.assertEqual(decision.discovery_ticket_id, "ticket-1")
        self.assertEqual(decision.policy_source, "hybrid")
        self.assertIn("runtime_control_subagent_recovered_via_discovery", decision.notes)

    def test_build_subagent_capability_gap_protocol_maps_request_action_replan(self) -> None:
        decision = self.source.build_subagent_capability_gap_protocol(
            gap=SimpleNamespace(
                intent="review_thesis",
                stage="review",
                capability="missing_review_capability",
            ),
            preferred_subagent_type=None,
            requires_llm=None,
            escalation_decision=SimpleNamespace(
                strategy="action_replan",
                policy_source="rule",
                notes=("skip_optional_review_phase",),
            ),
        )

        self.assertEqual(decision.resolution_source, "capability_gap_escalation")
        self.assertEqual(decision.decision_type, "request_action_replan")
        self.assertEqual(decision.capability, "missing_review_capability")

    def test_build_subagent_phase_protocol_maps_override_rule_variant(self) -> None:
        decision = self.source.build_subagent_phase_protocol(
            plan=SimpleNamespace(intent="review_thesis", phase="review", plan_id="plan-1", strategy="catalog"),
            dag_id="review_execution",
            resolution=SimpleNamespace(
                final_patch=SimpleNamespace(
                    decision_type="patch",
                    operations=[SimpleNamespace(op_type="insert_after")],
                    policy_source="hybrid",
                    notes=["add_review_summary_node"],
                ),
                final_patch_rows=[{"patch_id": "patch-review"}],
                rule_variant_id="summary_only",
                final_plan_variant="full_parallel",
            ),
        )

        self.assertEqual(decision.resolution_source, "phase_patch_cycle")
        self.assertEqual(decision.decision_type, "override_rule_variant")
        self.assertEqual(decision.rule_plan_variant, "summary_only")
        self.assertEqual(decision.final_plan_variant, "full_parallel")
        self.assertFalse(decision.final_request_replan)
        self.assertEqual(decision.policy_source, "hybrid")


if __name__ == "__main__":
    unittest.main()
