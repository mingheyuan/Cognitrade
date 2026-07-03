from __future__ import annotations

from dataclasses import dataclass

from prompt_toolkit.layout import Float, FloatContainer, HSplit, Window
from prompt_toolkit.widgets import TextArea

from research_system.cli.chat_output_area import TranscriptOutputArea


@dataclass(frozen=True)
class ChatViewComponents:
    header_bar: Window
    output_area: TranscriptOutputArea
    commands_popup: TextArea
    commands_popup_float: Float
    input_area: TextArea
    status_bar: Window
    hint_bar: Window
    main_stack: HSplit
    root_container: FloatContainer
