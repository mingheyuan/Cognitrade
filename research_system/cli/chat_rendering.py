from __future__ import annotations

from research_system.cli.chat_scene import (
    build_commands_popup_view,
    build_hint_bar_view,
    build_output_blocks,
    build_shell_scene_view,
    build_status_bar_items,
    build_tui_blocks,
)
from research_system.cli.chat_state import apply_session_patch_and_checkpoint, has_session_state_patch
from research_system.cli.chat_transcript import TranscriptMessage
from research_system.cli.chat_transcript_store import clear_stream_slot, record_transcript_event
from research_system.cli.chat_types import TuiSession
from research_system.cli.chat_view import sync_chat_view_from_scene
from research_system.cli.chat_updates import (
    SESSION_PATCH_UNSET,
    SessionStatePatch,
    ShellStatePatch,
    TranscriptUpdate,
    default_update_for_text,
)

MAX_TRANSCRIPT_MESSAGES = 2000


def commands_popup_text() -> str:
    return "\n".join(build_commands_popup_view().lines)


def render_tui_output(lines: list[TranscriptMessage]) -> str:
    rendered: list[str] = []
    for block in build_tui_blocks(lines):
        if block.inline:
            if rendered and rendered[-1] != "":
                rendered.append("")
            rendered.extend(block.lines)
            continue
        if rendered and rendered[-1] != "":
            rendered.append("")
        if block.title is None:
            rendered.extend(block.lines)
            continue
        rendered.append(f"[{block.title}]")
        rendered.extend(block.lines)
    return "\n".join(rendered)
def build_tui_status_text(session: TuiSession) -> list[tuple[str, str]]:
    return [
        ("class:status", f" {item.label}: {item.value} ")
        for item in build_status_bar_items(session)
    ]


def build_tui_hint_text(session: TuiSession) -> list[tuple[str, str]]:
    return [("class:hint", f" {build_hint_bar_view(session).text} ")]


def _cursor_index_for_line(text: str, line_index: int) -> int:
    if line_index <= 0:
        return 0
    current_line = 0
    for index, char in enumerate(text):
        if char == "\n":
            current_line += 1
            if current_line >= line_index:
                return index + 1
    return len(text)


def refresh_tui(session: TuiSession) -> None:
    if session.output_area is not None:
        output_window = getattr(session.output_area, "window", None)
        prior_cursor = (
            getattr(session.output_area.buffer, "cursor_position", 0)
            if hasattr(session.output_area, "buffer")
            else 0
        )
        blocks = tuple(build_output_blocks(session))
        if hasattr(session.output_area, "set_blocks"):
            text_changed = bool(session.output_area.set_blocks(blocks))
            session.ui_state.last_rendered_output_text = getattr(session.output_area, "text", "")
        else:
            rendered_output = render_tui_output(session.output_lines)
            text_changed = rendered_output != session.ui_state.last_rendered_output_text
            if text_changed:
                session.output_area.text = rendered_output
                session.ui_state.last_rendered_output_text = rendered_output
        if hasattr(session.output_area, "buffer"):
            if session.follow_output:
                session.output_area.buffer.cursor_position = len(session.output_area.text)
                if output_window is not None:
                    output_window.vertical_scroll = 0
                    output_window.vertical_scroll_2 = 0
            elif text_changed:
                session.output_area.buffer.cursor_position = min(prior_cursor, len(session.output_area.text))
    sync_chat_view_from_scene(session)
    if session.app is not None:
        session.app.invalidate()
def apply_tui_update(session: TuiSession, update: TranscriptUpdate) -> None:
    if update.action == "reset_turn":
        session.answer_stream_open = False
        from research_system.cli.chat_transcript_store import reset_turn_scoped_slots

        reset_turn_scoped_slots(session)
        apply_shell_state_patch(session, update.state_patch)
        apply_session_state_patch(session, update.session_patch)
        refresh_tui(session)
        return
    if update.action == "stream_done":
        session.answer_stream_open = False
        clear_stream_slot(session, update.slot_key or "answer_stream")
        apply_shell_state_patch(session, update.state_patch)
        apply_session_state_patch(session, update.session_patch)
        refresh_tui(session)
        return
    if update.action == "stream_append":
        record_transcript_event(session, kind=update.kind, text=update.text)
        session.answer_stream_open = True
        apply_shell_state_patch(session, update.state_patch)
        apply_session_state_patch(session, update.session_patch)
        session.output_lines = session.output_lines[-MAX_TRANSCRIPT_MESSAGES:]
        refresh_tui(session)
        return
    if update.kind is None:
        apply_shell_state_patch(session, update.state_patch)
        apply_session_state_patch(session, update.session_patch)
        refresh_tui(session)
        return
    session.answer_stream_open = False
    if has_shell_state_patch(update.state_patch):
        apply_shell_state_patch(session, update.state_patch)
    if has_session_state_patch(update.session_patch):
        apply_session_state_patch(session, update.session_patch)
    record_transcript_event(session, kind=update.kind, text=update.text)
    session.output_lines = session.output_lines[-MAX_TRANSCRIPT_MESSAGES:]
    refresh_tui(session)


def apply_shell_state_patch(session: TuiSession, patch: ShellStatePatch) -> None:
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
            patch.answer_stream_open,
        )
    )
