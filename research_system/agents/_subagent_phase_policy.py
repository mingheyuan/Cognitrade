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
    def build_phase_one_replan_patch(
        self,
        *,
        include_qdrant: bool,
        include_methodology_memory: bool,
        indexed_evidence_count: int,
    ) -> SubagentGraphPatch:
        notes = ["replan_retrieval_phase_one_from_indexing"]
        if include_qdrant:
            notes.append("include_qdrant_branch")
        else:
            notes.append("skip_qdrant_branch")
        if include_methodology_memory:
            notes.append("include_methodology_memory_branch")
        else:
            notes.append("skip_methodology_memory_branch")
        return self.build_replan_patch(
            reason="retrieval_phase_one_indexing_completed",
            notes=notes,
            metadata={
                "include_qdrant": str(bool(include_qdrant)).lower(),
                "include_methodology_memory": str(bool(include_methodology_memory)).lower(),
                "indexed_evidence_count": str(indexed_evidence_count),
            },
        )

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

    def build_phase_two_replan_patch(
        self,
        *,
        decision_subagent_type: str,
        evidence_count: int,
        allow_llm_decision: bool,
    ) -> SubagentGraphPatch:
        notes = ["replan_retrieval_phase_two_from_deduper", f"decision_subagent_type={decision_subagent_type}"]
        if evidence_count <= 0:
            notes.append("empty_evidence_candidates_after_dedup")
        if not allow_llm_decision:
            notes.append("llm_decision_disabled_by_runtime_policy")
        return self.build_replan_patch(
            reason="retrieval_phase_two_dedup_completed",
            notes=notes,
            metadata={
                "decision_subagent_type": decision_subagent_type,
                "evidence_count": str(evidence_count),
                "allow_llm_decision": str(bool(allow_llm_decision)).lower(),
            },
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


class CollectorPhasePolicy(SubagentPhaseRulePolicy):
    def build_stock_check_replan_patch(
        self,
        *,
        snapshot_types: list[str],
        allow_partial: bool,
    ) -> SubagentGraphPatch:
        return self.build_replan_patch(
            reason="collector_stock_check_strategy_resolved",
            notes=[
                "replan_collect_stock_check_from_strategy",
                f"snapshot_types={','.join(snapshot_types)}",
                f"allow_partial={str(bool(allow_partial)).lower()}",
            ],
            metadata={
                "snapshot_types": ",".join(snapshot_types),
                "allow_partial": str(bool(allow_partial)).lower(),
            },
        )

    def build_stock_compare_replan_patch(
        self,
        *,
        snapshot_types: list[str],
        allow_partial: bool,
        symbols: list[str],
    ) -> SubagentGraphPatch:
        return self.build_replan_patch(
            reason="collector_stock_compare_strategy_resolved",
            notes=[
                "replan_collect_stock_compare_from_strategy",
                f"snapshot_types={','.join(snapshot_types)}",
                f"allow_partial={str(bool(allow_partial)).lower()}",
                f"compare_symbol_count={len(symbols)}",
            ],
            metadata={
                "snapshot_types": ",".join(snapshot_types),
                "allow_partial": str(bool(allow_partial)).lower(),
                "symbols": ",".join(str(item).strip() for item in symbols if str(item).strip()),
            },
        )

    def build_sector_research_replan_patch(
        self,
        *,
        include_policy_snapshot: bool,
    ) -> SubagentGraphPatch:
        return self.build_replan_patch(
            reason="collector_sector_strategy_resolved",
            notes=[
                "replan_collect_sector_research_from_strategy",
                f"include_policy_snapshot={str(bool(include_policy_snapshot)).lower()}",
            ],
            metadata={
                "include_policy_snapshot": str(bool(include_policy_snapshot)).lower(),
            },
        )

    def build_stock_screen_replan_patch(
        self,
        *,
        sector_query: str | None,
        top_n: int,
    ) -> SubagentGraphPatch:
        universe_mode = "sector_seeded" if str(sector_query or "").strip() else "market_slice"
        return self.build_replan_patch(
            reason="collector_stock_screen_strategy_resolved",
            notes=[
                "replan_collect_stock_screen_from_strategy",
                f"universe_mode={universe_mode}",
                f"top_n={top_n}",
            ],
            metadata={
                "sector_query": str(sector_query or ""),
                "top_n": str(int(top_n)),
                "universe_mode": universe_mode,
            },
        )

    def build_review_thesis_replan_patch(
        self,
        *,
        followup_mode: str,
        sector: str | None,
        symbol: str | None,
        snapshot_types: list[str],
        allow_partial: bool,
        include_policy_snapshot: bool,
    ) -> SubagentGraphPatch:
        return self.build_replan_patch(
            reason="collector_review_thesis_strategy_resolved",
            notes=[
                "replan_collect_review_thesis_from_strategy",
                f"followup_mode={followup_mode}",
                f"include_policy_snapshot={str(bool(include_policy_snapshot)).lower()}",
                f"allow_partial={str(bool(allow_partial)).lower()}",
            ],
            metadata={
                "followup_mode": followup_mode,
                "sector": str(sector or ""),
                "symbol": str(symbol or ""),
                "snapshot_types": ",".join(snapshot_types),
                "allow_partial": str(bool(allow_partial)).lower(),
                "include_policy_snapshot": str(bool(include_policy_snapshot)).lower(),
            },
        )


class DistillerPhasePolicy(SubagentPhaseRulePolicy):
    def build_chunk_execution_replan_patch(
        self,
        *,
        chunk_subagent_type: str,
        chunk_count: int,
        force_fallback: bool,
        runtime_degraded_reason: str | None,
    ) -> SubagentGraphPatch:
        notes = [
            "replan_distillation_chunk_graph_from_strategy",
            f"chunk_subagent_type={chunk_subagent_type}",
            f"chunk_count={chunk_count}",
        ]
        if force_fallback:
            notes.append("force_fallback_requested")
        if runtime_degraded_reason:
            notes.append(f"runtime_degraded_reason={runtime_degraded_reason}")
        return self.build_replan_patch(
            reason="distillation_chunk_strategy_resolved",
            notes=notes,
            metadata={
                "chunk_subagent_type": chunk_subagent_type,
                "chunk_count": str(chunk_count),
                "force_fallback": str(bool(force_fallback)).lower(),
                "runtime_degraded_reason": runtime_degraded_reason or "",
            },
        )


class CriticPhasePolicy(SubagentPhaseRulePolicy):
    def build_critic_replan_patch(
        self,
        *,
        review_mode: str,
        include_logic_block_review: bool,
        include_dimension_review: bool,
        summary_risk_count: int,
    ) -> SubagentGraphPatch:
        notes = [
            "replan_critic_review_graph_from_strategy",
            f"review_mode={review_mode}",
            f"summary_risk_count={summary_risk_count}",
        ]
        if not include_logic_block_review:
            notes.append("skip_logic_block_review")
        if not include_dimension_review:
            notes.append("skip_dimension_review")
        return self.build_replan_patch(
            reason="critic_review_strategy_resolved",
            notes=notes,
            metadata={
                "review_mode": review_mode,
                "include_logic_block_review": str(bool(include_logic_block_review)).lower(),
                "include_dimension_review": str(bool(include_dimension_review)).lower(),
                "summary_risk_count": str(summary_risk_count),
            },
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
    "CollectorPhasePolicy",
    "CriticPhasePolicy",
    "DistillerPhasePolicy",
    "ReporterPhasePolicy",
    "ReviewerPhasePolicy",
    "RetrieverPhasePolicy",
    "ScreenReviewerPhasePolicy",
    "SubagentPhaseRulePolicy",
]
