from __future__ import annotations


def strategy_follow_up(report_strategy: dict | None) -> str | None:
    focus_area = str((report_strategy or {}).get("focus_area") or "").strip()
    if focus_area == "cashflow_resilience":
        return "Prioritize validating cashflow resilience, leverage tolerance, and fundamental snapshot freshness."
    if focus_area == "same_basis_compare":
        return "Keep peer assumptions and same-basis comparison inputs explicit before final ranking."
    if focus_area == "valuation":
        return "Re-check valuation assumptions, DCF inputs, and whether the intrinsic gap still holds."
    if focus_area == "catalyst_validation":
        return "Re-check whether notice, policy, and regulatory catalysts are still active before acting."
    if focus_area == "momentum_validation":
        return "Confirm short-term momentum and sentiment signals remain intact with the latest market snapshot."
    return None


def summarize_proposal_drafts(evidences: list[dict]) -> dict[str, object]:
    proposal_items = [
        item
        for item in evidences
        if str((item.get("metadata") or {}).get("source_type") or "") == "proposal_draft"
    ]
    proposal_types: list[str] = []
    statuses: list[str] = []
    status_counts: dict[str, int] = {}
    pending_reasons: list[str] = []
    approved_count = 0
    pending_count = 0
    for item in proposal_items:
        hint = item.get("retrieval_hint") or {}
        summary = str(item.get("summary") or "")
        proposal_type = str(hint.get("proposal_type") or "").strip()
        status = str(hint.get("status") or "").strip()
        if not proposal_type and "proposal_draft type=" in summary:
            proposal_type = summary.split("proposal_draft type=", 1)[1].split()[0].strip()
        if not status and "status=" in summary:
            status = summary.split("status=", 1)[1].splitlines()[0].split()[0].strip()
        if proposal_type:
            proposal_types.append(proposal_type)
        if status:
            statuses.append(status)
            status_counts[status] = status_counts.get(status, 0) + 1
            if status == "approved":
                approved_count += 1
            elif status == "pending":
                pending_count += 1
        if "review_reason=" in summary:
            reason = summary.split("review_reason=", 1)[1].splitlines()[0].strip()
            if reason and reason not in pending_reasons:
                pending_reasons.append(reason)
    return {
        "proposal_count": len(proposal_items),
        "proposal_types": sorted(set(proposal_types)),
        "statuses": sorted(set(statuses)),
        "status_counts": status_counts,
        "pending_reasons": pending_reasons,
        "approved_count": approved_count,
        "pending_count": pending_count,
    }
