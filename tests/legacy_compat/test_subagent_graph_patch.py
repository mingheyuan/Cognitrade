from __future__ import annotations

import unittest

from pydantic import ValidationError

from research_system.schemas.subagents import (
    SubagentGraphPatch,
    SubagentGraphPatchOperation,
    SubagentPlan,
    SubagentPlanNode,
)


class SubagentGraphPatchSchemaTests(unittest.TestCase):
    def test_subagent_plan_accepts_catalog_selected_nodes(self) -> None:
        plan = SubagentPlan(
            intent="stock_check",
            phase="collect",
            strategy="catalog",
            nodes=[
                SubagentPlanNode(
                    node_id="market_snapshot",
                    subagent_type="stock_snapshot_collector",
                    objective="Collect market snapshot.",
                    inputs={"symbol": "600519"},
                ),
                SubagentPlanNode(
                    node_id="dedupe_candidates",
                    subagent_type="evidence_candidate_deduper",
                    depends_on=["market_snapshot"],
                ),
            ],
            catalog_trace={"selection_mode": "llm_catalog"},
        )

        self.assertEqual(plan.strategy, "catalog")
        self.assertEqual([node.node_id for node in plan.nodes], ["market_snapshot", "dedupe_candidates"])

    def test_insert_subagent_patch_requires_node_payload(self) -> None:
        with self.assertRaises(ValidationError):
            SubagentGraphPatchOperation(op_type="insert_subagent")

    def test_replace_subgraph_patch_requires_target_and_replacements(self) -> None:
        with self.assertRaises(ValidationError):
            SubagentGraphPatchOperation(
                op_type="replace_subgraph",
                target_node_id="existing_node",
            )

    def test_graph_patch_supports_controlled_rewire_and_replan(self) -> None:
        patch = SubagentGraphPatch(
            decision_type="replan",
            policy_source="llm",
            confidence=0.72,
            operations=[
                SubagentGraphPatchOperation(
                    op_type="rewire_dependency",
                    target_node_id="retrieval_decider",
                    new_depends_on=["qdrant_evidence_searcher", "methodology_memory_recaller"],
                    reason="Need both evidence sources before final retrieval decision.",
                ),
                SubagentGraphPatchOperation(
                    op_type="request_replan",
                    reason="Current catalog lacks a suitable sector-signal enrichment subagent.",
                ),
            ],
            notes=["capability_gap=sector_signal_enrichment"],
        )

        self.assertEqual(patch.decision_type, "replan")
        self.assertEqual(patch.policy_source, "llm")
        self.assertEqual(len(patch.operations), 2)


if __name__ == "__main__":
    unittest.main()
