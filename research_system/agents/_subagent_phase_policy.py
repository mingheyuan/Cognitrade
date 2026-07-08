from __future__ import annotations

from research_system.schemas.subagents import (
    SubagentGraphPatch,
    SubagentGraphPatchOperation,
    SubagentPlanNode,
)


class SubagentPhaseRulePolicy:
    @staticmethod
    def build_patch(
        *,
        notes: list[str],
        operations: list[SubagentGraphPatchOperation],
        confidence: float = 1.0,
        policy_source: str = "rule",
    ) -> SubagentGraphPatch | None:
        if not operations:
            return None
        return SubagentGraphPatch(
            policy_source=policy_source,
            confidence=confidence,
            notes=list(notes),
            operations=list(operations),
        )

    @staticmethod
    def remove_node(*, target_node_id: str, reason: str) -> SubagentGraphPatchOperation:
        return SubagentGraphPatchOperation(
            op_type="remove_node",
            target_node_id=target_node_id,
            reason=reason,
        )

    @staticmethod
    def replace_subgraph(
        *,
        target_node_id: str,
        replacement_nodes: list[SubagentPlanNode],
        reason: str,
    ) -> SubagentGraphPatchOperation:
        return SubagentGraphPatchOperation(
            op_type="replace_subgraph",
            target_node_id=target_node_id,
            replacement_nodes=list(replacement_nodes),
            reason=reason,
        )

    @staticmethod
    def insert_subagent(*, node: SubagentPlanNode, reason: str) -> SubagentGraphPatchOperation:
        return SubagentGraphPatchOperation(
            op_type="insert_subagent",
            node=node,
            reason=reason,
        )

    @staticmethod
    def request_replan(*, reason: str, metadata: dict[str, str] | None = None) -> SubagentGraphPatchOperation:
        return SubagentGraphPatchOperation(
            op_type="request_replan",
            reason=reason,
            metadata={str(key): str(value) for key, value in dict(metadata or {}).items()},
        )

    def build_replan_patch(
        self,
        *,
        reason: str,
        notes: list[str] | None = None,
        metadata: dict[str, str] | None = None,
        confidence: float = 1.0,
        policy_source: str = "rule",
    ) -> SubagentGraphPatch:
        patch = self.build_patch(
            notes=list(notes or [reason]),
            operations=[self.request_replan(reason=reason, metadata=metadata)],
            confidence=confidence,
            policy_source=policy_source,
        )
        assert patch is not None
        patch.decision_type = "replan"
        patch.termination_reason = reason
        return patch


class RetrieverPhasePolicy(SubagentPhaseRulePolicy):
    def build_phase_one_patch(
        self,
        *,
        should_skip_qdrant: bool,
        should_recall_methodology_memory: bool,
    ) -> SubagentGraphPatch | None:
        operations: list[SubagentGraphPatchOperation] = []
        notes: list[str] = []
        if should_skip_qdrant:
            operations.append(
                self.remove_node(
                    target_node_id="qdrant_evidence_searcher",
                    reason="skip qdrant retrieval for stock_screen",
                )
            )
            notes.append("removed_qdrant_for_stock_screen")
        if not should_recall_methodology_memory:
            operations.append(
                self.remove_node(
                    target_node_id="methodology_memory_recaller",
                    reason="methodology memory not required for current intent",
                )
            )
            notes.append("removed_methodology_memory_for_intent")
        return self.build_patch(notes=notes, operations=operations)

    def build_phase_two_patch(
        self,
        *,
        allow_llm_decision: bool,
        fallback_node: SubagentPlanNode | None,
    ) -> SubagentGraphPatch | None:
        if allow_llm_decision or fallback_node is None:
            return None
        return self.build_patch(
            notes=["replace_llm_decider_with_fallback"],
            operations=[
                self.replace_subgraph(
                    target_node_id="retrieval_decider",
                    replacement_nodes=[fallback_node],
                    reason="llm decision disabled by runtime policy",
                )
            ],
        )


