from __future__ import annotations

from research_system.cli.runtime.chat_action_runtime import ChatActionRuntime
from research_system.cli.memory.chat_memory import (
    archive_session_thread,
    compact_session_thread,
    fork_session_thread,
    read_session_thread,
    rename_session_thread,
    resume_session_thread,
    start_new_session_thread,
    unarchive_session_thread,
)
from research_system.cli.state.chat_state import (
    build_new_thread_session_patch,
    mirror_session_state_patch,
)


def handle_new_thread_action(runtime: ChatActionRuntime) -> None:
    thread_id = start_new_session_thread(settings=runtime.settings, state=runtime.state)
    runtime.emit_meta(
        f"new thread: {thread_id}",
        session_patch=build_new_thread_session_patch(thread_id=thread_id),
    )
    return None


def handle_rename_thread_action(runtime: ChatActionRuntime) -> None:
    thread_label = str(runtime.parsed.get("thread_label") or "").strip()
    if not thread_label:
        runtime.emit_warning("thread name is required")
        return None
    payload = rename_session_thread(settings=runtime.settings, state=runtime.state, thread_label=thread_label)
    if payload is None:
        runtime.emit_warning("thread name is required")
        return None
    runtime.emit_meta(f"thread_renamed: {payload['thread_id']}")
    runtime.emit_meta(f"thread_name: {payload['thread_label']}")
    return None


def handle_read_thread_action(runtime: ChatActionRuntime) -> None:
    thread_id = str(runtime.parsed.get("thread_id") or "").strip()
    if not thread_id:
        runtime.emit_warning("thread_id is required")
        return None
    payload = read_session_thread(settings=runtime.settings, state=runtime.state, thread_id=thread_id)
    if payload is None:
        runtime.emit_warning(f"thread_id does not exist: {thread_id}")
        return None
    _emit_thread_detail(runtime, payload, action_label="thread_read")
    summary_text = str(payload.get("summary_text") or "").strip()
    if summary_text:
        runtime.emit_meta(f"thread_summary_turns: {payload.get('summary_turn_count') or 0}")
        for line in summary_text.splitlines()[:4]:
            runtime.emit_meta(f"thread_summary: {line}")
    recent_turns = list(payload.get("turns") or [])
    for row in recent_turns[-3:]:
        role = str(row.get("role") or "turn").strip() or "turn"
        content = " ".join(str(row.get("content") or "").split())
        if len(content) > 96:
            content = content[:93] + "..."
        runtime.emit_meta(f"thread_turn: {role}: {content or '-'}")
    return None


def handle_compact_thread_action(runtime: ChatActionRuntime) -> None:
    payload = compact_session_thread(settings=runtime.settings, state=runtime.state)
    source_turn_count = int(payload.get("source_turn_count") or 0)
    runtime.emit_meta(f"thread compacted: {runtime.state.thread_id or 'unset'}")
    runtime.emit_meta(f"summary_turns: {source_turn_count}")
    return None


def handle_fork_thread_action(runtime: ChatActionRuntime) -> None:
    source_thread_id = str(getattr(runtime.state, "thread_id", None) or "").strip() or None
    thread_id = fork_session_thread(settings=runtime.settings, state=runtime.state, source_thread_id=source_thread_id)
    runtime.emit_meta(
        f"forked thread: {thread_id}",
        session_patch=mirror_session_state_patch(runtime.state, thread_id=thread_id),
    )
    if source_thread_id:
        runtime.emit_meta(f"forked_from: {source_thread_id}")
    return None


def handle_archive_thread_action(runtime: ChatActionRuntime) -> None:
    payload = archive_session_thread(
        settings=runtime.settings,
        state=runtime.state,
        thread_id=str(runtime.parsed.get("thread_id") or "").strip() or None,
    )
    if payload is None:
        runtime.emit_warning("thread_id is required and must exist")
        return None
    runtime.emit_meta(f"thread_archived: {payload['thread_id']}")
    runtime.emit_meta(f"thread_archived_at: {payload['archived_at']}")
    if payload.get("next_thread_id"):
        runtime.emit_meta(
            f"thread_switched_to: {payload['next_thread_id']}",
            session_patch=build_new_thread_session_patch(thread_id=str(payload["next_thread_id"])),
        )
    return None


def handle_unarchive_thread_action(runtime: ChatActionRuntime) -> None:
    thread_id = str(runtime.parsed.get("thread_id") or "").strip()
    payload = unarchive_session_thread(settings=runtime.settings, thread_id=thread_id)
    if payload is None:
        runtime.emit_warning("thread_id is required and must exist")
        return None
    runtime.emit_meta(f"thread_unarchived: {payload['thread_id']}")
    runtime.emit_meta(f"thread_unarchived_at: {payload['unarchived_at']}")
    return None


def handle_set_thread_action(runtime: ChatActionRuntime) -> None:
    next_thread_id = str(runtime.parsed.get("thread_id") or "").strip()
    if not next_thread_id:
        runtime.emit_warning("thread_id is required")
        return None
    payload = resume_session_thread(settings=runtime.settings, state=runtime.state, thread_id=next_thread_id)
    if payload is None:
        runtime.emit_warning(f"thread_id does not exist: {next_thread_id}")
        return None
    runtime.emit_meta(
        f"thread_resumed: {next_thread_id}",
        session_patch=mirror_session_state_patch(runtime.state, thread_id=next_thread_id),
    )
    if payload.get("previous_thread_id") and payload.get("previous_thread_id") != next_thread_id:
        runtime.emit_meta(f"thread_resumed_from: {payload['previous_thread_id']}")
    if payload.get("thread_label"):
        runtime.emit_meta(f"thread_name: {payload['thread_label']}")
    runtime.emit_meta(f"thread_checkpointed: {'yes' if payload.get('has_checkpoint') else 'no'}")
    runtime.emit_meta(f"thread_compacted: {'yes' if payload.get('has_compact_summary') else 'no'}")
    return None


def _emit_thread_detail(runtime: ChatActionRuntime, payload: dict, *, action_label: str) -> None:
    runtime.emit_meta(f"{action_label}: {payload['thread_id']}")
    if payload.get("thread_label"):
        runtime.emit_meta(f"thread_name: {payload['thread_label']}")
    runtime.emit_meta(f"thread_turns: {payload.get('turn_count') or 0}")
    runtime.emit_meta(f"thread_lifetime_turns: {payload.get('lifetime_turn_count') or 0}")
    runtime.emit_meta(f"thread_source: {payload.get('thread_source') or 'new'}")
    runtime.emit_meta(f"thread_archived: {'yes' if payload.get('archived') else 'no'}")
    if payload.get("archived_at"):
        runtime.emit_meta(f"thread_archived_at: {payload.get('archived_at')}")
    if payload.get("parent_thread_id"):
        runtime.emit_meta(f"thread_parent: {payload.get('parent_thread_id')}")
    if payload.get("fork_turn_count"):
        runtime.emit_meta(f"thread_fork_turns: {payload.get('fork_turn_count')}")
    if payload.get("topic_title"):
        runtime.emit_meta(f"thread_topic: {payload.get('topic_title')}")
    if payload.get("topic_intent"):
        runtime.emit_meta(f"thread_intent: {payload.get('topic_intent')}")
    runtime.emit_meta(f"thread_compacted: {'yes' if payload.get('has_compact_summary') else 'no'}")
    runtime.emit_meta(f"thread_checkpointed: {'yes' if payload.get('has_checkpoint') else 'no'}")
