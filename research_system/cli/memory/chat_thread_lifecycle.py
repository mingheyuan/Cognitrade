from __future__ import annotations

import re
from typing import Any

from research_system.cli.memory.chat_memory_persistence import (
    clear_recent_session_memory,
    hydrate_session_from_persistent_memory,
)
from research_system.cli.state.chat_state import (
    apply_session_state_patch_to_state,
    build_new_thread_session_patch,
    persist_thread_checkpoint,
)
from research_system.cli.memory.chat_thread_queries import (
    current_thread_summary,
    ensure_session_thread,
    thread_topic_title,
)
from research_system.cli.memory.chat_thread_store import ChatThreadStore
from research_system.runtime.platform.settings import Settings
from research_system.services.memory import build_memory_recorded_at


def start_new_session_thread(*, settings: Settings, state) -> str:
    store = ChatThreadStore(settings)
    resolved = store.ensure_thread(None)
    store.ensure_thread_metadata(
        thread_id=resolved,
        defaults={
            "thread_source": "new",
            "parent_thread_id": None,
            "forked_from_thread_id": None,
            "fork_turn_count": 0,
            "archived": False,
            "archived_at": None,
            "unarchived_at": None,
        },
    )
    apply_session_state_patch_to_state(state, build_new_thread_session_patch(thread_id=resolved))
    return resolved


def fork_session_thread(*, settings: Settings, state, source_thread_id: str | None = None) -> str:
    store = ChatThreadStore(settings)
    parent_thread_id = str(source_thread_id or getattr(state, "thread_id", None) or "").strip()
    if not parent_thread_id:
        parent_thread_id = ensure_session_thread(settings=settings, state=state)
    parent_thread_id = store.ensure_thread(parent_thread_id)
    child_thread_id = store.ensure_thread(None)

    turns = store.read_all_turns(thread_id=parent_thread_id)
    copied_turns: list[dict[str, Any]] = []
    for index, row in enumerate(turns, start=1):
        copied = dict(row)
        copied["thread_id"] = child_thread_id
        copied["turn_seq"] = index
        copied_turns.append(copied)
    if copied_turns:
        store.replace_turns(thread_id=child_thread_id, rows=copied_turns)

    parent_summary = store.load_thread_summary(thread_id=parent_thread_id)
    if isinstance(parent_summary, dict):
        summary_metadata = dict(parent_summary.get("metadata") or {})
        summary_metadata.setdefault("forked_from_thread_id", parent_thread_id)
        store.write_thread_summary(
            thread_id=child_thread_id,
            summary_text=str(parent_summary.get("summary_text") or ""),
            last_turn_at=parent_summary.get("last_turn_at"),
            source_turn_count=parent_summary.get("source_turn_count"),
            covered_turn_count=parent_summary.get("covered_turn_count"),
            delta_turn_count=parent_summary.get("delta_turn_count"),
            metadata=summary_metadata,
        )

    parent_checkpoint = store.load_thread_checkpoint(thread_id=parent_thread_id)
    if isinstance(parent_checkpoint, dict) and isinstance(parent_checkpoint.get("session_state"), dict):
        session_state = dict(parent_checkpoint["session_state"])
        session_state["thread_id"] = child_thread_id
        store.write_thread_checkpoint(thread_id=child_thread_id, session_state=session_state)

    parent_metadata = store.load_thread_metadata(thread_id=parent_thread_id) or {"thread_id": parent_thread_id}
    topic_title = thread_topic_title(
        last_user_query=getattr(state, "last_user_query", None),
        last_result_summary=getattr(state, "last_result_summary", None),
        topic_title=parent_metadata.get("topic_title"),
    )
    store.write_thread_metadata(
        thread_id=child_thread_id,
        metadata={
            **dict(parent_metadata),
            "thread_id": child_thread_id,
            "thread_source": "fork",
            "parent_thread_id": parent_thread_id,
            "forked_from_thread_id": parent_thread_id,
            "fork_turn_count": len(copied_turns),
            "topic_title": topic_title,
            "archived": False,
            "archived_at": None,
            "unarchived_at": None,
        },
    )

    state.thread_id = child_thread_id
    state.thread_source = "fork"
    state.parent_thread_id = parent_thread_id
    clear_recent_session_memory(state)
    state.thread_id = child_thread_id
    state.thread_source = "fork"
    state.parent_thread_id = parent_thread_id
    hydrate_session_from_persistent_memory(settings=settings, state=state)
    return child_thread_id


