from __future__ import annotations


def run_reviewer_flow(
    host,
    *,
    request,
    action,
    review_request,
    source_entries,
    latest_sector_snapshot,
    latest_market_snapshot,
    latest_fundamental_snapshot,
    latest_notice_snapshot,
) -> dict:
    return host.reviewer.run(
        source_run_id=str((review_request or {}).get("review_target_run_id") or ""),
        query=request.user_query,
        source_entries=source_entries,
        latest_sector_snapshot=latest_sector_snapshot,
        latest_market_snapshot=latest_market_snapshot,
        latest_fundamental_snapshot=latest_fundamental_snapshot,
        latest_notice_snapshot=latest_notice_snapshot,
        planning_context_hints=dict(action.inputs.get("planning_context_hints") or {}),
    )


def run_screen_reviewer_flow(
    host,
    *,
    context,
    action,
    screen_candidates,
    screening_scorecard,
) -> dict:
    return host.screen_reviewer.run(
        source_run_id=context.run_id,
        candidates=screen_candidates,
        scorecard=screening_scorecard,
        planning_context_hints=dict(action.inputs.get("planning_context_hints") or {}),
    )

