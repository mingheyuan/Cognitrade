from __future__ import annotations


class OrchestratorProposalAutoApprovalSupport:
    def __init__(self, *, memory_gate_support) -> None:
        self.memory_gate_support = memory_gate_support

    def evaluate_methodology_auto_approval(
        self,
        *,
        proposal,
        constraints: dict,
    ) -> dict[str, object]:
        if bool(constraints.get("force_auto_approve", False)):
            return {"approved": True, "reason": "force_auto_approve_override"}

        proposal_type = str(proposal.proposal_type or "")
        sample_size = int(proposal.sample_size or 0)
        min_dimension_sample_size = int(constraints.get("min_dimension_sample_size") or 2)
        min_valuation_sample_size = int(constraints.get("min_valuation_sample_size") or 2)
        min_review_pattern_sample_size = int(constraints.get("min_review_pattern_sample_size") or 2)
        min_dimension_confidence = float(constraints.get("min_dimension_average_confidence") or 0.5)
        min_valuation_confidence = float(constraints.get("min_valuation_average_confidence") or 0.5)
        max_memory_support_age_days = int(constraints.get("max_memory_support_age_days") or 180)
        min_dimension_memory_support_entity_scope_count = int(
            constraints.get("min_dimension_memory_support_entity_scope_count") or 2
        )
        min_valuation_memory_support_entity_scope_count = int(
            constraints.get("min_valuation_memory_support_entity_scope_count") or 2
        )

        if proposal_type == "MethodologyTemplatePatch":
            if sample_size < min_dimension_sample_size:
                return {
                    "approved": False,
                    "reason": f"pending_manual_review_dimension_sample_size<{min_dimension_sample_size}",
                }
            average_confidence = float((proposal.before_metrics or {}).get("average_confidence") or 0.0)
            if average_confidence < min_dimension_confidence:
                return {
                    "approved": False,
                    "reason": f"pending_manual_review_dimension_confidence<{min_dimension_confidence}",
                }
            staleness_reason = self.memory_gate_support.stale_memory_support_reason(
                proposal=proposal,
                max_age_days=max_memory_support_age_days,
            )
            if staleness_reason:
                return {"approved": False, "reason": staleness_reason}
            diversity_reason = self.memory_gate_support.insufficient_memory_support_reason(
                proposal=proposal,
                min_entity_scope_count=min_dimension_memory_support_entity_scope_count,
            )
            if diversity_reason:
                return {"approved": False, "reason": diversity_reason}
            return {"approved": True, "reason": "auto_approve_dimension_threshold_met"}

        if proposal_type == "ValuationHeuristicPatch":
            if sample_size < min_valuation_sample_size:
                return {
                    "approved": False,
                    "reason": f"pending_manual_review_valuation_sample_size<{min_valuation_sample_size}",
                }
            average_confidence = float((proposal.before_metrics or {}).get("average_confidence") or 0.0)
            if average_confidence < min_valuation_confidence:
                return {
                    "approved": False,
                    "reason": f"pending_manual_review_valuation_confidence<{min_valuation_confidence}",
                }
            staleness_reason = self.memory_gate_support.stale_memory_support_reason(
                proposal=proposal,
                max_age_days=max_memory_support_age_days,
            )
            if staleness_reason:
                return {"approved": False, "reason": staleness_reason}
            diversity_reason = self.memory_gate_support.insufficient_memory_support_reason(
                proposal=proposal,
                min_entity_scope_count=min_valuation_memory_support_entity_scope_count,
            )
            if diversity_reason:
                return {"approved": False, "reason": diversity_reason}
            return {"approved": True, "reason": "auto_approve_valuation_threshold_met"}

        if proposal_type == "MemoryLearningUpdate":
            if sample_size < min_review_pattern_sample_size:
                return {
                    "approved": False,
                    "reason": f"pending_manual_review_review_pattern_sample_size<{min_review_pattern_sample_size}",
                }
            severity_counts = dict((proposal.before_metrics or {}).get("severity_counts") or {})
            verdict_counts = dict((proposal.after_metrics or {}).get("verdict_counts") or {})
            high_count = int(severity_counts.get("high") or 0)
            rejected_count = int(verdict_counts.get("rejected") or 0)
            if high_count <= 0 and rejected_count <= 0:
                return {
                    "approved": False,
                    "reason": "pending_manual_review_review_pattern_signal_too_weak",
                }
            return {"approved": True, "reason": "auto_approve_review_pattern_threshold_met"}

        return {"approved": False, "reason": f"pending_manual_review_unknown_proposal_type:{proposal_type or 'unknown'}"}
