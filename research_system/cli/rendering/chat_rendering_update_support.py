from __future__ import annotations

from collections.abc import Callable

from research_system.cli.state.chat_types import TuiSession
from research_system.cli.state.chat_updates import SessionStatePatch, ShellStatePatch, TranscriptUpdate


def apply_tui_update(
    session: TuiSession,
    update: TranscriptUpdate,
    *,
    max_transcript_messages: int,
    reset_turn_scoped_slots_fn: Callable[[TuiSession], None],
    clear_stream_slot_fn: Callable[[TuiSession, str], None],
    record_transcript_event_fn: Callable[[TuiSession], Callable[..., None]] | Callable[..., None],
    apply_shell_state_patch_fn: Callable[[TuiSession, ShellStatePatch], None],
    apply_session_state_patch_fn: Callable[[TuiSession, SessionStatePatch], None],
    has_shell_state_patch_fn: Callable[[ShellStatePatch], bool],
    has_session_state_patch_fn: Callable[[SessionStatePatch], bool],
    refresh_tui_fn: Callable[[TuiSession], None],
) -> None:
    if update.action == "reset_turn":
        session.answer_stream_open = False
        reset_turn_scoped_slots_fn(session)
        apply_shell_state_patch_fn(session, update.state_patch)
        apply_session_state_patch_fn(session, update.session_patch)
        refresh_tui_fn(session)
        return
    if update.action == "stream_done":
        session.answer_stream_open = False
        clear_stream_slot_fn(session, update.slot_key or "answer_stream")
        apply_shell_state_patch_fn(session, update.state_patch)
        apply_session_state_patch_fn(session, update.session_patch)
        refresh_tui_fn(session)
        return
    if update.action == "stream_append":
        record_transcript_event_fn(session, kind=update.kind, text=update.text)
        session.answer_stream_open = True
        apply_shell_state_patch_fn(session, update.state_patch)
        apply_session_state_patch_fn(session, update.session_patch)
        _trim_output_lines(session, max_transcript_messages)
        refresh_tui_fn(session)
        return
    if update.kind is None:
        apply_shell_state_patch_fn(session, update.state_patch)
        apply_session_state_patch_fn(session, update.session_patch)
        refresh_tui_fn(session)
        return
    session.answer_stream_open = False
    if has_shell_state_patch_fn(update.state_patch):
        apply_shell_state_patch_fn(session, update.state_patch)
    if has_session_state_patch_fn(update.session_patch):
        apply_session_state_patch_fn(session, update.session_patch)
    record_transcript_event_fn(session, kind=update.kind, text=update.text)
    _trim_output_lines(session, max_transcript_messages)
    refresh_tui_fn(session)


def _trim_output_lines(session: TuiSession, max_transcript_messages: int) -> None:
    session.output_lines = session.output_lines[-max_transcript_messages:]
