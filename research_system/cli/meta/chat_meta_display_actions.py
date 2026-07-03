from __future__ import annotations

from research_system.cli.runtime.chat_action_runtime import ChatActionRuntime
from research_system.cli.memory.chat_memory import (
    current_thread_summary,
    has_recent_chat_memory,
    hydrate_session_from_persistent_memory,
)
from research_system.cli.state.chat_state import build_show_last_session_patch
from research_system.cli.state.chat_updates import ShellStatePatch


def handle_show_thread_action(runtime: ChatActionRuntime) -> None:
    summary = current_thread_summary(settings=runtime.settings, state=runtime.state)
    runtime.emit_meta(f"thread_id: {summary.get('thread_id') or 'unset'}")
    if summary.get("thread_label"):
        runtime.emit_meta(f"thread_name: {summary.get('thread_label')}")
    runtime.emit_meta(f"thread_turns: {summary.get('turn_count') or 0}")
    runtime.emit_meta(f"thread_lifetime_turns: {summary.get('lifetime_turn_count') or 0}")
    runtime.emit_meta(f"thread_source: {summary.get('thread_source') or 'new'}")
    runtime.emit_meta(f"thread_archived: {'yes' if summary.get('archived') else 'no'}")
    if summary.get("archived_at"):
        runtime.emit_meta(f"thread_archived_at: {summary.get('archived_at')}")
    if summary.get("parent_thread_id"):
        runtime.emit_meta(f"thread_parent: {summary.get('parent_thread_id')}")
    if summary.get("fork_turn_count"):
        runtime.emit_meta(f"thread_fork_turns: {summary.get('fork_turn_count')}")
    if summary.get("topic_title"):
        runtime.emit_meta(f"thread_topic: {summary.get('topic_title')}")
    if summary.get("topic_intent"):
        runtime.emit_meta(f"thread_intent: {summary.get('topic_intent')}")
    if summary.get("thread_status"):
        runtime.emit_meta(f"thread_status: {summary.get('thread_status')}")
    if summary.get("open_question"):
        runtime.emit_meta(f"thread_open_question: {summary.get('open_question')}")
    if summary.get("recommended_next_step"):
        runtime.emit_meta(f"thread_next_step: {summary.get('recommended_next_step')}")
    runtime.emit_meta(f"thread_compacted: {'yes' if summary.get('has_compact_summary') else 'no'}")
    runtime.emit_meta(f"thread_checkpointed: {'yes' if summary.get('has_checkpoint') else 'no'}")
    return None


def handle_show_last_action(runtime: ChatActionRuntime) -> None:
    if not runtime.state.last_run_id and not has_recent_chat_memory(runtime.state):
        hydrate_session_from_persistent_memory(settings=runtime.settings, state=runtime.state)
    run_id = runtime.state.last_run_id
    runtime.emit_meta(
        f"last_run_id: {run_id or 'unset'}",
        state_patch=ShellStatePatch(current_run_id=run_id),
        session_patch=build_show_last_session_patch(runtime.state),
    )
    runtime.emit_meta(f"intent: {runtime.state.current_intent or 'auto'}")
    runtime.emit_meta(f"review_target_run_id: {runtime.state.review_target_run_id or 'unset'}")
    runtime.emit_meta(f"thread_id: {runtime.state.thread_id or 'unset'}")
    runtime.emit_meta(f"thinking: {'on' if runtime.state.thinking_enabled else 'off'}")
    runtime.emit_meta(f"events: {runtime.state.events_mode}")
    return None
