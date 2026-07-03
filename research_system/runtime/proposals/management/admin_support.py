from __future__ import annotations

from datetime import datetime, timezone

from research_system.services.proposals.payload.payloads import (
    proposal_detail_payload,
    proposal_display_summary,
    proposal_summary_row,
)


class OrchestratorProposalAdminSupport:
    def __init__(self, host) -> None:
        self.host = host

    def list_proposals(self, *, status: str | None = None, limit: int = 50) -> list[dict]:
        proposal_store = self.host.require_proposal_store()
        if status:
            proposals = proposal_store.list(status)
            proposals.sort(key=lambda item: item.updated_at, reverse=True)
        else:
            proposals = proposal_store.list_all()
        return [proposal_summary_row(item) for item in proposals[:limit]]

    def show_proposal(self, proposal_id: str) -> dict:
        located = self.host.require_proposal_store().find(proposal_id)
        if located is None:
            raise self.host.orchestrator_error_cls(f"proposal not found: {proposal_id}")
        proposal, stored_status = located
        return proposal_detail_payload(proposal, stored_status=stored_status)

    def review_proposal(
        self,
        proposal_id: str,
        *,
        decision: str,
        reviewer: str = "manual_cli_review",
        reason: str | None = None,
    ) -> dict:
        normalized_decision = str(decision or "").strip().lower()
        if normalized_decision not in {"approve", "reject"}:
            raise self.host.orchestrator_error_cls(f"unsupported proposal decision: {decision}")
        located = self.host.require_proposal_store().find(proposal_id)
        if located is None:
            raise self.host.orchestrator_error_cls(f"proposal not found: {proposal_id}")
        proposal, previous_status = located
        if proposal.status in {"approved", "rejected"}:
            current = self.show_proposal(proposal.proposal_id)
            return {
                "proposal_id": proposal.proposal_id,
                "status": proposal.status,
                "message": f"proposal already {proposal.status}",
                "display_summary": f"{proposal.proposal_id} already {proposal.status}",
                "result": current,
            }

        reviewed_at = datetime.now(timezone.utc)
        next_status = "approved" if normalized_decision == "approve" else "rejected"
        self.host.require_research_support().append_proposal_review_event(
            proposal,
            decision=next_status,
            reviewer=reviewer,
            reason=str(reason or f"manual_{next_status}_via_cli"),
            previous_status=previous_status,
            reviewed_at=reviewed_at,
            source="manual_cli_review",
        )
        proposal.status = next_status
        proposal.reviewer = reviewer
        proposal.review_reason = str(reason or f"manual_{proposal.status}_via_cli")
        proposal.reviewed_at = reviewed_at
        proposal.updated_at = reviewed_at
        apply_outputs = self.host.require_research_support().persist_proposal_transition(
            proposal,
            previous_status=previous_status,
            apply_approved_side_effects=proposal.status == "approved",
        )

        return {
            "proposal_id": proposal.proposal_id,
            "status": proposal.status,
            "message": f"proposal {proposal.status}",
            "display_summary": proposal_display_summary(
                {
                    "proposal_id": proposal.proposal_id,
                    "status": proposal.status,
                    "proposal_type": proposal.proposal_type,
                    "title": proposal.title,
                }
            ),
            "result": {
                **self.show_proposal(proposal.proposal_id),
                **apply_outputs,
            },
        }
