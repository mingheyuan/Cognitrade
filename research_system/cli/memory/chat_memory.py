from __future__ import annotations

from typing import Any

from research_system.cli.memory.chat_memory_context import (
    build_chat_conversation_context as query_build_chat_conversation_context,
    build_recent_research_context as query_build_recent_research_context,
    build_relevant_memory_context as query_build_relevant_memory_context,
    context_from_record as query_context_from_record,
    context_signature as query_context_signature,
    extract_query_terms as query_extract_query_terms,
    has_recent_chat_memory as query_has_recent_chat_memory,
    load_latest_run_memory as query_load_latest_run_memory,
    score_query_match as query_score_query_match,
    state_memory_record as query_state_memory_record,
    trim_context_text as query_trim_context_text,
)
from research_system.cli.memory.chat_memory_persistence import (
    apply_record_to_state as persistence_apply_record_to_state,
    apply_turns_to_state as persistence_apply_turns_to_state,
    auto_compact_thread_if_needed as persistence_auto_compact_thread_if_needed,
    build_thread_summary_text as persistence_build_thread_summary_text,
    clear_recent_session_memory as persistence_clear_recent_session_memory,
    compact_session_thread as persistence_compact_session_thread,
    extract_conclusion as persistence_extract_conclusion,
    extract_follow_up as persistence_extract_follow_up,
    hydrate_session_from_persistent_memory as persistence_hydrate_session_from_persistent_memory,
    remember_basic_chat_turn as persistence_remember_basic_chat_turn,
    remember_chat_research_result as persistence_remember_chat_research_result,
)
from research_system.cli.memory.chat_thread_lifecycle import (
    archive_session_thread as lifecycle_archive_session_thread,
    fork_session_thread as lifecycle_fork_session_thread,
    rename_session_thread as lifecycle_rename_session_thread,
    resume_session_thread as lifecycle_resume_session_thread,
    start_new_session_thread as lifecycle_start_new_session_thread,
    switch_session_thread as lifecycle_switch_session_thread,
    unarchive_session_thread as lifecycle_unarchive_session_thread,
)
from research_system.cli.memory.chat_thread_queries import (
    build_thread_context as query_build_thread_context,
    build_thread_profile_context as query_build_thread_profile_context,
    build_thread_summary_context as query_build_thread_summary_context,
    build_thread_turn_context as query_build_thread_turn_context,
    current_thread_summary as query_current_thread_summary,
    ensure_session_thread as query_ensure_session_thread,
    list_chat_threads as query_list_chat_threads,
    read_session_thread as query_read_session_thread,
    thread_preview_payload as query_thread_preview_payload,
    thread_topic_title as query_thread_topic_title,
)
from research_system.runtime.platform.settings import Settings

_THREAD_CONTEXT_LIMIT = 6


def ensure_session_thread(*, settings: Settings, state) -> str:
    return query_ensure_session_thread(settings=settings, state=state)


def start_new_session_thread(*, settings: Settings, state) -> str:
    return lifecycle_start_new_session_thread(settings=settings, state=state)


def fork_session_thread(*, settings: Settings, state, source_thread_id: str | None = None) -> str:
    return lifecycle_fork_session_thread(
        settings=settings,
        state=state,
        source_thread_id=source_thread_id,
    )


def switch_session_thread(*, settings: Settings, state, thread_id: str) -> bool:
    return lifecycle_switch_session_thread(settings=settings, state=state, thread_id=thread_id)


def resume_session_thread(*, settings: Settings, state, thread_id: str) -> dict[str, Any] | None:
    return lifecycle_resume_session_thread(settings=settings, state=state, thread_id=thread_id)


def rename_session_thread(*, settings: Settings, state, thread_label: str) -> dict[str, Any] | None:
    return lifecycle_rename_session_thread(settings=settings, state=state, thread_label=thread_label)


def read_session_thread(*, settings: Settings, state, thread_id: str | None = None) -> dict[str, Any] | None:
    return query_read_session_thread(settings=settings, state=state, thread_id=thread_id)


def archive_session_thread(*, settings: Settings, state, thread_id: str | None = None) -> dict[str, Any] | None:
    return lifecycle_archive_session_thread(settings=settings, state=state, thread_id=thread_id)


def unarchive_session_thread(*, settings: Settings, thread_id: str) -> dict[str, Any] | None:
    return lifecycle_unarchive_session_thread(settings=settings, thread_id=thread_id)


def list_chat_threads(*, settings: Settings, limit: int = 10, scope: str = "active") -> list[dict[str, Any]]:
    return query_list_chat_threads(settings=settings, limit=limit, scope=scope)


def compact_session_thread(*, settings: Settings, state) -> dict[str, Any]:
    return persistence_compact_session_thread(settings=settings, state=state)


def has_recent_chat_memory(state) -> bool:
    return query_has_recent_chat_memory(state)


