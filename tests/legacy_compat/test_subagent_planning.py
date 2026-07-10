from __future__ import annotations

import unittest

from research_system.schemas.subagents import (
    SubagentGraphPatch,
    SubagentGraphPatchOperation,
    SubagentPlan,
    SubagentPlanNode,
)
from research_system.subagents.planning import apply_subagent_graph_patch


class SubagentPlanningTests(unittest.TestCase):
    def test_apply_graph_patch_removes_optional_node_and_rewires_dependencies(self) -> None:
        plan = SubagentPlan(
            intent="stock_screen",
            phase="retrieve",
            nodes=[
                SubagentPlanNode(node_id="index", subagent_type="blackboard_evidence_indexer"),
                SubagentPlanNode(node_id="qdrant", subagent_type="qdrant_evidence_searcher", depends_on=["index"]),
                SubagentPlanNode(node_id="memory", subagent_type="methodology_memory_recaller", depends_on=["index"]),
                SubagentPlanNode(node_id="decision", subagent_type="retrieval_decider", depends_on=["qdrant", "memory"]),
            ],
        )
        patch = SubagentGraphPatch(
            operations=[
                SubagentGraphPatchOperation(op_type="remove_node", target_node_id="qdrant", reason="skip qdrant"),
                SubagentGraphPatchOperation(
                    op_type="rewire_dependency",
                    target_node_id="decision",
                    remove_depends_on=["qdrant"],
                    new_depends_on=["index"],
                    reason="fallback to blackboard-only decision context",
                ),
            ],
            notes=["phase_one_pruned"],
        )

        patched = apply_subagent_graph_patch(plan, patch)

        node_ids = [node.node_id for node in patched.nodes]
        self.assertEqual(node_ids, ["index", "memory", "decision"])
        decision_node = next(node for node in patched.nodes if node.node_id == "decision")
        self.assertEqual(decision_node.depends_on, ["memory", "index"])
        self.assertIn("phase_one_pruned", patched.notes)
        self.assertTrue((patched.catalog_trace or {}).get("applied_patch_ids"))

    def test_apply_graph_patch_replaces_subgraph_and_preserves_downstream_dependencies(self) -> None:
        plan = SubagentPlan(
            intent="stock_check",
            phase="retrieve",
            nodes=[
                SubagentPlanNode(node_id="dedupe", subagent_type="evidence_candidate_deduper"),
                SubagentPlanNode(node_id="decide", subagent_type="retrieval_decider", depends_on=["dedupe"]),
                SubagentPlanNode(node_id="report", subagent_type="report_bridge", depends_on=["decide"]),
            ],
        )
        patch = SubagentGraphPatch(
            operations=[
                SubagentGraphPatchOperation(
                    op_type="replace_subgraph",
                    target_node_id="decide",
                    replacement_nodes=[
                        SubagentPlanNode(
                            node_id="fallback_prepare",
                            subagent_type="fallback_prepare",
                            depends_on=["dedupe"],
                        ),
                        SubagentPlanNode(
                            node_id="decide",
                            subagent_type="fallback_retrieval_decider",
                            depends_on=["fallback_prepare"],
                        ),
                    ],
                    reason="replace llm decision with fallback chain",
                )
            ]
        )

        patched = apply_subagent_graph_patch(plan, patch)

        node_ids = [node.node_id for node in patched.nodes]
        self.assertEqual(node_ids, ["dedupe", "fallback_prepare", "decide", "report"])
        report_node = next(node for node in patched.nodes if node.node_id == "report")
        self.assertEqual(report_node.depends_on, ["decide"])
        decide_node = next(node for node in patched.nodes if node.node_id == "decide")
        self.assertEqual(decide_node.subagent_type, "fallback_retrieval_decider")


if __name__ == "__main__":
    unittest.main()
