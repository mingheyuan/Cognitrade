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
    def test_retriever_phase_one_replan_patch_tracks_selected_branches(self) -> None:
        patch = RetrieverPhasePolicy().build_phase_one_replan_patch(
            include_qdrant=False,
            include_methodology_memory=True,
            indexed_evidence_count=3,
        )

        self.assertEqual(str(patch.decision_type), "replan")
        self.assertEqual(str(patch.operations[0].op_type), "request_replan")
        self.assertEqual(str((patch.operations[0].metadata or {}).get("include_qdrant") or ""), "false")
        self.assertEqual(str((patch.operations[0].metadata or {}).get("include_methodology_memory") or ""), "true")
        self.assertEqual(str((patch.operations[0].metadata or {}).get("indexed_evidence_count") or ""), "3")
        self.assertEqual(
            list(patch.notes or []),
            [
                "replan_retrieval_phase_one_from_indexing",
                "skip_qdrant_branch",
                "include_methodology_memory_branch",
            ],
        )

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

    def test_retriever_phase_two_replan_patch_tracks_decider_selection(self) -> None:
        patch = RetrieverPhasePolicy().build_phase_two_replan_patch(
            decision_subagent_type="fallback_retrieval_decider",
            evidence_count=0,
            allow_llm_decision=False,
        )

        self.assertEqual(str(patch.decision_type), "replan")
        self.assertEqual(str(patch.operations[0].op_type), "request_replan")
        self.assertEqual(
            str((patch.operations[0].metadata or {}).get("decision_subagent_type") or ""),
            "fallback_retrieval_decider",
        )
        self.assertEqual(str((patch.operations[0].metadata or {}).get("evidence_count") or ""), "0")
        self.assertEqual(str((patch.operations[0].metadata or {}).get("allow_llm_decision") or ""), "false")
        self.assertEqual(
            list(patch.notes or []),
            [
                "replan_retrieval_phase_two_from_deduper",
                "decision_subagent_type=fallback_retrieval_decider",
                "empty_evidence_candidates_after_dedup",
                "llm_decision_disabled_by_runtime_policy",
            ],
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

    def test_distiller_chunk_replan_patch_tracks_selected_execution_branch(self) -> None:
        patch = DistillerPhasePolicy().build_chunk_execution_replan_patch(
            chunk_subagent_type="fallback_chunk_distiller",
            chunk_count=2,
            force_fallback=True,
            runtime_degraded_reason="turn_llm_budget_exhausted",
        )

        self.assertEqual(str(patch.decision_type), "replan")
        self.assertEqual(len(list(patch.operations or [])), 1)
        self.assertEqual(str(patch.operations[0].op_type), "request_replan")
        self.assertEqual(
            str((patch.operations[0].metadata or {}).get("chunk_subagent_type") or ""),
            "fallback_chunk_distiller",
        )
        self.assertEqual(str((patch.operations[0].metadata or {}).get("chunk_count") or ""), "2")
        self.assertEqual(str((patch.operations[0].metadata or {}).get("force_fallback") or ""), "true")
        self.assertEqual(
            str((patch.operations[0].metadata or {}).get("runtime_degraded_reason") or ""),
            "turn_llm_budget_exhausted",
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
