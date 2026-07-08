from __future__ import annotations

import unittest

from research_system.agents._subagent_phase_policy import (
    DistillerPhasePolicy,
    ReporterPhasePolicy,
    ReviewerPhasePolicy,
    RetrieverPhasePolicy,
    ScreenReviewerPhasePolicy,
)
from research_system.schemas.subagents import SubagentPlanNode


def _node(*, node_id: str, subagent_type: str) -> SubagentPlanNode:
    return SubagentPlanNode(
        node_id=node_id,
        subagent_type=subagent_type,
        objective=f"run {subagent_type}",
        inputs={},
        depends_on=[],
        tags=["test"],
        max_attempts=1,
        replayable=True,
        cache_scope="run",
        cache_key=node_id,
        notes=[],
    )


class SubagentPhasePolicyTests(unittest.TestCase):
    def test_retriever_phase_one_patch_removes_expected_nodes(self) -> None:
        patch = RetrieverPhasePolicy().build_phase_one_patch(
            should_skip_qdrant=True,
            should_recall_methodology_memory=False,
        )

        self.assertIsNotNone(patch)
        self.assertEqual(
            [op.target_node_id for op in list(patch.operations or [])],
            ["qdrant_evidence_searcher", "methodology_memory_recaller"],
        )
        self.assertEqual(
            list(patch.notes or []),
            ["removed_qdrant_for_stock_screen", "removed_methodology_memory_for_intent"],
        )

    def test_reporter_terminal_patch_replaces_terminal_node(self) -> None:
        patch = ReporterPhasePolicy().build_terminal_patch(
            branch_mode="fallback",
            replacement_node=_node(node_id="report_terminal", subagent_type="fallback_report_composer"),
        )

        self.assertIsNotNone(patch)
        self.assertEqual(str(patch.operations[0].op_type), "replace_subgraph")
        self.assertEqual(str(patch.operations[0].target_node_id), "report_terminal")
        self.assertEqual(
            str(patch.operations[0].replacement_nodes[0].subagent_type),
            "fallback_report_composer",
        )

    def test_distiller_chunk_patch_replaces_each_chunk_node(self) -> None:
        patch = DistillerPhasePolicy().build_chunk_execution_patch(
            force_fallback=True,
            replacement_nodes=[
                ("chunk_1", _node(node_id="chunk_1", subagent_type="fallback_chunk_distiller")),
                ("chunk_2", _node(node_id="chunk_2", subagent_type="fallback_chunk_distiller")),
            ],
        )

        self.assertIsNotNone(patch)
        self.assertEqual(len(list(patch.operations or [])), 2)
        self.assertEqual(
            [str(op.target_node_id) for op in list(patch.operations or [])],
            ["chunk_1", "chunk_2"],
        )

    def test_reviewer_patch_inserts_candidate_and_summary_nodes(self) -> None:
        patch = ReviewerPhasePolicy().build_review_patch(
            candidate_nodes=[
                _node(node_id="review_candidate_1", subagent_type="review_candidate_verdict"),
                _node(node_id="review_candidate_2", subagent_type="review_candidate_verdict"),
            ],
            summary_node=_node(node_id="review_summary_builder", subagent_type="review_summary_builder"),
        )

        self.assertIsNotNone(patch)
        self.assertEqual(len(list(patch.operations or [])), 3)
        self.assertEqual(
            [str(op.node.node_id) for op in list(patch.operations or []) if op.node is not None],
            ["review_candidate_1", "review_candidate_2", "review_summary_builder"],
        )
        self.assertEqual(
            list(patch.notes or []),
            ["insert_review_summary_builder", "insert_review_candidate_nodes"],
        )

    def test_screen_reviewer_replan_patch_tracks_candidate_count(self) -> None:
        patch = ScreenReviewerPhasePolicy().build_screen_review_replan_patch(
            candidate_count=0,
        )

        self.assertIsNotNone(patch)
        self.assertEqual(str(patch.decision_type), "replan")
        self.assertEqual(len(list(patch.operations or [])), 1)
        self.assertEqual(str(patch.operations[0].op_type), "request_replan")
        self.assertEqual(str((patch.operations[0].metadata or {}).get("candidate_count") or ""), "0")
        self.assertEqual(
            list(patch.notes or []),
            ["replan_screen_review_graph_from_strategy", "no_screen_review_candidate_nodes"],
        )


if __name__ == "__main__":
    unittest.main()
