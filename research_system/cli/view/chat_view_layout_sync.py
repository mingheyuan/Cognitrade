from __future__ import annotations

from prompt_toolkit.layout.containers import to_container

from research_system.cli.view.chat_view_components import build_layout_children


def sync_main_layout(session, scene) -> None:
    if session.main_stack is None:
        return
    body_children = build_layout_children(
        scene,
        session.output_area.window if session.output_area is not None else None,
        session.status_bar,
        session.input_hint_bar,
        session.input_area,
    )
    header = session.main_stack.children[0] if session.main_stack.children else None
    if header is not None:
        session.main_stack.children = [to_container(header), *[to_container(child) for child in body_children]]
    else:
        session.main_stack.children = [to_container(child) for child in body_children]

