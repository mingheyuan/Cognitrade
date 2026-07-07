from __future__ import annotations

from research_system.schemas.runtime import BlackboardEntry


def add_review_findings(
    support,
    *,
    context,
    request,
    intent: str,
    phase: str,
    agent: str,
    findings: list[dict],
    source_refs: list[dict],
) -> None:
    for finding in findings:
        entry = BlackboardEntry(
            run_id=context.run_id,
            thread_id=context.thread.thread_id,
            request_id=request.request_id,
            kind="review_finding",
            agent=agent,
            phase=phase,
            status="active" if finding.get("verdict") == "confirmed" else "needs_review",
            payload=finding,
            source_refs=source_refs,
            confidence=1.0,
            risk_flags=[str(finding.get("verdict") or "inconclusive")],
        )
        support.add_entry(context, request, intent, entry)


def add_review_summary(
    support,
    *,
    context,
    request,
    intent: str,
    phase: str,
    agent: str,
    summary: dict,
    source_refs: list[dict],
) -> None:
    entry = BlackboardEntry(
        run_id=context.run_id,
        thread_id=context.thread.thread_id,
        request_id=request.request_id,
        kind="source_record",
        agent=agent,
        phase=phase,
        payload={
            "title": "review_summary",
            "content": str(summary or {}),
            "source_type": "review_summary",
            "summary": summary or {},
        },
        source_refs=source_refs,
        confidence=1.0,
        risk_flags=[],
    )
    support.add_entry(context, request, intent, entry)
