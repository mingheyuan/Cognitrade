from __future__ import annotations

from research_system.schemas.proposals import ProposalReviewEvent


class OrchestratorProposalTransitionSupport:
    def __init__(
        self,
        *,
        proposal_store,
        methodology_manifest_service,
        retrieval_policy_service,
        methodology_memory_writer,
    ) -> None:
        self.proposal_store = proposal_store
        self.methodology_manifest_service = methodology_manifest_service
        self.retrieval_policy_service = retrieval_policy_service
        self.methodology_memory_writer = methodology_memory_writer

    def persist_proposal_transition(
        self,
        proposal,
        *,
        previous_status: str,
        apply_approved_side_effects: bool,
    ) -> dict[str, object]:
        self.proposal_store.save(proposal, previous_status=previous_status)
        outputs: dict[str, object] = {
            "manifest": None,
            "retrieval_policy": None,
            "memory_record": None,
        }
        if not apply_approved_side_effects:
            return outputs
        outputs["manifest"] = self.methodology_manifest_service.approve(proposal)
        outputs["retrieval_policy"] = self.retrieval_policy_service.approve(proposal)
        outputs["memory_record"] = self.methodology_memory_writer.write_from_proposal(proposal)
        return outputs

    def append_proposal_review_event(
        self,
        proposal,
        *,
        decision: str,
        reviewer: str,
        reason: str,
        previous_status: str | None,
        reviewed_at,
        source: str,
    ) -> None:
        history = list(getattr(proposal, "review_history", []) or [])
        history.append(
            ProposalReviewEvent(
                decision=decision,
                reviewer=reviewer,
                reason=reason,
                reviewed_at=reviewed_at,
                previous_status=previous_status,
                source=source,
            )
        )
        proposal.review_history = history
