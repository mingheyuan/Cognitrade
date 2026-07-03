from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research_system.cli.follow_up.chat_follow_up_markers import (
    is_context_dependent_follow_up,
    looks_like_review_follow_up,
    query_intent_hint,
)


@dataclass(frozen=True)
class FollowUpResolution:
    query: str
    intent: str | None
    run_id: str | None
    resolved: bool
    reason: str | None = None


def resolve_follow_up_research_request(
    *,
    query: str,
    parsed_intent: str | None,
    parsed_run_id: str | None,
    state,
    conversation_context: list[dict[str, Any]] | None = None,
) -> FollowUpResolution:
    stripped = query.strip()
    if not stripped:
        return FollowUpResolution(query=query, intent=parsed_intent, run_id=parsed_run_id, resolved=False)

    intent_hint = query_intent_hint(stripped)
    if parsed_intent is None and intent_hint is not None:
        return FollowUpResolution(
            query=query,
            intent=intent_hint,
            run_id=parsed_run_id,
            resolved=False,
            reason="query_hint",
        )

    normalized_run_id = str(parsed_run_id).strip() if parsed_run_id is not None else None
    if normalized_run_id == "":
        normalized_run_id = None

    if parsed_intent == "review_thesis" or normalized_run_id:
        return FollowUpResolution(
            query=query,
            intent=parsed_intent,
            run_id=normalized_run_id,
            resolved=False,
        )

    previous = previous_research_record(state=state, conversation_context=conversation_context)
    if previous is None:
        return FollowUpResolution(query=query, intent=parsed_intent, run_id=parsed_run_id, resolved=False)

    if looks_like_review_follow_up(stripped):
        review_target_run_id = resolve_review_target_run_id(state=state)
        if review_target_run_id:
            return FollowUpResolution(
                query=augment_follow_up_query(previous_query=previous.get("query"), query=stripped),
                intent="review_thesis",
                run_id=review_target_run_id,
                resolved=True,
                reason="review_follow_up",
            )
        return FollowUpResolution(query=query, intent=parsed_intent, run_id=parsed_run_id, resolved=False)

    if not is_context_dependent_follow_up(stripped):
        return FollowUpResolution(query=query, intent=parsed_intent, run_id=parsed_run_id, resolved=False)

    inferred_intent = str(previous.get("intent") or "").strip() or getattr(state, "current_intent", None)
    if not inferred_intent:
        return FollowUpResolution(query=query, intent=parsed_intent, run_id=parsed_run_id, resolved=False)

    resolved_run_id = None
    if inferred_intent == "review_thesis":
        resolved_run_id = resolve_review_target_run_id(state=state)

    return FollowUpResolution(
        query=augment_follow_up_query(previous_query=previous.get("query"), query=stripped),
        intent=inferred_intent,
        run_id=resolved_run_id,
        resolved=True,
        reason="context_follow_up",
    )


def previous_research_record(*, state, conversation_context: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    state_record = {
        "query": getattr(state, "last_user_query", None),
        "intent": getattr(state, "last_result_intent", None),
        "run_id": getattr(state, "last_run_id", None),
    }
    if any(state_record.values()):
        return state_record

    for item in reversed(conversation_context or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "").strip() == "recent_research_memory":
            return {
                "query": item.get("query"),
                "intent": item.get("intent"),
                "run_id": item.get("run_id"),
            }
        if str(item.get("turn_type") or "").strip() == "research_query":
            return {
                "query": item.get("content"),
                "intent": item.get("intent"),
                "run_id": item.get("run_id"),
            }
    return None


def resolve_review_target_run_id(*, state) -> str | None:
    value = getattr(state, "review_target_run_id", None) or getattr(state, "last_run_id", None)
    resolved = str(value or "").strip()
    return resolved or None


def augment_follow_up_query(*, previous_query: Any, query: str) -> str:
    previous_text = str(previous_query or "").strip()
    if not previous_text:
        return query
    return (
        "\u4e0a\u4e00\u8f6e\u7814\u7a76\u5bf9\u8c61: "
        f"{previous_text}\n"
        "\u672c\u8f6e\u8ffd\u95ee: "
        f"{query}"
    )