class ReporterPhasePolicy(SubagentPhaseRulePolicy):
    def build_report_replan_patch(
        self,
        *,
        branch_mode: str,
    ) -> SubagentGraphPatch:
        return self.build_replan_patch(
            reason=f"report_terminal_branch={branch_mode}",
            notes=[
                "replan_report_terminal_from_strategy",
                f"report_branch={branch_mode}",
            ],
            metadata={"branch_mode": branch_mode},
        )

    def build_terminal_patch(
        self,
        *,
        branch_mode: str,
        replacement_node: SubagentPlanNode | None,
    ) -> SubagentGraphPatch | None:
        if replacement_node is None:
            return None
        if branch_mode == "deterministic_bank_compare":
            return self.build_patch(
                notes=["replace_terminal_with_deterministic_bank_compare"],
                operations=[
                    self.replace_subgraph(
                        target_node_id="report_terminal",
                        replacement_nodes=[replacement_node],
                        reason="deterministic bank compare branch selected",
                    )
                ],
            )
        if branch_mode == "fallback":
            return self.build_patch(
                notes=["replace_terminal_with_fallback_report"],
                operations=[
                    self.replace_subgraph(
                        target_node_id="report_terminal",
                        replacement_nodes=[replacement_node],
                        reason="fallback reporting branch selected",
                    )
                ],
            )
        return None


class DistillerPhasePolicy(SubagentPhaseRulePolicy):
    def build_chunk_execution_patch(
        self,
        *,
        force_fallback: bool,
        replacement_nodes: list[tuple[str, SubagentPlanNode]],
    ) -> SubagentGraphPatch | None:
        if not force_fallback or not replacement_nodes:
            return None
        operations = [
            self.replace_subgraph(
                target_node_id=target_node_id,
                replacement_nodes=[replacement_node],
                reason="llm chunk distillation disabled by runtime policy",
            )
            for target_node_id, replacement_node in replacement_nodes
        ]
        return self.build_patch(
            notes=["replace_llm_chunk_distillers_with_fallback"],
            operations=operations,
        )


class ReviewerPhasePolicy(SubagentPhaseRulePolicy):
    def build_review_replan_patch(
        self,
        *,
        candidate_count: int,
        review_required: bool,
        sector_snapshot_available: bool,
    ) -> SubagentGraphPatch:
        notes = ["replan_review_graph_from_preprocess"]
        if candidate_count <= 0:
            notes.append("no_reviewable_claims_detected")
        if not sector_snapshot_available:
            notes.append("missing_sector_snapshot")
        if review_required:
            notes.append("review_required_after_focus_validation")
        return self.build_replan_patch(
            reason="review_preprocess_completed",
            notes=notes,
            metadata={
                "candidate_count": str(candidate_count),
                "review_required": str(bool(review_required)).lower(),
                "sector_snapshot_available": str(bool(sector_snapshot_available)).lower(),
            },
        )

    def build_review_patch(
        self,
        *,
        candidate_nodes: list[SubagentPlanNode],
        summary_node: SubagentPlanNode,
    ) -> SubagentGraphPatch | None:
        operations = [
            self.insert_subagent(
                node=node,
                reason="insert candidate-level posterior review node",
            )
            for node in candidate_nodes
        ]
        operations.append(
            self.insert_subagent(
                node=summary_node,
                reason="insert terminal review summary builder",
            )
        )
        notes = ["insert_review_summary_builder"]
        if candidate_nodes:
            notes.append("insert_review_candidate_nodes")
        return self.build_patch(notes=notes, operations=operations)


class ScreenReviewerPhasePolicy(SubagentPhaseRulePolicy):
    def build_screen_review_replan_patch(
        self,
        *,
        candidate_count: int,
    ) -> SubagentGraphPatch:
        notes = ["replan_screen_review_graph_from_strategy"]
        if candidate_count <= 0:
            notes.append("no_screen_review_candidate_nodes")
        else:
            notes.append(f"screen_review_candidate_count={candidate_count}")
        return self.build_replan_patch(
            reason="screen_review_preprocess_completed",
            notes=notes,
            metadata={"candidate_count": str(candidate_count)},
        )

    def build_screen_review_patch(
        self,
        *,
        candidate_nodes: list[SubagentPlanNode],
        summary_node: SubagentPlanNode,
    ) -> SubagentGraphPatch | None:
        operations = [
            self.insert_subagent(
                node=node,
                reason="insert screen candidate review node",
            )
            for node in candidate_nodes
        ]
        operations.append(
            self.insert_subagent(
                node=summary_node,
                reason="insert screen review summary builder",
            )
        )
        return self.build_patch(
            notes=[
                "insert_screen_review_summary_builder",
                "insert_screen_review_candidate_nodes" if candidate_nodes else "no_screen_review_candidate_nodes",
            ],
            operations=operations,
        )


__all__ = [
    "DistillerPhasePolicy",
    "ReporterPhasePolicy",
    "ReviewerPhasePolicy",
    "RetrieverPhasePolicy",
    "ScreenReviewerPhasePolicy",
    "SubagentPhaseRulePolicy",
]
