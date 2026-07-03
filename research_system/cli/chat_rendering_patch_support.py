from __future__ import annotations

from research_system.cli.chat_state import apply_session_patch_and_checkpoint
from research_system.cli.chat_types import TuiSession
from research_system.cli.chat_updates import SessionStatePatch, ShellStatePatch


def sync_live_events_focus(session: TuiSession, build_events_rows_for_session) -> None:
    if not isinstance(session.events_payload, dict):
        return
    rows = build_events_rows_for_session(session, respect_output_panel=False)
    if not rows:
        session.events_focus_index = None
        return
    if session.output_panel == "events" or session.trace_focus_index is not None:
        session.events_focus_index = len(rows) - 1


def apply_shell_state_patch(
    session: TuiSession,
    patch: ShellStatePatch,
    *,
    sync_live_events_focus_fn,
) -> None:
    if patch.current_gate is not None:
        session.current_gate = patch.current_gate or session.current_gate
    if patch.current_phase is not None:
        session.current_phase = patch.current_phase or session.current_phase
    if patch.run_status_label is not None:
        session.run_status_label = patch.run_status_label or session.run_status_label
    if patch.current_run_id is not None:
        session.current_run_id = patch.current_run_id or session.current_run_id
    if patch.running is not None:
        session.running = patch.running
    if patch.stop_requested is not None:
        session.stop_requested = patch.stop_requested
    if patch.commands_popup_visible is not None:
        session.commands_popup_visible = patch.commands_popup_visible
        session.help_mode = "commands" if patch.commands_popup_visible else "hidden"
    if patch.focus_target is not None:
        session.focus_target = patch.focus_target
    if patch.help_mode is not None:
        session.help_mode = patch.help_mode
        session.commands_popup_visible = patch.help_mode == "commands"
    if patch.input_mode is not None:
        session.input_mode = patch.input_mode
    if patch.output_mode is not None:
        session.output_mode = patch.output_mode
        session.follow_output = patch.output_mode == "live"
    if patch.output_panel is not None:
        session.output_panel = patch.output_panel
    if patch.trace_payload is not None:
        session.trace_payload = patch.trace_payload
    if patch.events_payload is not None:
        session.events_payload = patch.events_payload
        sync_live_events_focus_fn(session)
    if patch.subagent_payload is not None:
        session.subagent_payload = patch.subagent_payload
    if patch.answer_stream_open is not None:
        session.answer_stream_open = patch.answer_stream_open


def apply_session_state_patch(session: TuiSession, patch: SessionStatePatch) -> None:
    apply_session_patch_and_checkpoint(settings=session.settings, state=session.state, patch=patch)


def has_shell_state_patch(patch: ShellStatePatch) -> bool:
    return any(
        value is not None
        for value in (
            patch.current_run_id,
            patch.current_phase,
            patch.current_gate,
            patch.run_status_label,
            patch.running,
            patch.stop_requested,
            patch.commands_popup_visible,
            patch.focus_target,
            patch.help_mode,
            patch.input_mode,
            patch.output_mode,
            patch.output_panel,
            patch.trace_payload,
            patch.events_payload,
            patch.subagent_payload,
            patch.answer_stream_open,
        )
    )
