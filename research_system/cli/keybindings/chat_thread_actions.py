from __future__ import annotations

from research_system.cli.keybindings.chat_thread_state import (
    ThreadPanelRuntime,
    focused_thread_row,
    stabilize_thread_focus,
)


def switch_to_focused_thread(runtime: ThreadPanelRuntime) -> bool:
    if runtime.session.output_panel != "threads":
        return False
    row = focused_thread_row(runtime)
    if row is None:
        runtime.emit_warning_update(runtime.session, "No focused thread. Use F8 to focus a thread first.")
        return True
    thread_id = str(row.get("thread_id") or "").strip()
    if not thread_id:
        runtime.emit_warning_update(runtime.session, "Focused thread is missing a thread_id.")
        return True
    payload = runtime.resume_thread(settings=runtime.session.settings, state=runtime.session.state, thread_id=thread_id)
    if payload is None:
        runtime.emit_warning_update(runtime.session, f"Failed to resume thread {thread_id}.")
        return True
    stabilize_thread_focus(runtime, preferred_thread_id=thread_id)
    runtime.session.follow_output = False
    runtime.emit_meta(f"thread_resumed: {thread_id}")
    runtime.emit_meta(f"thread_checkpointed: {'yes' if payload.get('has_checkpoint') else 'no'}")
    runtime.emit_meta(f"thread_compacted: {'yes' if payload.get('has_compact_summary') else 'no'}")
    runtime.invalidate()
    return True


def fork_focused_thread(runtime: ThreadPanelRuntime) -> None:
    if runtime.session.output_panel != "threads":
        return
    row = focused_thread_row(runtime)
    if row is None:
        runtime.emit_warning_update(runtime.session, "No focused thread. Use F8 to focus a thread first.")
        return
    source_thread_id = str(row.get("thread_id") or "").strip()
    if not source_thread_id:
        runtime.emit_warning_update(runtime.session, "Focused thread is missing a thread_id.")
        return
    child_thread_id = runtime.fork_thread(
        settings=runtime.session.settings,
        state=runtime.session.state,
        source_thread_id=source_thread_id,
    )
    runtime.session.thread_panel_scope = "active"
    stabilize_thread_focus(runtime, preferred_thread_id=child_thread_id)
    runtime.session.follow_output = False
    runtime.move_output_cursor(runtime.session, absolute="start")
    runtime.emit_meta(f"forked thread: {child_thread_id}")
    runtime.emit_meta(f"forked_from: {source_thread_id}")


def toggle_focused_thread_archive_state(runtime: ThreadPanelRuntime) -> None:
    if runtime.session.output_panel != "threads":
        return
    row = focused_thread_row(runtime)
    if row is None:
        runtime.emit_warning_update(runtime.session, "No focused thread. Use F8 to focus a thread first.")
        return
    thread_id = str(row.get("thread_id") or "").strip()
    if not thread_id:
        runtime.emit_warning_update(runtime.session, "Focused thread is missing a thread_id.")
        return
    if bool(row.get("archived")):
        payload = runtime.unarchive_thread(settings=runtime.session.settings, thread_id=thread_id)
        if payload is None:
            runtime.emit_warning_update(runtime.session, f"Failed to unarchive thread {thread_id}.")
            return
        runtime.emit_meta(f"thread_unarchived: {payload['thread_id']}")
        runtime.emit_meta(f"thread_unarchived_at: {payload['unarchived_at']}")
        stabilize_thread_focus(runtime, preferred_thread_id=thread_id if runtime.session.thread_panel_scope != "archived" else None)
    else:
        payload = runtime.archive_thread(settings=runtime.session.settings, state=runtime.session.state, thread_id=thread_id)
        if payload is None:
            runtime.emit_warning_update(runtime.session, f"Failed to archive thread {thread_id}.")
            return
        runtime.emit_meta(f"thread_archived: {payload['thread_id']}")
        runtime.emit_meta(f"thread_archived_at: {payload['archived_at']}")
        next_thread_id = str(payload.get("next_thread_id") or "").strip() or None
        if next_thread_id:
            runtime.emit_meta(f"thread_switched_to: {next_thread_id}")
        preferred_thread_id = thread_id if runtime.session.thread_panel_scope == "all" else next_thread_id
        stabilize_thread_focus(runtime, preferred_thread_id=preferred_thread_id)
    runtime.session.follow_output = False
    runtime.move_output_cursor(runtime.session, absolute="start")
