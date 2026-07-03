from __future__ import annotations

from prompt_toolkit.filters import Condition
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.layout import Float, Window
from prompt_toolkit.layout.containers import ConditionalContainer
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.widgets import TextArea

from research_system.cli.commands.chat_commands import ChatCommandCompleter


def build_header_bar(scene_provider) -> Window:
    header_control = FormattedTextControl(
        lambda: [
            ("class:header.brand", f" {scene_provider().header.brand} "),
            ("class:header.sep", "  "),
            ("class:header.title", scene_provider().header.title),
            ("class:header.sep", "  "),
            ("class:header.mode", f"[{scene_provider().header.mode_tag}]"),
            ("class:header.sep", "  "),
            ("class:header.subtitle", scene_provider().header.subtitle),
        ]
    )
    return Window(content=header_control, height=1)


def build_commands_popup(scene, scene_provider) -> tuple[TextArea, Float]:
    commands_popup = TextArea(
        text="\n".join(scene.commands_popup.lines),
        read_only=True,
        focusable=True,
        scrollbar=True,
        wrap_lines=False,
        style="class:popup",
    )
    commands_popup_float = Float(
        top=scene.popup_layout.top,
        left=scene.popup_layout.left,
        right=scene.popup_layout.right,
        bottom=scene.popup_layout.bottom,
        content=ConditionalContainer(
            content=commands_popup,
            filter=Condition(lambda: scene_provider().commands_popup_visible),
        ),
    )
    return commands_popup, commands_popup_float


def build_input_area(session, scene) -> TextArea:
    prompt_processor = BeforeInput(scene.input_bar.prompt, "class:text-area.prompt")
    return TextArea(
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


def build_status_bar(scene_provider, scene) -> Window:
    status_control = FormattedTextControl(
        lambda: [("class:status", f" {item.label} {item.value} ") for item in scene_provider().status_items]
    )
    return Window(content=status_control, height=scene.status_bar_height)


def build_hint_bar(scene_provider, scene) -> Window:
    hint_control = FormattedTextControl(lambda: [("class:hint", f" {scene_provider().hint.text} ")])
    return Window(content=hint_control, height=scene.hint_bar_height)

