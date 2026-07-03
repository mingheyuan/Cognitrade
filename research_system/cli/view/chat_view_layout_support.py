from __future__ import annotations


def build_layout_children(scene, output_area, status_bar, hint_bar, input_area):
    children = [output_area]
    if scene.layout_mode == "help":
        children.extend([status_bar, input_area])
    elif scene.layout_mode == "command":
        children.extend([hint_bar, input_area, status_bar])
    elif scene.layout_mode == "running":
        children.extend([status_bar, hint_bar, input_area])
    else:
        children.extend([status_bar, hint_bar, input_area])
    return children

