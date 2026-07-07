from __future__ import annotations

from typing import Any

from research_system.adapters.storage.local_memory_adapter import LocalMemoryAdapter
from research_system.cli.memory.chat_memory_context import (
    has_recent_chat_memory,
    load_latest_run_memory,
)
from research_system.cli.state.chat_state import (
    apply_session_checkpoint_to_state,
    apply_session_state_patch_to_state,
    clear_recent_session_patch,
)
from research_system.cli.memory.chat_thread_queries import ensure_session_thread
from research_system.cli.memory.chat_thread_store import ChatThreadStore
from research_system.runtime.platform.settings import Settings
from research_system.services.memory import MemoryService

_THREAD_CONTEXT_LIMIT = 6


def hydrate_session_from_persistent_memory(*, settings: Settings, state) -> bool:
    if has_recent_chat_memory(state):
        return False
    thread_id = ensure_session_thread(settings=settings, state=state)
    store = ChatThreadStore(settings)
    checkpoint = store.load_thread_checkpoint(thread_id=thread_id)
    if checkpoint is not None:
        apply_session_checkpoint_to_state(state=state, checkpoint=checkpoint)
        apply_thread_metadata_to_state(state=state, metadata=store.load_thread_metadata(thread_id=thread_id) or {})
        return True
    turns = store.read_recent_turns(thread_id=thread_id, limit=_THREAD_CONTEXT_LIMIT)
    if turns:
        apply_turns_to_state(state=state, turns=turns)
        apply_thread_metadata_to_state(state=state, metadata=store.load_thread_metadata(thread_id=thread_id) or {})
        return True
    record = MemoryService(LocalMemoryAdapter(settings)).load_latest_chat_research_memory(thread_id=thread_id)
    if record is None:
        record = load_latest_run_memory(settings)
    if record is None:
        return False
    apply_record_to_state(state=state, record=record)
    apply_thread_metadata_to_state(state=state, metadata=store.load_thread_metadata(thread_id=thread_id) or {})
    return True


def apply_turns_to_state(*, state, turns: list[dict[str, Any]]) -> None:
    for turn in turns:
        role = str(turn.get("role") or "").strip()
        turn_type = str(turn.get("turn_type") or "").strip()
        content = str(turn.get("content") or "").strip()
        if role == "user" and content:
            state.last_user_query = content
        if role == "assistant" and content:
            metadata = turn.get("metadata") if isinstance(turn.get("metadata"), dict) else {}
            summary = str(metadata.get("summary") or "").strip()
            conclusion = str(metadata.get("conclusion") or "").strip()
            follow_up = str(metadata.get("follow_up") or "").strip()
            if summary:
                state.last_result_summary = summary
            if conclusion or content:
                state.last_result_conclusion = conclusion or content
            if follow_up:
                state.last_result_follow_up = follow_up
        if turn_type.startswith("research_"):
            state.last_result_intent = str(turn.get("intent") or "").strip() or state.last_result_intent
            state.last_run_id = str(turn.get("run_id") or "").strip() or state.last_run_id


def apply_record_to_state(*, state, record: dict[str, Any]) -> None:
    state.last_user_query = record.get("query") or state.last_user_query
    state.last_result_intent = record.get("intent") or state.last_result_intent
    state.last_result_summary = record.get("summary") or state.last_result_summary
    state.last_result_conclusion = record.get("conclusion") or state.last_result_conclusion
    state.last_result_follow_up = record.get("follow_up") or state.last_result_follow_up
    state.last_run_id = record.get("run_id") or state.last_run_id
    state.thread_id = record.get("thread_id") or getattr(state, "thread_id", None)


def clear_recent_session_memory(state) -> None:
    apply_session_state_patch_to_state(state, clear_recent_session_patch())


def apply_thread_metadata_to_state(*, state, metadata: dict[str, Any]) -> None:
    if not metadata:
        return
    state.thread_source = str(metadata.get("thread_source") or "").strip() or getattr(state, "thread_source", None)
    state.parent_thread_id = str(metadata.get("parent_thread_id") or "").strip() or getattr(state, "parent_thread_id", None)