def switch_session_thread(*, settings: Settings, state, thread_id: str) -> bool:
    resolved = str(thread_id or "").strip()
    if not resolved:
        return False
    store = ChatThreadStore(settings)
    state.thread_id = store.ensure_thread(resolved)
    clear_recent_session_memory(state)
    hydrate_session_from_persistent_memory(settings=settings, state=state)
    return True


def resume_session_thread(*, settings: Settings, state, thread_id: str) -> dict[str, Any] | None:
    resolved = str(thread_id or "").strip()
    if not resolved:
        return None
    store = ChatThreadStore(settings)
    checkpoint_payload = store.load_thread_checkpoint(thread_id=resolved)
    metadata_payload = store.load_thread_metadata(thread_id=resolved)
    summary_payload = store.load_thread_summary(thread_id=resolved)
    if not store.has_thread(thread_id=resolved) and not any(
        isinstance(payload, dict) for payload in (checkpoint_payload, metadata_payload, summary_payload)
    ):
        return None
    store.ensure_thread(resolved)
    previous_thread_id = str(getattr(state, "thread_id", None) or "").strip() or None
    resumed = switch_session_thread(settings=settings, state=state, thread_id=resolved)
    if not resumed:
        return None
    summary = current_thread_summary(settings=settings, state=state)
    return {
        "thread_id": resolved,
        "previous_thread_id": previous_thread_id,
        "resumed": True,
        "thread_label": summary.get("thread_label"),
        "has_checkpoint": bool(summary.get("has_checkpoint")),
        "has_compact_summary": bool(summary.get("has_compact_summary")),
        "topic_title": summary.get("topic_title"),
        "topic_intent": summary.get("topic_intent"),
    }


def rename_session_thread(*, settings: Settings, state, thread_label: str) -> dict[str, Any] | None:
    normalized_label = re.sub(r"\s+", " ", str(thread_label or "").strip())
    if not normalized_label:
        return None
    thread_id = ensure_session_thread(settings=settings, state=state)
    store = ChatThreadStore(settings)
    metadata = store.ensure_thread_metadata(thread_id=thread_id, defaults={"thread_label": None})
    metadata["thread_label"] = normalized_label
    store.write_thread_metadata(thread_id=thread_id, metadata=metadata)
    return {
        "thread_id": thread_id,
        "thread_label": normalized_label,
    }


def archive_session_thread(*, settings: Settings, state, thread_id: str | None = None) -> dict[str, Any] | None:
    store = ChatThreadStore(settings)
    target_thread_id = str(thread_id or getattr(state, "thread_id", None) or "").strip()
    if not target_thread_id:
        return None
    if not store.has_thread(thread_id=target_thread_id):
        return None
    if target_thread_id == str(getattr(state, "thread_id", None) or "").strip():
        persist_thread_checkpoint(settings=settings, state=state)
    archived_at = build_memory_recorded_at()
    metadata = store.archive_thread(thread_id=target_thread_id, archived_at=archived_at)
    next_thread_id = None
    if target_thread_id == str(getattr(state, "thread_id", None) or "").strip():
        next_thread_id = start_new_session_thread(settings=settings, state=state)
    return {
        "thread_id": target_thread_id,
        "archived_at": archived_at,
        "next_thread_id": next_thread_id,
        "metadata": metadata,
    }


def unarchive_session_thread(*, settings: Settings, thread_id: str) -> dict[str, Any] | None:
    store = ChatThreadStore(settings)
    target_thread_id = str(thread_id or "").strip()
    if not target_thread_id or not store.has_thread(thread_id=target_thread_id):
        return None
    unarchived_at = build_memory_recorded_at()
    metadata = store.unarchive_thread(thread_id=target_thread_id, unarchived_at=unarchived_at)
    return {
        "thread_id": target_thread_id,
        "unarchived_at": unarchived_at,
        "metadata": metadata,
    }
