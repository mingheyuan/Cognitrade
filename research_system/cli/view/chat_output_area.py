from __future__ import annotations

from prompt_toolkit.document import Document
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.layout.screen import Point

from research_system.cli.view.chat_scene import TranscriptRenderBlock


class _TranscriptCursorBuffer:
    def __init__(self, owner: "TranscriptOutputArea") -> None:
        self._owner = owner
        self.cursor_position = 0

    def load_history_if_not_yet_loaded(self) -> None:
        return None

    def cursor_up(self, count: int = 1) -> None:
        self.cursor_position = self._move_rows(-count)

    def cursor_down(self, count: int = 1) -> None:
        self.cursor_position = self._move_rows(count)

    def _move_rows(self, row_delta: int) -> int:
        document = Document(text=self._owner.text, cursor_position=min(self.cursor_position, len(self._owner.text)))
        row = document.cursor_position_row + row_delta
        row = max(0, min(row, max(0, len(document.lines) - 1)))
        col = min(document.cursor_position_col, len(document.lines[row]))
        return document.translate_row_col_to_index(row, col)


class TranscriptOutputArea:
    def __init__(self) -> None:
        self._text = ""
        self._blocks: tuple[TranscriptRenderBlock, ...] = ()
        self._block_texts: list[str] = []
        self._block_fragments: list[list[tuple[str, str]]] = []
        self.buffer = _TranscriptCursorBuffer(self)
        self.control = FormattedTextControl(
            self._formatted_text,
            focusable=True,
            show_cursor=False,
            get_cursor_position=self._cursor_position,
        )
        self.window = Window(
            content=self.control,
            wrap_lines=True,
            right_margins=[ScrollbarMargin(display_arrows=False)],
            allow_scroll_beyond_bottom=True,
            style="class:output",
        )

    @property
    def text(self) -> str:
        return self._text

    @text.setter
    def text(self, value: str) -> None:
        self._blocks = ()
        self._block_texts = [value]
        self._block_fragments = [[("", value)]]
        self._text = value
        self.buffer.cursor_position = min(self.buffer.cursor_position, len(self._text))

    def set_blocks(self, blocks: tuple[TranscriptRenderBlock, ...]) -> bool:
        if blocks == self._blocks:
            return False
        prefix = 0
        max_prefix = min(len(self._blocks), len(blocks))
        while prefix < max_prefix and self._blocks[prefix] == blocks[prefix]:
            prefix += 1
        new_block_texts = self._block_texts[:prefix]
        new_block_fragments = self._block_fragments[:prefix]
        for block in blocks[prefix:]:
            block_text, block_fragments = self._render_block(block)
            new_block_texts.append(block_text)
            new_block_fragments.append(block_fragments)
        self._blocks = blocks
        self._block_texts = new_block_texts
        self._block_fragments = new_block_fragments
        self._text = "".join(new_block_texts)
        self.buffer.cursor_position = min(self.buffer.cursor_position, len(self._text))
        return True

    def _formatted_text(self):
        fragments: list[tuple[str, str]] = []
        for block_fragments in self._block_fragments:
            fragments.extend(block_fragments)
        return fragments

    def _cursor_position(self):
        text = self._text
        position = min(self.buffer.cursor_position, len(text))
        if not text:
            return Point(x=0, y=0)
        line = text.count("\n", 0, position)
        line_start = text.rfind("\n", 0, position)
        column = position if line_start == -1 else position - line_start - 1
        return Point(x=column, y=line)

    def _render_block(self, block: TranscriptRenderBlock) -> tuple[str, list[tuple[str, str]]]:
        text_parts: list[str] = []
        fragments: list[tuple[str, str]] = []
        if block.title is not None:
            title_line = f"[{block.title}]\n"
            text_parts.append(title_line)
            fragments.append(("class:output.block_title", title_line))
            for line in block.lines:
                line_text = f"{line}\n"
                text_parts.append(line_text)
                fragments.append(("class:output.block_body", line_text))
            if block.lines:
                text_parts.append("\n")
                fragments.append(("", "\n"))
        elif block.inline:
            for line in block.lines:
                line_text = f"{line}\n"
                text_parts.append(line_text)
                fragments.append(("class:output.inline", line_text))
        else:
            for line in block.lines:
                line_text = f"{line}\n"
                text_parts.append(line_text)
                fragments.append(("class:output.block_body", line_text))
            if block.lines:
                text_parts.append("\n")
                fragments.append(("", "\n"))
        return ("".join(text_parts), fragments)

