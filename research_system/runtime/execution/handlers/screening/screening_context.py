from __future__ import annotations

from research_system.runtime.orchestration.orchestrator_context import RuntimeContext


def collect_screening_inputs(context: RuntimeContext) -> dict[str, list[dict]]:
    return {
        "source_payloads": [
            entry.payload
            for entry in context.blackboard.list_entries(thread_id=context.thread.thread_id)
            if entry.kind == "source_record"
        ],
        "snapshot_payloads": [
            entry.payload
            for entry in context.blackboard.list_entries(thread_id=context.thread.thread_id)
            if entry.kind == "verified_snapshot"
        ],
    }


def collect_screening_enrichment_context(context: RuntimeContext) -> dict[str, object]:
    source_payloads: list[dict] = []
    snapshot_payloads: list[dict] = []
    screen_candidates_reversed: list[dict] = []
    seen_symbols: set[str] = set()
    screening_scorecard = None
    enrichment_plan = None

    entries = list(context.blackboard.list_entries(thread_id=context.thread.thread_id))
    for entry in entries:
        if entry.kind == "source_record":
            source_payloads.append(entry.payload)
        elif entry.kind == "verified_snapshot":
            snapshot_payloads.append(entry.payload)

    for entry in reversed(entries):
        if entry.kind != "source_record":
            continue
        source_type = str(entry.payload.get("source_type") or "")
        if source_type == "screening_scorecard" and screening_scorecard is None:
            screening_scorecard = entry.payload.get("scorecard")
            continue
        if source_type == "screening_enrichment_plan" and enrichment_plan is None:
            enrichment_plan = entry.payload.get("enrichment_plan")
            continue
        if source_type not in {"screen_candidate", "watchlist_candidate"}:
            continue
        candidate = entry.payload.get("candidate")
        if not isinstance(candidate, dict):
            continue
        symbol = str(candidate.get("symbol") or "").strip()
        if not symbol or symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        screen_candidates_reversed.append(candidate)

    screen_candidates_reversed.reverse()
    return {
        "source_payloads": source_payloads,
        "snapshot_payloads": snapshot_payloads,
        "screen_candidates": screen_candidates_reversed,
        "screening_scorecard": screening_scorecard,
        "enrichment_plan": enrichment_plan,
    }
