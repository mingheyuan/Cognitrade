from __future__ import annotations


class ProposalPayloadRenderers:
    def render_proposal_row_text(self, payload: dict) -> str:
        proposal_id = str(payload.get("proposal_id") or "unknown")
        status = str(payload.get("status") or "unknown")
        proposal_type = str(payload.get("proposal_type") or "unknown")
        title = str(payload.get("title") or "").strip()
        sample_size = payload.get("sample_size")
        review_reason = str(payload.get("review_reason") or "").strip()
        line = f"{proposal_id}  [{status}]  {proposal_type}"
        if title:
            line += f"  {title}"
        if sample_size not in (None, ""):
            line += f"  sample_size={sample_size}"
        if review_reason:
            line += f"  reason={review_reason}"
        return line

    def render_proposal_review_history_line(self, row: dict) -> str:
        decision = str(row.get("decision") or "unknown")
        reviewer = str(row.get("reviewer") or "unknown")
        source = str(row.get("source") or "manual")
        reviewed_at = str(row.get("reviewed_at") or "").strip()
        reason = str(row.get("reason") or "").strip()
        previous_status = str(row.get("previous_status") or "").strip()
        line = f"  [{decision}] reviewer={reviewer} source={source}"
        if previous_status:
            line += f" from={previous_status}"
        if reviewed_at:
            line += f" at={reviewed_at}"
        if reason:
            line += f" reason={reason}"
        return line


__all__ = ["ProposalPayloadRenderers"]
