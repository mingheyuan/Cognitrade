from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.runtime.execution.core.runtime_transition_decision_source import (
    OrchestratorRuntimeTransitionDecisionSource,
)
from research_system.schemas.planning import ActionPlanPatch, ActionPlanPatchOperation


class RuntimeTransitionDecisionSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = OrchestratorRuntimeTransitionDecisionSource()

    def test_build_action_transition_maps_request_replan(self) -> None:
        decision = self.source.build_action_transition(
            plan=SimpleNamespace(intent="review_thesis", plan_id="plan-1"),
            plan_patch=ActionPlanPatch(
                decision_type="replan",
                operations=[
                    ActionPlanPatchOperation(
                        op_type="request_replan",
                        reason="review capability gap requires action replan",
                    )
                ],
                notes=["request_action_plan_replan"],
                policy_source="rule",
            ),
            source_domain="subagent_capability_gap",
            source_stage="review",
            source_action_node_id="review",
            capability="missing_review_capability",
            reason="subagent_capability_gap_action_replan",
        )

        self.assertEqual(decision.transition_type, "request_action_replan")
        self.assertTrue(decision.request_replan)
        self.assertEqual(decision.source_domain, "subagent_capability_gap")
        self.assertEqual(decision.capability, "missing_review_capability")

    def test_build_subagent_catalog_transition_maps_continue_discovery(self) -> None:
        decision = self.source.build_subagent_catalog_transition(
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
                        policy_source="rule",
                        notes=("runtime_control_subagent_recovered_via_discovery",),
                    ),
                ),
            ),
        )

        self.assertEqual(decision.transition_source, "catalog_selection")
        self.assertEqual(decision.transition_type, "continue_discovery")
        self.assertEqual(decision.selected_subagent_type, "candidate_review_bridge")
        self.assertEqual(decision.discovery_ticket_id, "ticket-1")

    def test_build_subagent_capability_gap_transition_maps_continue_discovery(self) -> None:
        decision = self.source.build_subagent_capability_gap_transition(
            gap=SimpleNamespace(
                intent="review_thesis",
                stage="review",
                capability="missing_review_capability",
            ),
            preferred_subagent_type=None,
            requires_llm=None,
            escalation_decision=SimpleNamespace(
                strategy="discovery",
                policy_source="rule",
                notes=("subagent_control_escalation_rule=default",),
            ),
        )

        self.assertEqual(decision.transition_source, "capability_gap_escalation")
        self.assertEqual(decision.transition_type, "continue_discovery")
        self.assertEqual(decision.capability, "missing_review_capability")

    def test_build_subagent_phase_transition_maps_local_patch(self) -> None:
        decision = self.source.build_subagent_phase_transition(
            plan=SimpleNamespace(intent="review_thesis", phase="review", plan_id="plan-1"),
            dag_id="review_execution",
            resolution=SimpleNamespace(
                final_patch=SimpleNamespace(
                    decision_type="patch",
                    operations=[SimpleNamespace(op_type="insert_after")],
                    policy_source="rule",
                    notes=["add_review_summary_node"],
                ),
                final_patch_rows=[{"patch_id": "patch-review"}],
                rule_variant_id="rule_patched",
                final_plan_variant="rule_patched",
            ),
        )

        self.assertEqual(decision.transition_source, "phase_patch_cycle")
        self.assertEqual(decision.transition_type, "apply_local_patch")
        self.assertFalse(decision.final_request_replan)
        self.assertEqual(decision.patch_count, 1)


if __name__ == "__main__":
    unittest.main()
