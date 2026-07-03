from __future__ import annotations

from research_system.cli.rendering.chat_rendering_output_refresh import refresh_output_area
from research_system.cli.rendering.chat_rendering_status_support import (
    build_tui_hint_text as support_build_tui_hint_text,
    build_tui_status_text as support_build_tui_status_text,
)
from research_system.cli.rendering.chat_rendering_text_support import (
    commands_popup_text as support_commands_popup_text,
    render_tui_output as support_render_tui_output,
)
from research_system.cli.transcript.chat_transcript import TranscriptMessage
from research_system.cli.state.chat_types import TuiSession
from research_system.cli.view.chat_view import sync_chat_view_from_scene


def commands_popup_text() -> str:
    return support_commands_popup_text()


def render_tui_output(lines: list[TranscriptMessage]) -> str:
    return support_render_tui_output(lines)


def build_tui_status_text(session: TuiSession) -> list[tuple[str, str]]:
    return support_build_tui_status_text(session)


def build_tui_hint_text(session: TuiSession) -> list[tuple[str, str]]:
    return support_build_tui_hint_text(session)


def refresh_tui(session: TuiSession) -> None:
    refresh_output_area(session)
    sync_chat_view_from_scene(session)
    if session.app is not None:
        session.app.invalidate()
