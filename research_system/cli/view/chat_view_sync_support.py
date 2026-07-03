from __future__ import annotations

from research_system.cli.view.chat_scene import build_shell_scene_view
from research_system.cli.view.chat_view_focus_sync import sync_focus_target
from research_system.cli.view.chat_view_layout_sync import sync_main_layout
from research_system.cli.view.chat_view_sync_signature import build_scene_sync_signature
from research_system.cli.view.chat_view_widget_sync import sync_auxiliary_widgets, sync_input_widget


def sync_chat_view_from_scene(session) -> None:
    scene = build_shell_scene_view(session)
    scene_signature = build_scene_sync_signature(scene)
    view_changed = session.ui_state.last_view_sync_signature != scene_signature
    if view_changed:
        sync_main_layout(session, scene)
        sync_input_widget(session, scene)
        sync_auxiliary_widgets(session, scene)
    sync_focus_target(session)
    session.ui_state.last_view_sync_signature = scene_signature

