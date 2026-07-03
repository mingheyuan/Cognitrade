from __future__ import annotations

from prompt_toolkit.filters import to_filter


def sync_input_widget(session, scene) -> None:
    if session.input_area is None:
        return
    if hasattr(session.input_area, "window"):
        session.input_area.window.height = scene.input_bar.height
    control = getattr(session.input_area, "control", None)
    processors = getattr(control, "input_processors", []) if control is not None else []
    for processor in processors:
        if hasattr(processor, "text"):
            processor.text = scene.input_bar.prompt
    if hasattr(session.input_area, "buffer") and hasattr(session.input_area.buffer, "multiline"):
        session.input_area.buffer.multiline = to_filter(scene.input_bar.multiline)


def sync_auxiliary_widgets(session, scene) -> None:
    if session.status_bar is not None:
        session.status_bar.height = scene.status_bar_height
    if session.input_hint_bar is not None:
        session.input_hint_bar.height = scene.hint_bar_height
    if session.commands_popup is not None:
        session.commands_popup.text = "\n".join(scene.commands_popup.lines)
    if session.commands_popup_float is not None:
        session.commands_popup_float.top = scene.popup_layout.top
        session.commands_popup_float.left = scene.popup_layout.left
        session.commands_popup_float.right = scene.popup_layout.right
        session.commands_popup_float.bottom = scene.popup_layout.bottom

