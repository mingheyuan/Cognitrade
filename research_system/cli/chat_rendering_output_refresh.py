from __future__ import annotations

from research_system.cli.chat_scene import build_output_blocks
from research_system.cli.chat_rendering_text_support import render_tui_output
from research_system.cli.chat_types import TuiSession


def refresh_output_area(session: TuiSession) -> None:
    if session.output_area is None:
        return
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
    if not hasattr(session.output_area, "buffer"):
        return
    if session.follow_output:
        session.output_area.buffer.cursor_position = len(session.output_area.text)
        if output_window is not None:
            output_window.vertical_scroll = 0
            output_window.vertical_scroll_2 = 0
        return
    if text_changed:
        session.output_area.buffer.cursor_position = min(prior_cursor, len(session.output_area.text))
