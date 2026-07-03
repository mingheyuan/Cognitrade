from __future__ import annotations

from prompt_toolkit.layout import Layout
from prompt_toolkit.styles import Style

from research_system.cli.chat_scene import build_shell_scene_view


def build_chat_application_style() -> Style:
    return Style.from_dict(
        {
            "header.brand": "bg:#0b1f17 #8de1b5 bold",
            "header.title": "bg:#0b1f17 #f5f7f5 bold",
            "header.mode": "bg:#0b1f17 #ffd479 bold",
            "header.subtitle": "bg:#0b1f17 #9eb9aa",
            "header.sep": "bg:#0b1f17 #466456",
            "status": "bg:#16242d #d9ecf2",
            "popup": "bg:#132026 #f3f7f8",
            "hint": "bg:#0f1418 #aab7c3",
            "output": "bg:#0b0f12 #e8ecef",
            "output.block_title": "bg:#0b0f12 #a9d0c1 bold",
            "output.block_body": "bg:#0b0f12 #e8ecef",
            "output.inline": "bg:#0b0f12 #b8c6cf",
            "input": "bg:#11181d #f1f5f6",
            "text-area.prompt": "#ffcf66 bold",
        }
    )


def build_chat_application_layout(session, view) -> Layout:
    focus_target = build_shell_scene_view(session).input_bar.focus_target
    focused_element = view.commands_popup if focus_target == "commands_popup" else view.input_area
    return Layout(view.root_container, focused_element=focused_element)
