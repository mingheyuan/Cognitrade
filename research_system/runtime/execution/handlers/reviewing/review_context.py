from __future__ import annotations


def collect_review_request_context(context) -> dict[str, object]:
    review_request = None
    latest_sector_snapshot = None
    latest_market_snapshot = None
    latest_fundamental_snapshot = None
    latest_notice_snapshot = None
    for entry in context.blackboard.list_entries(thread_id=context.thread.thread_id):
        if entry.kind == "source_record" and entry.payload.get("source_type") == "review_request":
            review_request = entry.payload
        elif entry.kind == "verified_snapshot":
            snapshot_type = entry.payload.get("snapshot_type")
            if snapshot_type == "sector":
                latest_sector_snapshot = entry.payload
            elif snapshot_type == "market":
                latest_market_snapshot = entry.payload
            elif snapshot_type == "fundamental":
                latest_fundamental_snapshot = entry.payload
            elif snapshot_type == "notice":
                latest_notice_snapshot = entry.payload
    source_entries = list((review_request or {}).get("review_source_entries") or []) if review_request is not None else []
    return {
        "review_request": review_request,
        "latest_sector_snapshot": latest_sector_snapshot,
        "latest_market_snapshot": latest_market_snapshot,
        "latest_fundamental_snapshot": latest_fundamental_snapshot,
        "latest_notice_snapshot": latest_notice_snapshot,
        "source_entries": source_entries,
    }


def collect_screen_review_context(context) -> dict[str, object]:
    screen_candidates_reversed: list[dict] = []
    screening_scorecard = None
    seen_symbols: set[str] = set()
    entries = list(context.blackboard.list_entries(thread_id=context.thread.thread_id))
    for entry in reversed(entries):
        if entry.kind != "source_record":
            continue
        source_type = str(entry.payload.get("source_type") or "")
        if source_type == "screening_scorecard" and screening_scorecard is None:
            screening_scorecard = entry.payload.get("scorecard")
            continue
        if source_type not in {"screen_candidate", "watchlist_candidate"}:
            continue
        candidate = entry.payload.get("candidate")
        if isinstance(candidate, dict):
            symbol = str(candidate.get("symbol") or "").strip()
            if symbol and symbol not in seen_symbols:
                seen_symbols.add(symbol)
                screen_candidates_reversed.append(candidate)
    screen_candidates_reversed.reverse()
    return {
        "screen_candidates": screen_candidates_reversed,
        "screening_scorecard": screening_scorecard,
    }
