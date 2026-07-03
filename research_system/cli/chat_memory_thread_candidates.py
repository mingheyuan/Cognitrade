from __future__ import annotations

from typing import Any

from research_system.cli.chat_memory_match_support import (
    score_query_match,
    trim_context_text,
)
from research_system.cli.chat_thread_store import ChatThreadStore

_RELEVANT_SUMMARY_SCAN_LIMIT = 8
_RELEVANT_ARCHIVE_SCAN_LIMIT = 40


def build_thread_profile_candidates(
    *,
    query_terms: set[str],
    thread_profile: dict[str, Any],
) -> list[tuple[float, dict[str, Any]]]:
    profile_blob = "\n".join(
        str(thread_profile.get(key) or "")
        for key in (
            "topic_title",
            "topic_intent",
            "last_user_query",
            "last_result_summary",
            "last_result_follow_up",
            "open_question",
            "last_run_id",
        )
    ).strip()
    if not profile_blob:
        return []
    matched_terms, score = score_query_match(query_terms=query_terms, blob=profile_blob)
    if score <= 0:
        return []
    return [
        (
            score,
            {
                "type": "relevant_thread_profile",
                "thread_id": thread_profile.get("thread_id"),
                "topic_title": thread_profile.get("topic_title"),
                "topic_intent": thread_profile.get("topic_intent"),
                "last_user_query": thread_profile.get("last_user_query"),
                "last_result_summary": trim_context_text(str(thread_profile.get("last_result_summary") or "").strip()) or None,
                "last_result_follow_up": trim_context_text(str(thread_profile.get("last_result_follow_up") or "").strip()) or None,
                "last_run_id": thread_profile.get("last_run_id"),
                "open_question": trim_context_text(str(thread_profile.get("open_question") or "").strip()) or None,
                "recommended_next_step": trim_context_text(str(thread_profile.get("recommended_next_step") or "").strip()) or None,
                "thread_status": thread_profile.get("thread_status"),
                "matched_terms": matched_terms,
                "match_score": round(score, 3),
            },
        )
    ]


def build_summary_candidates(
    *,
    store: ChatThreadStore,
    thread_id: str,
    query_terms: set[str],
) -> list[tuple[float, dict[str, Any]]]:
    candidates: list[tuple[float, dict[str, Any]]] = []
    for payload in store.read_recent_summaries(thread_id=thread_id, limit=_RELEVANT_SUMMARY_SCAN_LIMIT):
        if not isinstance(payload, dict):
            continue
        summary_text = str(payload.get("summary_text") or "").strip()
        if not summary_text:
            continue
        matched_terms, score = score_query_match(query_terms=query_terms, blob=summary_text)
        if score <= 0:
            continue
        candidates.append(
            (
                score,
                {
                    "type": "relevant_thread_summary",
                    "thread_id": thread_id,
                    "summary": summary_text,
                    "last_turn_at": str(payload.get("last_turn_at") or "").strip() or None,
                    "source_turn_count": payload.get("source_turn_count"),
                    "matched_terms": matched_terms,
                    "match_score": round(score, 3),
                },
            )
        )
    return candidates


def build_archive_candidates(
    *,
    store: ChatThreadStore,
    thread_id: str,
    query_terms: set[str],
) -> list[tuple[float, dict[str, Any]]]:
    candidates: list[tuple[float, dict[str, Any]]] = []
    for turn in store.read_archived_turns(thread_id=thread_id, limit=_RELEVANT_ARCHIVE_SCAN_LIMIT):
        if not isinstance(turn, dict):
            continue
        content = str(turn.get("content") or "").strip()
        if not content:
            continue
        matched_terms, score = score_query_match(query_terms=query_terms, blob=content)
        if score <= 0:
            continue
        candidates.append(
            (
                score,
                {
                    "type": "relevant_thread_archive",
                    "thread_id": thread_id,
                    "role": str(turn.get("role") or "").strip() or None,
                    "turn_type": str(turn.get("turn_type") or "").strip() or None,
                    "content": trim_context_text(content),
                    "intent": str(turn.get("intent") or "").strip() or None,
                    "run_id": str(turn.get("run_id") or "").strip() or None,
                    "recorded_at": str(turn.get("recorded_at") or "").strip() or None,
                    "matched_terms": matched_terms,
                    "match_score": round(score, 3),
                },
            )
        )
    return candidates


__all__ = [
    "build_archive_candidates",
    "build_summary_candidates",
    "build_thread_profile_candidates",
]
