from __future__ import annotations

from research_system.cli.view.chat_scene import build_commands_popup_view, build_tui_blocks
from research_system.cli.transcript.chat_transcript import TranscriptMessage


def commands_popup_text() -> str:
    return "\n".join(build_commands_popup_view().lines)


def render_tui_output(lines: list[TranscriptMessage]) -> str:
    rendered: list[str] = []
    for block in build_tui_blocks(lines):
        if block.inline:
            if rendered and rendered[-1] != "":
                rendered.append("")
            rendered.extend(block.lines)
            continue
        if rendered and rendered[-1] != "":
            rendered.append("")
        if block.title is None:
            rendered.extend(block.lines)
            continue
        rendered.append(f"[{block.title}]")
        rendered.extend(block.lines)
    return "\n".join(rendered)
