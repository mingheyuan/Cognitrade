from __future__ import annotations

from typing import Any

from research_system.cli.chat_thread_context_support import (
    build_thread_context as context_build_thread_context,
    build_thread_summary_context as context_build_thread_summary_context,
    build_thread_turn_context as context_build_thread_turn_context,
)
from research_system.cli.chat_thread_query_views import (
    ensure_session_thread as views_ensure_session_thread,
    list_chat_threads as views_list_chat_threads,
    read_session_thread as views_read_session_thread,
    thread_preview_payload as views_thread_preview_payload,
    thread_topic_title as views_thread_topic_title,
    trim_context_text as views_trim_context_text,
)
from research_system.cli.chat_thread_summary_support import (
    build_thread_profile_context as summary_build_thread_profile_context,
    current_thread_summary as summary_current_thread_summary,
)
from research_system.runtime.settings import Settings

_THREAD_CONTEXT_LIMIT = 6


def ensure_session_thread(*, settings: Settings, state) -> str:
    return views_ensure_session_thread(settings=settings, state=state)


def read_session_thread(*, settings: Settings, state, thread_id: str | None = None) -> dict[str, Any] | None:
    return views_read_session_thread(settings=settings, state=state, thread_id=thread_id)


def list_chat_threads(*, settings: Settings, limit: int = 10, scope: str = "active") -> list[dict[str, Any]]:
    return views_list_chat_threads(settings=settings, limit=limit, scope=scope)


def build_thread_context(*, settings: Settings, state, limit: int = _THREAD_CONTEXT_LIMIT) -> list[dict[str, Any]]:
    return context_build_thread_context(settings=settings, state=state, limit=limit)


def current_thread_summary(*, settings: Settings, state) -> dict[str, Any]:
    return summary_current_thread_summary(settings=settings, state=state)


def build_thread_profile_context(*, settings: Settings, state) -> list[dict[str, Any]]:
    return summary_build_thread_profile_context(settings=settings, state=state)


def build_thread_summary_context(*, settings: Settings, state) -> list[dict[str, Any]]:
    return context_build_thread_summary_context(settings=settings, state=state)


def thread_preview_payload(*, settings: Settings, thread_id: str, turn_limit: int = 3) -> dict[str, Any]:
    return views_thread_preview_payload(settings=settings, thread_id=thread_id, turn_limit=turn_limit)


def build_thread_turn_context(*, settings: Settings, state, limit: int) -> list[dict[str, Any]]:
    return context_build_thread_turn_context(settings=settings, state=state, limit=limit)


def thread_topic_title(*, last_user_query: Any, last_result_summary: Any, topic_title: Any = None) -> str | None:
    return views_thread_topic_title(
        last_user_query=last_user_query,
        last_result_summary=last_result_summary,
        topic_title=topic_title,
    )


def _trim_context_text(text: str, limit: int = 220) -> str:
    return views_trim_context_text(text, limit=limit)
