from __future__ import annotations


def extend_reviewer_trace(
    support,
    *,
    context,
    phase: str,
    source_entry_count: int,
    summary: dict,
    payload: dict | None = None,
) -> None:
    effective_payload = dict(payload or {})
    if not effective_payload.get("subagent_trace"):
        effective_payload["subagent_trace"] = [
            {
                "subagent_type": "review_claim_extractor",
                "status": "completed",
                "objective": "Extract reviewable claims from review target.",
                "notes": [f"source_entry_count={source_entry_count}"],
            },
            {
                "subagent_type": "review_summary_builder",
                "status": "completed",
                "objective": "Aggregate posterior review findings into review summary.",
                "notes": list((summary or {}).get("notes") or []),
            },
        ]
    support.extend_subagent_trace(
        context,
        agent="reviewer",
        phase=phase,
        payload=effective_payload,
    )


def extend_screen_reviewer_trace(
    support,
    *,
    context,
    phase: str,
    summary: dict,
    payload: dict | None = None,
) -> None:
    effective_payload = dict(payload or {})
    if not effective_payload.get("subagent_trace"):
        effective_payload["subagent_trace"] = [
            {
                "subagent_type": "screen_candidate_reviewer",
                "status": "completed",
                "objective": "Perform lightweight evidence review for screened candidates.",
                "notes": list((summary or {}).get("notes") or []),
            }
        ]
    support.extend_subagent_trace(
        context,
        agent="screen_reviewer",
        phase=phase,
        payload=effective_payload,
    )
