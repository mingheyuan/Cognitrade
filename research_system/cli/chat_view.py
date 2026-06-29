from __future__ import annotations

from dataclasses import dataclass

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.layout import Float, FloatContainer, HSplit, Layout, Window
from prompt_toolkit.layout.containers import ConditionalContainer, to_container
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea
from prompt_toolkit.filters import to_filter

from research_system.cli.chat_commands import ChatCommandCompleter
from research_system.cli.chat_scene import build_shell_scene_view
from research_system.cli.chat_types import TuiSession
from research_system.cli.windows_terminal import create_terminal_io


@dataclass(frozen=True)
class ChatViewComponents:
    header_bar: Window
    output_area: TextArea
    commands_popup: TextArea
    commands_popup_float: Float
    input_area: TextArea
    status_bar: Window
    hint_bar: Window
    main_stack: HSplit
    root_container: FloatContainer


def build_chat_view(session: TuiSession) -> ChatViewComponents:
    def _scene():
        return build_shell_scene_view(session)

    scene = _scene()
    header_control = FormattedTextControl(
        lambda: [
            ("class:header.brand", f" { _scene().header.brand} "),
            ("class:header.sep", "  "),
            ("class:header.title", _scene().header.title),
            ("class:header.sep", "  "),
            ("class:header.mode", f"[{_scene().header.mode_tag}]"),
            ("class:header.sep", "  "),
            ("class:header.subtitle", _scene().header.subtitle),
        ]
    )
    header_bar = Window(content=header_control, height=1)
    output_area = TextArea(
        text="",
        read_only=True,
        scrollbar=True,
        focusable=True,
        focus_on_click=True,
        wrap_lines=True,
        style="class:output",
    )
    commands_popup = TextArea(
        text="\n".join(scene.commands_popup.lines),
        read_only=True,
        focusable=True,
        scrollbar=True,
        wrap_lines=False,
        style="class:popup",
    )
    prompt_processor = BeforeInput(scene.input_bar.prompt, "class:text-area.prompt")
    input_area = TextArea(
        height=scene.input_bar.height,
        prompt="",
        multiline=scene.input_bar.multiline,
        wrap_lines=True,
        accept_handler=lambda buffer: True,
        history=InMemoryHistory(),
        completer=ChatCommandCompleter(session),
        complete_while_typing=True,
        input_processors=[prompt_processor],
        style="class:input",
    )
    status_control = FormattedTextControl(
        lambda: [("class:status", f" {item.label} {item.value} ") for item in _scene().status_items]
    )
    status_bar = Window(content=status_control, height=scene.status_bar_height)
    hint_control = FormattedTextControl(lambda: [("class:hint", f" {_scene().hint.text} ")])
    hint_bar = Window(content=hint_control, height=scene.hint_bar_height)
    commands_popup_float = Float(
        top=scene.popup_layout.top,
        left=scene.popup_layout.left,
        right=scene.popup_layout.right,
        bottom=scene.popup_layout.bottom,
        content=ConditionalContainer(
            content=commands_popup,
            filter=Condition(lambda: _scene().commands_popup_visible),
        ),
    )
    main_stack = HSplit(
        [
            to_container(header_bar),
            *[
                to_container(child)
                for child in build_layout_children(scene, output_area, status_bar, hint_bar, input_area)
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


def build_chat_application(*, session: TuiSession, view: ChatViewComponents, key_bindings) -> Application:
    focus_target = build_shell_scene_view(session).input_bar.focus_target
    focused_element = view.commands_popup if focus_target == "commands_popup" else view.input_area
    input_obj, output_obj = create_terminal_io()
    return Application(
        layout=Layout(view.root_container, focused_element=focused_element),
        key_bindings=key_bindings,
        full_screen=True,
        mouse_support=True,
        input=input_obj,
        output=output_obj,
        style=Style.from_dict(
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
                "input": "bg:#11181d #f1f5f6",
                "text-area.prompt": "#ffcf66 bold",
            }
        ),
    )


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


def sync_chat_view_from_scene(session: TuiSession) -> None:
    scene = build_shell_scene_view(session)
    scene_signature = (
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
    view_changed = session.ui_state.last_view_sync_signature != scene_signature
    if session.main_stack is not None and view_changed:
        body_children = build_layout_children(
            scene,
            session.output_area,
            session.status_bar,
            session.input_hint_bar,
            session.input_area,
        )
        header = session.main_stack.children[0] if session.main_stack.children else None
        if header is not None:
            session.main_stack.children = [to_container(header), *[to_container(child) for child in body_children]]
        else:
            session.main_stack.children = [to_container(child) for child in body_children]
    if session.input_area is not None and view_changed:
        if hasattr(session.input_area, "window"):
            session.input_area.window.height = scene.input_bar.height
        control = getattr(session.input_area, "control", None)
        processors = getattr(control, "input_processors", []) if control is not None else []
        for processor in processors:
            if hasattr(processor, "text"):
                processor.text = scene.input_bar.prompt
        if hasattr(session.input_area, "buffer") and hasattr(session.input_area.buffer, "multiline"):
            session.input_area.buffer.multiline = to_filter(scene.input_bar.multiline)
    if session.status_bar is not None and view_changed:
        session.status_bar.height = scene.status_bar_height
    if session.input_hint_bar is not None and view_changed:
        session.input_hint_bar.height = scene.hint_bar_height
    if session.commands_popup is not None and view_changed:
        session.commands_popup.text = "\n".join(scene.commands_popup.lines)
    if session.commands_popup_float is not None and view_changed:
        session.commands_popup_float.top = scene.popup_layout.top
        session.commands_popup_float.left = scene.popup_layout.left
        session.commands_popup_float.right = scene.popup_layout.right
        session.commands_popup_float.bottom = scene.popup_layout.bottom
    app_layout = getattr(session.app, "layout", None) if session.app is not None else None
    if app_layout is not None and session.ui_state.last_focus_target != session.focus_target:
        try:
            if session.focus_target == "commands_popup" and getattr(session, "commands_popup", None) is not None:
                app_layout.focus(session.commands_popup)
            elif session.input_area is not None:
                app_layout.focus(session.input_area)
        except Exception:  # pragma: no cover - prompt_toolkit focus fallback
            pass
        session.ui_state.last_focus_target = session.focus_target
    session.ui_state.last_view_sync_signature = scene_signature
