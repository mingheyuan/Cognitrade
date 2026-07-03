from __future__ import annotations

from research_system.cli.view.chat_scene import build_hint_bar_view, build_status_bar_items
from research_system.cli.state.chat_types import TuiSession


def build_tui_status_text(session: TuiSession) -> list[tuple[str, str]]:
    return [
        ("class:status", f" {item.label}: {item.value} ")
        for item in build_status_bar_items(session)
    ]


def build_tui_hint_text(session: TuiSession) -> list[tuple[str, str]]:
    return [("class:hint", f" {build_hint_bar_view(session).text} ")]
