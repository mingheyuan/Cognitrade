from __future__ import annotations

from prompt_toolkit.layout import FloatContainer, HSplit
from prompt_toolkit.layout.containers import to_container

from research_system.cli.chat_scene import build_shell_scene_view
from research_system.cli.chat_types import TuiSession
from research_system.cli.chat_view_application_support import (
    build_chat_application_layout,
    build_chat_application_style,
)
from research_system.cli.chat_view_components import (
    ChatViewComponents,
    TranscriptOutputArea,
    build_layout_children,
)
from research_system.cli.chat_view_widget_factory import (
    build_commands_popup,
    build_header_bar,
    build_hint_bar,
    build_input_area,
    build_status_bar,
)


def build_chat_view(session: TuiSession) -> ChatViewComponents:
    def _scene():
        return build_shell_scene_view(session)

    scene = _scene()
    header_bar = build_header_bar(_scene)
    output_area = TranscriptOutputArea()
    commands_popup, commands_popup_float = build_commands_popup(scene, _scene)
    input_area = build_input_area(session, scene)
    status_bar = build_status_bar(_scene, scene)
    hint_bar = build_hint_bar(_scene, scene)
    main_stack = HSplit(
        [
            to_container(header_bar),
            *[
                to_container(child)
                for child in build_layout_children(scene, output_area.window, status_bar, hint_bar, input_area)
            ],
        ]
    )
    root_container = FloatContainer(
        content=main_stack,
        floats=[commands_popup_float],
    )
    return ChatViewComponents(
        header_bar=header_bar,
        output_area=output_area,
        commands_popup=commands_popup,
        commands_popup_float=commands_popup_float,
        input_area=input_area,
        status_bar=status_bar,
        hint_bar=hint_bar,
        main_stack=main_stack,
        root_container=root_container,
    )


def build_chat_application(
    *,
    session: TuiSession,
    view: ChatViewComponents,
    key_bindings,
    application_factory,
    terminal_io_factory,
) -> object:
    input_obj, output_obj = terminal_io_factory()
    return application_factory(
        layout=build_chat_application_layout(session, view),
        key_bindings=key_bindings,
        full_screen=True,
        mouse_support=True,
        input=input_obj,
        output=output_obj,
        style=build_chat_application_style(),
    )
