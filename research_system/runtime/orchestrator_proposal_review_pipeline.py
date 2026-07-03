from __future__ import annotations

from typing import Any


class OrchestratorProposalReviewPipeline:
    def __init__(self, *, host: Any) -> None:
        self.host = host

    def process_methodology_evolution_proposal(
        self,
        *,
        proposal,
        constraints: dict,
        previous_status: str,
        auto_approve: bool,
    ) -> tuple[dict[str, object], dict[str, object]]:
        apply_outputs = {
            "manifest": None,
            "retrieval_policy": None,
            "memory_record": None,
        }
        approval_decision = self.host.evaluate_methodology_auto_approval(
            proposal=proposal,
            constraints=constraints,
        )
        if not auto_approve:
            return approval_decision, apply_outputs
        reviewed_at = proposal.updated_at.__class__.now(proposal.updated_at.tzinfo)
        if approval_decision["approved"]:
            review_decision = "approved"
            reviewer = "system_auto_approve"
            reason = str(approval_decision["reason"] or "auto_approve_for_methodology_evolution")
            apply_approved_side_effects = True
            proposal.status = "approved"
        else:
            review_decision = "pending"
            reviewer = "system_threshold_gate"
            reason = str(approval_decision["reason"] or "pending_manual_review")
            apply_approved_side_effects = False
        self.host.append_proposal_review_event(
            proposal,
            decision=review_decision,
            reviewer=reviewer,
            reason=reason,
            previous_status=previous_status,
            reviewed_at=reviewed_at,
            source="auto_gate",
        )
        proposal.reviewer = reviewer
        proposal.review_reason = reason
        proposal.reviewed_at = reviewed_at
        proposal.updated_at = reviewed_at
        apply_outputs = self.host.persist_proposal_transition(
            proposal,
            previous_status=previous_status,
            apply_approved_side_effects=apply_approved_side_effects,
        )
        return approval_decision, apply_outputs


__all__ = ["OrchestratorProposalReviewPipeline"]
