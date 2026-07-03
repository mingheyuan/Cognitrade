from __future__ import annotations


class ProposalPayloadBuilders:
    def proposal_display_summary(self, payload: dict) -> str:
        proposal_id = str(payload.get("proposal_id") or "unknown")
        status = str(payload.get("status") or "unknown")
        proposal_type = str(payload.get("proposal_type") or "unknown")
        title = str(payload.get("title") or "").strip()
        line = f"{proposal_id} [{status}] {proposal_type}"
        if title:
            line += f" {title}"
        return line

    def proposal_detail_payload(self, proposal, *, stored_status: str | None = None) -> dict:
        payload = {
            "proposal_id": proposal.proposal_id,
            "status": proposal.status,
            "stored_status": stored_status,
            "proposal_type": proposal.proposal_type,
            "title": proposal.title,
            "summary": proposal.summary,
            "author_agent": proposal.author_agent,
            "sample_size": proposal.sample_size,
            "benchmark_scope": proposal.benchmark_scope,
            "review_reason": proposal.review_reason,
            "reviewer": proposal.reviewer,
            "reviewed_at": proposal.reviewed_at.isoformat() if proposal.reviewed_at else None,
            "review_history": [item.model_dump(mode="json") for item in proposal.review_history],
            "created_from_run_id": proposal.created_from_run_id,
            "created_from_review_id": proposal.created_from_review_id,
            "before_metrics": proposal.before_metrics,
            "after_metrics": proposal.after_metrics,
            "evidence_summary": proposal.evidence_summary,
            "related_entry_ids": proposal.related_entry_ids,
            "created_at": proposal.created_at.isoformat(),
            "updated_at": proposal.updated_at.isoformat(),
        }
        payload["display_summary"] = self.proposal_display_summary(payload)
        return payload

    def proposal_summary_row(self, proposal) -> dict:
        payload = {
            "proposal_id": proposal.proposal_id,
            "status": proposal.status,
            "proposal_type": proposal.proposal_type,
            "title": proposal.title,
            "sample_size": proposal.sample_size,
            "benchmark_scope": proposal.benchmark_scope,
            "review_reason": proposal.review_reason,
            "reviewer": proposal.reviewer,
            "created_from_run_id": proposal.created_from_run_id,
            "updated_at": proposal.updated_at.isoformat(),
        }
        payload["display_summary"] = self.proposal_display_summary(payload)
        return payload


__all__ = ["ProposalPayloadBuilders"]
