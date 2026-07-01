from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research_system.services.stock_target_resolution import infer_query_intent_hint


_REVIEW_MARKERS = (
    "\u590d\u6838",
    "\u56de\u770b",
    "\u68c0\u67e5\u4e0a\u4e00\u8f6e",
    "\u68c0\u67e5\u4e0a\u6b21",
    "\u9a8c\u8bc1\u4e0a\u4e00\u8f6e",
    "\u8bc4\u4f30\u4e0a\u4e00\u8f6e",
    "\u4e0a\u4e00\u8f6e\u7ed3\u8bba",
    "\u4e0a\u6b21\u7ed3\u8bba",
    "review the previous",
    "review previous",
    "review last run",
    "validate previous",
)
_REFERENTIAL_MARKERS = (
    "\u5b83",
    "\u5b83\u4eec",
    "\u8fd9\u53ea",
    "\u8fd9\u4e24\u53ea",
    "\u8fd9\u4e09\u53ea",
    "\u8fd9\u51e0\u53ea",
    "\u90a3\u53ea",
    "\u90a3\u4e24\u53ea",
    "\u90a3\u4e09\u53ea",
    "\u90a3\u51e0\u53ea",
    "\u90a3\u4e24\u4e2a",
    "\u90a3\u4e09\u4e2a",
    "\u524d\u8005",
    "\u540e\u8005",
    "\u4e0a\u4e00\u8f6e",
    "\u4e0a\u6b21",
    "\u4e0a\u4e00\u4e2a\u7ed3\u8bba",
    "\u4e0a\u4e00\u8f6e\u7814\u7a76",
    "\u4e0a\u6b21\u7814\u7a76",
    "that one",
    "those two",
    "those three",
    "previous one",
    "last one",
    "last run",
)
_SHORT_CONTINUATION_MARKERS = (
    "\u7ee7\u7eed",
    "\u63a5\u7740",
    "\u518d\u8bf4",
    "\u518d\u5c55\u5f00",
    "\u5c55\u5f00",
    "\u7ec6\u8bf4",
    "\u8865\u5145",
    "\u8be6\u7ec6\u4e00\u70b9",
    "\u8be6\u7ec6\u4e9b",
    "continue",
    "go on",
    "expand",
    "more detail",
    "dig deeper",
)
_RELATIVE_COMPARISON_MARKERS = (
    "\u8c01\u66f4",
    "\u54ea\u4e2a\u66f4",
    "\u54ea\u53ea\u66f4",
    "\u66f4\u4fbf\u5b9c",
    "\u66f4\u7a33\u5065",
    "\u66f4\u503c\u5f97",
    "\u600e\u4e48\u6392",
    "\u6392\u4e00\u4e0b",
    "which is better",
    "which one is better",
    "which is cheaper",
    "which is safer",
    "who is more",
    "which is more",
    "rank them",
)


@dataclass(frozen=True)
class FollowUpResolution:
    query: str
    intent: str | None
    run_id: str | None
    resolved: bool
    reason: str | None = None


def looks_like_follow_up_research_query(query: str) -> bool:
    stripped = query.strip()
    lowered = stripped.lower()
    return any(marker in stripped for marker in _REVIEW_MARKERS[:8]) or any(
        marker in lowered for marker in _REVIEW_MARKERS[8:]
    ) or _is_context_dependent_follow_up(stripped)


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

    intent_hint = infer_query_intent_hint(stripped)
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

    previous = _previous_research_record(state=state, conversation_context=conversation_context)
    if previous is None:
        return FollowUpResolution(query=query, intent=parsed_intent, run_id=parsed_run_id, resolved=False)

    if _looks_like_review_follow_up(stripped):
        review_target_run_id = _resolve_review_target_run_id(state=state)
        if review_target_run_id:
            return FollowUpResolution(
                query=_augment_follow_up_query(previous_query=previous.get("query"), query=stripped),
                intent="review_thesis",
                run_id=review_target_run_id,
                resolved=True,
                reason="review_follow_up",
            )
        return FollowUpResolution(query=query, intent=parsed_intent, run_id=parsed_run_id, resolved=False)

    if not _is_context_dependent_follow_up(stripped):
        return FollowUpResolution(query=query, intent=parsed_intent, run_id=parsed_run_id, resolved=False)

    inferred_intent = str(previous.get("intent") or "").strip() or getattr(state, "current_intent", None)
    if not inferred_intent:
        return FollowUpResolution(query=query, intent=parsed_intent, run_id=parsed_run_id, resolved=False)

    resolved_run_id = None
    if inferred_intent == "review_thesis":
        resolved_run_id = _resolve_review_target_run_id(state=state)

    return FollowUpResolution(
        query=_augment_follow_up_query(previous_query=previous.get("query"), query=stripped),
        intent=inferred_intent,
        run_id=resolved_run_id,
        resolved=True,
        reason="context_follow_up",
    )


