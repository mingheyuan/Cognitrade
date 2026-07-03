from __future__ import annotations

from typing import Any

from research_system.cli.chat_thread_store import ChatThreadStore
from research_system.runtime.settings import Settings


def ensure_session_thread(*, settings: Settings, state) -> str:
    store = ChatThreadStore(settings)
    resolved = store.ensure_thread(getattr(state, "thread_id", None))
    state.thread_id = resolved
    return resolved


def read_session_thread(*, settings: Settings, state, thread_id: str | None = None) -> dict[str, Any] | None:
    resolved = str(thread_id or getattr(state, "thread_id", None) or "").strip()
    if not resolved:
        return None
    store = ChatThreadStore(settings)
    checkpoint_payload = store.load_thread_checkpoint(thread_id=resolved)
    metadata_payload = store.load_thread_metadata(thread_id=resolved) or {}
    summary_payload = store.load_thread_summary(thread_id=resolved)
    if not store.has_thread(thread_id=resolved) and not any(
        isinstance(payload, dict) for payload in (checkpoint_payload, metadata_payload, summary_payload)
    ):
        return None
    active_turns = [row for row in store.read_all_turns(thread_id=resolved) if isinstance(row, dict)]
    archived_turns = [row for row in store.read_archived_turns(thread_id=resolved) if isinstance(row, dict)]
    combined_turns = sorted(
        [*archived_turns, *active_turns],
        key=lambda row: (
            int(row.get("turn_seq") or 0),
            str(row.get("recorded_at") or ""),
        ),
    )
    summary_metadata = (
        summary_payload.get("metadata")
        if isinstance(summary_payload, dict) and isinstance(summary_payload.get("metadata"), dict)
        else {}
    )
    index_row = next(
        (
            row
            for row in store.list_threads(limit=1000, include_archived=True)
            if str(row.get("thread_id") or "").strip() == resolved
        ),
        {},
    )
    return {
        "thread_id": resolved,
        "thread_label": str(metadata_payload.get("thread_label") or "").strip() or None,
        "topic_title": thread_topic_title(
            last_user_query=summary_metadata.get("last_user_query"),
            last_result_summary=summary_metadata.get("last_result_summary"),
            topic_title=summary_metadata.get("topic_title") or metadata_payload.get("topic_title"),
        ),
        "topic_intent": str(summary_metadata.get("last_result_intent") or "").strip() or None,
        "thread_source": str(metadata_payload.get("thread_source") or "").strip() or "new",
        "parent_thread_id": str(metadata_payload.get("parent_thread_id") or "").strip() or None,
        "fork_turn_count": int(metadata_payload.get("fork_turn_count") or 0),
        "archived": bool(metadata_payload.get("archived")),
        "archived_at": str(metadata_payload.get("archived_at") or "").strip() or None,
        "unarchived_at": str(metadata_payload.get("unarchived_at") or "").strip() or None,
        "has_checkpoint": bool(isinstance(checkpoint_payload, dict) and isinstance(checkpoint_payload.get("session_state"), dict)),
        "has_compact_summary": bool(isinstance(summary_payload, dict) and str(summary_payload.get("summary_text") or "").strip()),
        "summary_text": str((summary_payload or {}).get("summary_text") or "").strip() or None,
        "summary_turn_count": int((summary_payload or {}).get("covered_turn_count") or 0),
        "turn_count": int(index_row.get("turn_count") or len(active_turns)),
        "lifetime_turn_count": int(index_row.get("lifetime_turn_count") or len(combined_turns)),
        "turns": combined_turns,
    }


def list_chat_threads(*, settings: Settings, limit: int = 10, scope: str = "active") -> list[dict[str, Any]]:
    include_archived = scope == "all"
    archived_only = scope == "archived"
    rows = ChatThreadStore(settings).list_threads(
        limit=limit,
        include_archived=include_archived,
        archived_only=archived_only,
    )
    enriched: list[dict[str, Any]] = []
    store = ChatThreadStore(settings)
    for row in rows:
        thread_id = str(row.get("thread_id") or "")
        summary = store.load_thread_summary(thread_id=thread_id)
        metadata_record = store.load_thread_metadata(thread_id=thread_id) or {}
        metadata = summary.get("metadata") if isinstance(summary, dict) and isinstance(summary.get("metadata"), dict) else {}
        enriched.append(
            {
                **row,
                "thread_label": str(metadata_record.get("thread_label") or "").strip() or None,
                "topic_title": thread_topic_title(
                    last_user_query=metadata.get("last_user_query"),
                    last_result_summary=metadata.get("last_result_summary"),
                    topic_title=metadata.get("topic_title") or metadata_record.get("topic_title"),
                ),
                "topic_intent": str(metadata.get("last_result_intent") or "").strip() or None,
                "thread_source": str(metadata_record.get("thread_source") or "").strip() or "new",
                "parent_thread_id": str(metadata_record.get("parent_thread_id") or "").strip() or None,
                "fork_turn_count": int(metadata_record.get("fork_turn_count") or 0),
                "archived": bool(metadata_record.get("archived")),
                "archived_at": str(metadata_record.get("archived_at") or "").strip() or None,
            }
        )
    return enriched


def thread_preview_payload(*, settings: Settings, thread_id: str, turn_limit: int = 3) -> dict[str, Any]:
    resolved_thread_id = str(thread_id or "").strip()
    if not resolved_thread_id:
        return {
            "thread_id": None,
            "thread_label": None,
            "recent_turns": [],
            "summary_text": None,
            "summary_turn_count": 0,
            "has_checkpoint": False,
        }
    store = ChatThreadStore(settings)
    recent_turns = store.read_recent_turns(thread_id=resolved_thread_id, limit=max(0, turn_limit))
    summary_payload = store.load_thread_summary(thread_id=resolved_thread_id)
    checkpoint_payload = store.load_thread_checkpoint(thread_id=resolved_thread_id)
    metadata_payload = store.load_thread_metadata(thread_id=resolved_thread_id) or {}
    return {
        "thread_id": resolved_thread_id,
        "thread_label": str(metadata_payload.get("thread_label") or "").strip() or None,
        "recent_turns": recent_turns,
        "summary_text": str((summary_payload or {}).get("summary_text") or "").strip() or None,
        "summary_turn_count": int((summary_payload or {}).get("covered_turn_count") or 0),
        "has_checkpoint": bool(isinstance(checkpoint_payload, dict) and isinstance(checkpoint_payload.get("session_state"), dict)),
    }


def thread_topic_title(*, last_user_query: Any, last_result_summary: Any, topic_title: Any = None) -> str | None:
    explicit = str(topic_title or "").strip()
    if explicit:
        return explicit
    query = trim_context_text(str(last_user_query or "").strip(), limit=72)
    if query:
        return query
    summary = trim_context_text(str(last_result_summary or "").strip(), limit=72)
    return summary or None


def trim_context_text(text: str, limit: int = 220) -> str:
    compact = text.replace("\n", " ").strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."
