from __future__ import annotations


def build_scene_sync_signature(scene) -> tuple:
    return (
        scene.layout_mode,
        scene.input_bar.prompt,
        scene.input_bar.height,
        scene.input_bar.multiline,
        scene.hint_bar_height,
        scene.status_bar_height,
        scene.commands_popup_visible,
        scene.popup_layout.top,
        scene.popup_layout.left,
        scene.popup_layout.right,
        scene.popup_layout.bottom,
        tuple(scene.commands_popup.lines),
        tuple((item.label, item.value) for item in scene.status_items),
        scene.hint.text,
        scene.header.title,
        scene.header.subtitle,
        scene.header.mode_tag,
    )