def _looks_like_review_follow_up(query: str) -> bool:
    lowered = query.lower()
    return any(marker in query for marker in _REVIEW_MARKERS[:8]) or any(
        marker in lowered for marker in _REVIEW_MARKERS[8:]
    )


def _is_context_dependent_follow_up(query: str) -> bool:
    lowered = query.lower()
    stripped = query.strip()
    if _has_stock_code(stripped):
        return False
    if _contains_referential_marker(stripped, lowered):
        return True
    if _contains_short_continuation_marker(stripped, lowered) and len(stripped) <= 10:
        return True
    if _contains_relative_comparison_marker(stripped, lowered):
        if len(stripped) <= 18:
            return True
        # Allow short English comparative follow-ups like "who is more resilient?"
        if stripped.isascii() and len(stripped.split()) <= 6:
            return True
    return False


def _contains_referential_marker(query: str, lowered: str) -> bool:
    return any(marker in query for marker in _REFERENTIAL_MARKERS[:19]) or any(
        marker in lowered for marker in _REFERENTIAL_MARKERS[19:]
    )


def _contains_short_continuation_marker(query: str, lowered: str) -> bool:
    return any(marker in query for marker in _SHORT_CONTINUATION_MARKERS[:9]) or any(
        marker in lowered for marker in _SHORT_CONTINUATION_MARKERS[9:]
    )


def _contains_relative_comparison_marker(query: str, lowered: str) -> bool:
    return any(marker in query for marker in _RELATIVE_COMPARISON_MARKERS[:8]) or any(
        marker in lowered for marker in _RELATIVE_COMPARISON_MARKERS[8:]
    )


def _has_stock_code(query: str) -> bool:
    digits = "".join(ch for ch in query if ch.isdigit())
    return len(digits) >= 6


def _previous_research_record(*, state, conversation_context: list[dict[str, Any]] | None) -> dict[str, Any] | None:
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


def _resolve_review_target_run_id(*, state) -> str | None:
    value = getattr(state, "review_target_run_id", None) or getattr(state, "last_run_id", None)
    resolved = str(value or "").strip()
    return resolved or None


def _augment_follow_up_query(*, previous_query: Any, query: str) -> str:
    previous_text = str(previous_query or "").strip()
    if not previous_text:
        return query
    return (
        "\u4e0a\u4e00\u8f6e\u7814\u7a76\u5bf9\u8c61: "
        f"{previous_text}\n"
        "\u672c\u8f6e\u8ffd\u95ee: "
        f"{query}"
    )


def is_short_term_memory_query(query: str) -> bool:
    stripped = query.strip()
    lowered = stripped.lower()
    markers = (
        "\u521a\u521a\u6211\u4eec\u5728\u804a\u4ec0\u4e48",
        "\u521a\u521a\u804a\u5230\u54ea\u4e86",
        "\u6211\u4eec\u521a\u521a\u5728\u804a\u4ec0\u4e48",
        "\u4e0a\u4e00\u53e5\u662f\u4ec0\u4e48",
        "\u4e0a\u4e00\u8f6e\u804a\u4ec0\u4e48",
        "what were we just talking about",
        "what did we just discuss",
        "what were we talking about",
        "what was the last topic",
    )
    return any(marker in stripped for marker in markers[:5]) or any(marker in lowered for marker in markers[5:])


def short_term_memory_reply(state) -> str:
    if not state.last_user_query and not state.last_result_summary and not state.last_result_conclusion:
        return "\u8fd9\u8f6e\u4f1a\u8bdd\u91cc\u6211\u8fd8\u6ca1\u6709\u53ef\u56de\u987e\u7684\u4e0a\u4e00\u6761\u7814\u7a76\u5185\u5bb9\u3002"
    lines = ["\u521a\u521a\u6211\u4eec\u5728\u804a\uff1a"]
    if state.last_user_query:
        lines.append(f"\u95ee\u9898\uff1a{state.last_user_query}")
    if state.last_result_intent:
        lines.append(f"\u7c7b\u578b\uff1a{state.last_result_intent}")
    if state.last_result_summary:
        lines.append(f"\u6458\u8981\uff1a{state.last_result_summary}")
    elif state.last_result_conclusion:
        lines.append(f"\u7ed3\u8bba\uff1a{state.last_result_conclusion}")
    if state.last_run_id:
        lines.append(f"run_id\uff1a{state.last_run_id}")
    return "\n".join(lines)
