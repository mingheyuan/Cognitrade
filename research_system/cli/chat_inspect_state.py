from __future__ import annotations

from dataclasses import dataclass

from research_system.cli.chat_inspect_counts_support import (
    events_rows_for_session,
    last_matching_event_index as support_last_matching_event_index,
    trace_branch_paths_for_session,
    trace_child_count_for_session,
    trace_groups_for_session,
    trace_task_count_for_session,
)
from research_system.cli.chat_inspect_panel_support import (
    load_events_panel_for_session,
    sync_events_focus_for_session,
    toggle_output_panel_for_session,
    toggle_trace_threads_panel_for_session,
)
from research_system.cli.chat_inspect_payload_support import (
    load_events_payload_for_session,
    load_trace_payload_for_session,
)
from research_system.cli.chat_types import TuiSession


@dataclass(frozen=True)
class InspectPanelRuntime:
    session: TuiSession
    execute_cli_command: object
    emit_warning_update: object
    move_output_cursor: object

    def invalidate(self) -> None:
        if self.session.app is not None:
            self.session.app.invalidate()


def build_inspect_panel_runtime(
    *,
    session: TuiSession,
    execute_cli_command,
    emit_warning_update,
    move_output_cursor,
) -> InspectPanelRuntime:
    return InspectPanelRuntime(
        session=session,
        execute_cli_command=execute_cli_command,
        emit_warning_update=emit_warning_update,
        move_output_cursor=move_output_cursor,
    )


def events_rows(runtime: InspectPanelRuntime) -> list[dict]:
    return events_rows_for_session(runtime.session)


def last_matching_event_index(
    rows: list[dict],
    *,
    invocation_id: str = "",
    action_id: str = "",
    node_id: str = "",
    phase: str = "",
    agent: str = "",
) -> int | None:
    return support_last_matching_event_index(
        rows,
        invocation_id=invocation_id,
        action_id=action_id,
        node_id=node_id,
        phase=phase,
        agent=agent,
    )


def trace_task_count(runtime: InspectPanelRuntime) -> int:
    return trace_task_count_for_session(runtime.session)


def events_count(runtime: InspectPanelRuntime) -> int:
    return len(events_rows(runtime))


def trace_groups(runtime: InspectPanelRuntime) -> list[dict]:
    return trace_groups_for_session(runtime.session)


def load_trace_payload_for_run(runtime: InspectPanelRuntime, run_id: str | None) -> dict | None:
    return load_trace_payload_for_session(
        runtime.session,
        run_id=run_id,
        execute_cli_command=runtime.execute_cli_command,
        emit_warning_update=runtime.emit_warning_update,
    )


def load_events_payload_for_run(runtime: InspectPanelRuntime, run_id: str | None) -> dict | None:
    return load_events_payload_for_session(
        runtime.session,
        run_id=run_id,
        execute_cli_command=runtime.execute_cli_command,
        emit_warning_update=runtime.emit_warning_update,
    )


def load_events_panel(runtime: InspectPanelRuntime) -> None:
    load_events_panel_for_session(
        runtime.session,
        execute_cli_command=runtime.execute_cli_command,
        emit_warning_update=runtime.emit_warning_update,
        invalidate=runtime.invalidate,
    )


def sync_events_focus_with_current_slice(runtime: InspectPanelRuntime, *, reset_to_first: bool = False) -> None:
    sync_events_focus_for_session(runtime.session, reset_to_first=reset_to_first)


def trace_child_count(runtime: InspectPanelRuntime) -> int:
    return trace_child_count_for_session(runtime.session)


def trace_branch_paths(runtime: InspectPanelRuntime) -> list[tuple[str, ...]]:
    return trace_branch_paths_for_session(runtime.session)


def toggle_output_panel(runtime: InspectPanelRuntime) -> None:
    toggle_output_panel_for_session(
        runtime.session,
        execute_cli_command=runtime.execute_cli_command,
        emit_warning_update=runtime.emit_warning_update,
        move_output_cursor=runtime.move_output_cursor,
        invalidate=runtime.invalidate,
    )


def toggle_trace_threads_panel(runtime: InspectPanelRuntime) -> None:
    toggle_trace_threads_panel_for_session(
        runtime.session,
        execute_cli_command=runtime.execute_cli_command,
        emit_warning_update=runtime.emit_warning_update,
        invalidate=runtime.invalidate,
    )
