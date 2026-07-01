from __future__ import annotations


def proposal_display_summary(payload: dict) -> str:
    proposal_id = str(payload.get("proposal_id") or "unknown")
    status = str(payload.get("status") or "unknown")
    proposal_type = str(payload.get("proposal_type") or "unknown")
    title = str(payload.get("title") or "").strip()
    line = f"{proposal_id} [{status}] {proposal_type}"
    if title:
        line += f" {title}"
    return line


def proposal_detail_payload(proposal, *, stored_status: str | None = None) -> dict:
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
    payload["display_summary"] = proposal_display_summary(payload)
    return payload


def proposal_summary_row(proposal) -> dict:
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
    payload["display_summary"] = proposal_display_summary(payload)
    return payload


def proposal_detail_fields(payload: dict) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    ordered = (
        ("proposal_id", payload.get("proposal_id")),
        ("status", payload.get("status") or "unknown"),
        ("proposal_type", payload.get("proposal_type") or "unknown"),
        ("title", payload.get("title")),
        ("summary", payload.get("summary")),
        ("sample_size", payload.get("sample_size")),
        ("benchmark_scope", payload.get("benchmark_scope")),
        ("review_reason", payload.get("review_reason")),
        ("reviewer", payload.get("reviewer")),
        ("reviewed_at", payload.get("reviewed_at")),
        ("created_from_run_id", payload.get("created_from_run_id")),
    )
    for label, value in ordered:
        if value in (None, ""):
            continue
        fields.append((label, str(value)))
    return fields


def proposal_result_sections(payload: dict) -> list[dict]:
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    sections: list[dict] = []
    for key in ("manifest", "retrieval_policy", "memory_record"):
        value = result.get(key)
        if value is not None:
            sections.append({key: value})
    return sections


def render_proposal_row_text(payload: dict) -> str:
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


def render_proposal_review_history_line(row: dict) -> str:
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
