from __future__ import annotations

from typing import Any

from research_system.cli.memory.chat_thread_query_views import ensure_session_thread
from research_system.cli.memory.chat_thread_store import ChatThreadStore
from research_system.runtime.platform.settings import Settings

_THREAD_CONTEXT_LIMIT = 6


def build_thread_context(*, settings: Settings, state, limit: int = _THREAD_CONTEXT_LIMIT) -> list[dict[str, Any]]:
    from research_system.cli.memory.chat_thread_queries import build_thread_profile_context, build_thread_summary_context, build_thread_turn_context

    return [
        *build_thread_profile_context(settings=settings, state=state),
        *build_thread_summary_context(settings=settings, state=state),
        *build_thread_turn_context(settings=settings, state=state, limit=limit),
    ]


def build_thread_summary_context(*, settings: Settings, state) -> list[dict[str, Any]]:
    thread_id = ensure_session_thread(settings=settings, state=state)
    rows = ChatThreadStore(settings).read_recent_summaries(thread_id=thread_id, limit=2)
    contexts: list[dict[str, Any]] = []
    for index, payload in enumerate(rows):
        if not isinstance(payload, dict):
            continue
        summary_text = str(payload.get("summary_text") or "").strip()
        if not summary_text:
            continue
        contexts.append(
            {
                "type": "chat_thread_summary",
                "thread_id": thread_id,
                "summary": summary_text,
                "summary_index": index,
                "last_turn_at": str(payload.get("last_turn_at") or "").strip() or None,
                "source_turn_count": payload.get("source_turn_count"),
                "covered_turn_count": payload.get("covered_turn_count"),
                "delta_turn_count": payload.get("delta_turn_count"),
                "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
            }
        )
    return contexts


def build_thread_turn_context(*, settings: Settings, state, limit: int) -> list[dict[str, Any]]:
    thread_id = ensure_session_thread(settings=settings, state=state)
    turns = ChatThreadStore(settings).read_recent_turns(thread_id=thread_id, limit=limit)
    contexts: list[dict[str, Any]] = []
    for turn in turns:
        content = str(turn.get("content") or "").strip()
        if not content:
            continue
        contexts.append(
            {
                "type": "chat_thread_turn",
                "thread_id": thread_id,
                "role": str(turn.get("role") or "").strip() or None,
                "turn_type": str(turn.get("turn_type") or "").strip() or None,
                "content": content,
                "intent": str(turn.get("intent") or "").strip() or None,
                "run_id": str(turn.get("run_id") or "").strip() or None,
                "recorded_at": str(turn.get("recorded_at") or "").strip() or None,
                "metadata": turn.get("metadata") if isinstance(turn.get("metadata"), dict) else {},
            }
        )
    return contexts
