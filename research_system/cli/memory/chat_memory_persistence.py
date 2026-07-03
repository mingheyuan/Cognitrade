from __future__ import annotations

from typing import Any

from research_system.cli.memory.chat_memory_compaction_support import (
    auto_compact_thread_if_needed as compaction_auto_compact_thread_if_needed,
    build_thread_summary_text as compaction_build_thread_summary_text,
    compact_session_thread as compaction_compact_session_thread,
)
from research_system.cli.memory.chat_memory_hydration_support import (
    apply_record_to_state as hydration_apply_record_to_state,
    apply_turns_to_state as hydration_apply_turns_to_state,
    clear_recent_session_memory as hydration_clear_recent_session_memory,
    hydrate_session_from_persistent_memory as hydration_hydrate_session_from_persistent_memory,
)
from research_system.cli.memory.chat_memory_remember_support import (
    extract_conclusion as remember_extract_conclusion,
    extract_follow_up as remember_extract_follow_up,
    remember_basic_chat_turn as remember_remember_basic_chat_turn,
    remember_chat_research_result as remember_remember_chat_research_result,
)
from research_system.cli.memory.chat_thread_store import ChatThreadStore
from research_system.runtime.platform.settings import Settings


def compact_session_thread(*, settings: Settings, state) -> dict[str, Any]:
    return compaction_compact_session_thread(settings=settings, state=state)


def hydrate_session_from_persistent_memory(*, settings: Settings, state) -> bool:
    return hydration_hydrate_session_from_persistent_memory(settings=settings, state=state)


def remember_chat_research_result(
    *,
    settings: Settings,
    state,
    query: str,
    intent: str | None,
    payload: object,
) -> dict[str, Any] | None:
    return remember_remember_chat_research_result(
        query=query,
        intent=intent,
        payload=payload,
        settings=settings,
        state=state,
    )


def remember_basic_chat_turn(
    *,
    settings: Settings,
    state,
    query: str,
    answer: str,
) -> None:
    remember_remember_basic_chat_turn(settings=settings, state=state, query=query, answer=answer)


def apply_turns_to_state(*, state, turns: list[dict[str, Any]]) -> None:
    hydration_apply_turns_to_state(state=state, turns=turns)


def apply_record_to_state(*, state, record: dict[str, Any]) -> None:
    hydration_apply_record_to_state(state=state, record=record)


def clear_recent_session_memory(state) -> None:
    hydration_clear_recent_session_memory(state)


def extract_conclusion(payload: dict[str, Any]) -> str | None:
    return remember_extract_conclusion(payload)


def extract_follow_up(payload: dict[str, Any]) -> str | None:
    return remember_extract_follow_up(payload)


def build_thread_summary_text(
    *,
    state,
    turns: list[dict[str, Any]],
    previous_summaries: list[dict[str, Any]] | None = None,
    covered_turn_count_before: int = 0,
    total_turn_count: int | None = None,
) -> str:
    return compaction_build_thread_summary_text(
        state=state,
        turns=turns,
        previous_summaries=previous_summaries,
        covered_turn_count_before=covered_turn_count_before,
        total_turn_count=total_turn_count,
    )


def auto_compact_thread_if_needed(
    *,
    settings: Settings,
    state,
    store: ChatThreadStore | None = None,
    thread_id: str | None = None,
) -> None:
    compaction_auto_compact_thread_if_needed(
        settings=settings,
        state=state,
        store=store,
        thread_id=thread_id,
    )
