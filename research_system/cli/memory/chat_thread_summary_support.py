from __future__ import annotations

from typing import Any

from research_system.cli.memory.chat_thread_query_views import ensure_session_thread, thread_topic_title
from research_system.cli.memory.chat_thread_store import ChatThreadStore
from research_system.runtime.platform.settings import Settings

_THREAD_CONTEXT_LIMIT = 6


def current_thread_summary(*, settings: Settings, state) -> dict[str, Any]:
    thread_id = ensure_session_thread(settings=settings, state=state)
    store = ChatThreadStore(settings)
    turns = store.read_recent_turns(thread_id=thread_id, limit=_THREAD_CONTEXT_LIMIT)
    persisted_summary = store.load_thread_summary(thread_id=thread_id)
    persisted_checkpoint = store.load_thread_checkpoint(thread_id=thread_id)
    metadata_record = store.load_thread_metadata(thread_id=thread_id) or {}
    summary_metadata = (persisted_summary or {}).get("metadata") if isinstance((persisted_summary or {}).get("metadata"), dict) else {}
    return {
        "thread_id": thread_id,
        "turn_count": len(turns),
        "lifetime_turn_count": int(
            next(
                (
                    row.get("lifetime_turn_count") or 0
                    for row in store.list_threads(limit=1000, include_archived=True)
                    if str(row.get("thread_id") or "") == thread_id
                ),
                0,
            )
        ),
        "thread_label": str(metadata_record.get("thread_label") or "").strip() or None,
        "last_user_query": getattr(state, "last_user_query", None),
        "last_run_id": getattr(state, "last_run_id", None),
        "last_result_summary": summary_metadata.get("last_result_summary") or getattr(state, "last_result_summary", None),
        "last_result_follow_up": summary_metadata.get("last_result_follow_up") or getattr(state, "last_result_follow_up", None),
        "has_compact_summary": bool(isinstance(persisted_summary, dict) and str(persisted_summary.get("summary_text") or "").strip()),
        "has_checkpoint": bool(isinstance(persisted_checkpoint, dict) and isinstance(persisted_checkpoint.get("session_state"), dict)),
        "topic_title": thread_topic_title(
            last_user_query=summary_metadata.get("last_user_query") or getattr(state, "last_user_query", None),
            last_result_summary=summary_metadata.get("last_result_summary") or getattr(state, "last_result_summary", None),
            topic_title=summary_metadata.get("topic_title") or metadata_record.get("topic_title"),
        ),
        "topic_intent": str(summary_metadata.get("last_result_intent") or getattr(state, "last_result_intent", None) or "").strip() or None,
        "open_question": str(getattr(state, "pending_clarification_question", None) or "").strip() or None,
        "recommended_next_step": summary_metadata.get("last_result_follow_up") or getattr(state, "last_result_follow_up", None),
        "thread_status": "clarification_pending" if str(getattr(state, "pending_clarification_question", None) or "").strip() else "active",
        "thread_source": str(metadata_record.get("thread_source") or "").strip() or "new",
        "parent_thread_id": str(metadata_record.get("parent_thread_id") or "").strip() or None,
        "fork_turn_count": int(metadata_record.get("fork_turn_count") or 0),
        "archived": bool(metadata_record.get("archived")),
        "archived_at": str(metadata_record.get("archived_at") or "").strip() or None,
    }


def build_thread_profile_context(*, settings: Settings, state) -> list[dict[str, Any]]:
    summary = current_thread_summary(settings=settings, state=state)
    if not any(
        summary.get(key)
        for key in (
            "topic_title",
            "topic_intent",
            "last_user_query",
            "last_result_summary",
            "last_result_follow_up",
            "last_run_id",
            "open_question",
        )
    ):
        return []
    return [
        {
            "type": "chat_thread_profile",
            "thread_id": summary.get("thread_id"),
            "thread_label": summary.get("thread_label"),
            "topic_title": summary.get("topic_title"),
            "topic_intent": summary.get("topic_intent"),
            "last_user_query": summary.get("last_user_query"),
            "last_result_summary": summary.get("last_result_summary"),
            "last_result_follow_up": summary.get("last_result_follow_up"),
            "last_run_id": summary.get("last_run_id"),
            "open_question": summary.get("open_question"),
            "recommended_next_step": summary.get("recommended_next_step"),
            "thread_status": summary.get("thread_status"),
            "thread_source": summary.get("thread_source"),
            "parent_thread_id": summary.get("parent_thread_id"),
            "fork_turn_count": summary.get("fork_turn_count"),
            "archived": summary.get("archived"),
            "archived_at": summary.get("archived_at"),
            "turn_count": summary.get("turn_count"),
            "lifetime_turn_count": summary.get("lifetime_turn_count"),
            "has_compact_summary": summary.get("has_compact_summary"),
            "has_checkpoint": summary.get("has_checkpoint"),
        }
    ]