def hydrate_session_from_persistent_memory(*, settings: Settings, state) -> bool:
    return persistence_hydrate_session_from_persistent_memory(settings=settings, state=state)


def remember_chat_research_result(
    *,
    settings: Settings,
    state,
    query: str,
    intent: str | None,
    payload: object,
) -> dict[str, Any] | None:
    return persistence_remember_chat_research_result(
        settings=settings,
        state=state,
        query=query,
        intent=intent,
        payload=payload,
    )


def remember_basic_chat_turn(
    *,
    settings: Settings,
    state,
    query: str,
    answer: str,
) -> None:
    persistence_remember_basic_chat_turn(settings=settings, state=state, query=query, answer=answer)


def build_recent_research_context(*, settings: Settings, state, limit: int = 1) -> list[dict[str, Any]]:
    return query_build_recent_research_context(settings=settings, state=state, limit=limit)


def build_thread_context(*, settings: Settings, state, limit: int = _THREAD_CONTEXT_LIMIT) -> list[dict[str, Any]]:
    return query_build_thread_context(settings=settings, state=state, limit=limit)


def build_chat_conversation_context(*, settings: Settings, state, query: str | None = None) -> list[dict[str, Any]]:
    return query_build_chat_conversation_context(settings=settings, state=state, query=query)


def current_thread_summary(*, settings: Settings, state) -> dict[str, Any]:
    return query_current_thread_summary(settings=settings, state=state)


def build_thread_profile_context(*, settings: Settings, state) -> list[dict[str, Any]]:
    return query_build_thread_profile_context(settings=settings, state=state)


def build_thread_summary_context(*, settings: Settings, state) -> list[dict[str, Any]]:
    return query_build_thread_summary_context(settings=settings, state=state)


def thread_preview_payload(*, settings: Settings, thread_id: str, turn_limit: int = 3) -> dict[str, Any]:
    return query_thread_preview_payload(settings=settings, thread_id=thread_id, turn_limit=turn_limit)


def build_relevant_memory_context(
    *,
    settings: Settings,
    state,
    query: str | None,
    limit: int = 4,
) -> list[dict[str, Any]]:
    return query_build_relevant_memory_context(settings=settings, state=state, query=query, limit=limit)


def _build_thread_turn_context(*, settings: Settings, state, limit: int) -> list[dict[str, Any]]:
    return query_build_thread_turn_context(settings=settings, state=state, limit=limit)


def _extract_query_terms(query: str) -> set[str]:
    return query_extract_query_terms(query)


def _score_query_match(*, query_terms: set[str], blob: str) -> tuple[list[str], float]:
    return query_score_query_match(query_terms=query_terms, blob=blob)


def _trim_context_text(text: str, limit: int = 220) -> str:
    return query_trim_context_text(text, limit=limit)


def _thread_topic_title(*, last_user_query: Any, last_result_summary: Any, topic_title: Any = None) -> str | None:
    return query_thread_topic_title(
        last_user_query=last_user_query,
        last_result_summary=last_result_summary,
        topic_title=topic_title,
    )


def _context_signature(context: dict[str, Any]) -> tuple[Any, ...]:
    return query_context_signature(context)


def _apply_turns_to_state(*, state, turns: list[dict[str, Any]]) -> None:
    persistence_apply_turns_to_state(state=state, turns=turns)


def _apply_record_to_state(*, state, record: dict[str, Any]) -> None:
    persistence_apply_record_to_state(state=state, record=record)


def _clear_recent_session_memory(state) -> None:
    persistence_clear_recent_session_memory(state)


def _load_latest_run_memory(settings: Settings) -> dict[str, Any] | None:
    return query_load_latest_run_memory(settings)


def _extract_conclusion(payload: dict[str, Any]) -> str | None:
    return persistence_extract_conclusion(payload)


def _extract_follow_up(payload: dict[str, Any]) -> str | None:
    return persistence_extract_follow_up(payload)


def _state_memory_record(state) -> dict[str, Any]:
    return query_state_memory_record(state)


def _context_from_record(record: dict[str, Any]) -> dict[str, Any] | None:
    return query_context_from_record(record)


def _build_thread_summary_text(
    *,
    state,
    turns: list[dict[str, Any]],
    previous_summaries: list[dict[str, Any]] | None = None,
    covered_turn_count_before: int = 0,
    total_turn_count: int | None = None,
) -> str:
    return persistence_build_thread_summary_text(
        state=state,
        turns=turns,
        previous_summaries=previous_summaries,
        covered_turn_count_before=covered_turn_count_before,
        total_turn_count=total_turn_count,
    )


def _auto_compact_thread_if_needed(*, settings: Settings, state, store: ChatThreadStore | None = None, thread_id: str | None = None) -> None:
    persistence_auto_compact_thread_if_needed(
        settings=settings,
        state=state,
        store=store,
        thread_id=thread_id,
    )
