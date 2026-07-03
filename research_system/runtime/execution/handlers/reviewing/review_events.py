from __future__ import annotations


def emit_reviewer_requested(
    support,
    *,
    context,
    request,
    intent: str,
    phase: str,
    review_target_run_id: str | None,
) -> None:
    support.emit_event(
        context,
        request_id=request.request_id,
        intent=intent,
        event_type="review_requested",
        phase=phase,
        agent="reviewer",
        message="Reviewer posterior review requested",
        payload={"source_run_id": review_target_run_id},
    )


def emit_reviewer_completed(
    support,
    *,
    context,
    request,
    intent: str,
    phase: str,
    summary: dict,
) -> None:
    support.emit_event(
        context,
        request_id=request.request_id,
        intent=intent,
        event_type="review_completed",
        phase=phase,
        agent="reviewer",
        message="Reviewer posterior review completed",
        payload=summary,
    )


def emit_screen_reviewer_requested(
    support,
    *,
    context,
    request,
    intent: str,
    phase: str,
    candidate_count: int,
) -> None:
    support.emit_event(
        context,
        request_id=request.request_id,
        intent=intent,
        event_type="review_requested",
        phase=phase,
        agent="screen_reviewer",
        message="Screen reviewer requested",
        payload={"candidate_count": candidate_count},
    )


def emit_screen_reviewer_completed(
    support,
    *,
    context,
    request,
    intent: str,
    phase: str,
    summary: dict,
) -> None:
    support.emit_event(
        context,
        request_id=request.request_id,
        intent=intent,
        event_type="review_completed",
        phase=phase,
        agent="screen_reviewer",
        message="Screen reviewer completed",
        payload=summary,
    )
