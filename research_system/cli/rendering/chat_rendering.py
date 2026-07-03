from __future__ import annotations

from research_system.cli.view.chat_scene import build_events_rows_for_session
from research_system.cli.rendering.chat_rendering_patch_support import (
    apply_session_state_patch as patch_apply_session_state_patch,
    apply_shell_state_patch as patch_apply_shell_state_patch,
    has_shell_state_patch as patch_has_shell_state_patch,
    sync_live_events_focus as patch_sync_live_events_focus,
)
from research_system.cli.rendering.chat_rendering_refresh_support import (
    build_tui_hint_text as refresh_build_tui_hint_text,
    build_tui_status_text as refresh_build_tui_status_text,
    commands_popup_text as refresh_commands_popup_text,
    refresh_tui as refresh_refresh_tui,
    render_tui_output as refresh_render_tui_output,
)
from research_system.cli.rendering.chat_rendering_update_support import (
    apply_tui_update as update_apply_tui_update,
)
from research_system.cli.state.chat_state import has_session_state_patch
from research_system.cli.transcript.chat_transcript_store import (
    clear_stream_slot,
    record_transcript_event,
    reset_turn_scoped_slots,
)
from research_system.cli.state.chat_types import TuiSession
from research_system.cli.state.chat_updates import (
    SessionStatePatch,
    ShellStatePatch,
    TranscriptUpdate,
)
from research_system.cli.transcript.chat_transcript import TranscriptMessage

MAX_TRANSCRIPT_MESSAGES = 2000


def _sync_live_events_focus(session: TuiSession) -> None:
    patch_sync_live_events_focus(session, build_events_rows_for_session)


def commands_popup_text() -> str:
    return refresh_commands_popup_text()


def render_tui_output(lines: list[TranscriptMessage]) -> str:
    return refresh_render_tui_output(lines)


def build_tui_status_text(session: TuiSession) -> list[tuple[str, str]]:
    return refresh_build_tui_status_text(session)


def build_tui_hint_text(session: TuiSession) -> list[tuple[str, str]]:
    return refresh_build_tui_hint_text(session)


def refresh_tui(session: TuiSession) -> None:
    refresh_refresh_tui(session)


def apply_tui_update(session: TuiSession, update: TranscriptUpdate) -> None:
    update_apply_tui_update(
        session,
        update,
        max_transcript_messages=MAX_TRANSCRIPT_MESSAGES,
        reset_turn_scoped_slots_fn=reset_turn_scoped_slots,
        clear_stream_slot_fn=clear_stream_slot,
        record_transcript_event_fn=record_transcript_event,
        apply_shell_state_patch_fn=apply_shell_state_patch,
        apply_session_state_patch_fn=apply_session_state_patch,
        has_shell_state_patch_fn=has_shell_state_patch,
        has_session_state_patch_fn=has_session_state_patch,
        refresh_tui_fn=refresh_tui,
    )


def apply_shell_state_patch(session: TuiSession, patch: ShellStatePatch) -> None:
    patch_apply_shell_state_patch(
        session,
        patch,
        sync_live_events_focus_fn=_sync_live_events_focus,
    )


def apply_session_state_patch(session: TuiSession, patch: SessionStatePatch) -> None:
    patch_apply_session_state_patch(session, patch)


def has_shell_state_patch(patch: ShellStatePatch) -> bool:
    return patch_has_shell_state_patch(patch)
