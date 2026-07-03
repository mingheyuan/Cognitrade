from __future__ import annotations

from research_system.cli.inspect.chat_inspect_counts_support import (
    events_rows_for_session,
    trace_task_count_for_session,
)
from research_system.cli.inspect.chat_inspect_payload_support import (
    load_events_payload_for_session,
    load_trace_payload_for_session,
)
from research_system.cli.state.chat_types import TuiSession


def load_events_panel_for_session(
    session: TuiSession,
    *,
    execute_cli_command,
    emit_warning_update,
    invalidate,
) -> None:
    run_id = session.current_run_id or session.state.last_run_id
    load_events_payload_for_session(
        session,
        run_id=run_id,
        execute_cli_command=execute_cli_command,
        emit_warning_update=emit_warning_update,
    )
    session.output_panel = "events"
    if session.events_focus_index is None:
        sliced_rows = events_rows_for_session(session)
        if sliced_rows:
            session.events_focus_index = 0
    session.follow_output = False
    invalidate()


def sync_events_focus_for_session(session: TuiSession, *, reset_to_first: bool = False) -> None:
    if session.output_panel != "events":
        return
    rows = events_rows_for_session(session)
    if not rows:
        session.events_focus_index = None
        return
    current_index = session.events_focus_index
    if current_index is None or current_index >= len(rows) or reset_to_first:
        session.events_focus_index = 0


def toggle_output_panel_for_session(
    session: TuiSession,
    *,
    execute_cli_command,
    emit_warning_update,
    move_output_cursor,
    invalidate,
) -> None:
    if session.output_panel == "trace":
        session.output_panel = "transcript"
        session.trace_focus_index = None
        session.trace_child_focus_index = None
        session.follow_output = session.output_mode == "live"
        if session.output_mode == "live":
            move_output_cursor(session, absolute="end")
        else:
            invalidate()
        return
    if session.output_panel == "events":
        session.output_panel = "threads"
        session.follow_output = False
        invalidate()
        return
    if session.output_panel == "threads":
        session.output_panel = "trace"
        session.follow_output = False
        invalidate()
        return
    run_id = session.current_run_id or session.state.last_run_id
    load_trace_payload_for_session(
        session,
        run_id=run_id,
        execute_cli_command=execute_cli_command,
        emit_warning_update=emit_warning_update,
    )
    session.output_panel = "trace"
    if trace_task_count_for_session(session) <= 0:
        session.trace_focus_index = None
        session.trace_child_focus_index = None
    session.follow_output = False
    invalidate()


def toggle_trace_threads_panel_for_session(
    session: TuiSession,
    *,
    execute_cli_command,
    emit_warning_update,
    invalidate,
) -> None:
    if session.output_panel == "trace":
        load_events_panel_for_session(
            session,
            execute_cli_command=execute_cli_command,
            emit_warning_update=emit_warning_update,
            invalidate=invalidate,
        )
        return
    if session.output_panel == "events":
        session.output_panel = "threads"
        session.follow_output = False
        invalidate()
        return
    if session.output_panel == "threads":
        session.output_panel = "trace"
        session.follow_output = False
        invalidate()
