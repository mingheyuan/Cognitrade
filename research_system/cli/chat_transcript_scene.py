from __future__ import annotations

from research_system.cli.chat_scene_models import TranscriptRenderBlock
from research_system.cli.chat_transcript import (
    TranscriptMessage,
    display_block_title_for_message,
    renders_as_inline_message,
)
from research_system.cli.chat_types import TuiSession


def build_tui_blocks(lines: list[TranscriptMessage]) -> list[TranscriptRenderBlock]:
    blocks: list[TranscriptRenderBlock] = []
    for item in lines:
        text = item.text
        if item.scope == "turn" and is_message_closed_for_render(item, lines):
            continue
        title = display_block_title_for_message(item)
        split_lines = tuple(str(text).splitlines() or [""])
        if title is not None:
            blocks.append(TranscriptRenderBlock(title=title, lines=split_lines))
        elif renders_as_inline_message(item):
            blocks.append(TranscriptRenderBlock(title=None, lines=split_lines, inline=True))
        else:
            blocks.append(TranscriptRenderBlock(title=None, lines=split_lines))
    return blocks


def active_transcript_message_ids(session: TuiSession) -> set[str]:
    return {
        str(message_id).strip()
        for message_id in session.active_message_ids.values()
        if str(message_id or "").strip()
    }


def build_transcript_blocks_for_session(session: TuiSession) -> list[TranscriptRenderBlock]:
    active_message_ids = active_transcript_message_ids(session)
    if not active_message_ids:
        return build_tui_blocks(session.output_lines)
    committed_lines = [
        message
        for message in session.output_lines
        if message.message_id not in active_message_ids
    ]
    live_lines = [
        message
        for message in session.output_lines
        if message.message_id in active_message_ids
    ]
    blocks = build_tui_blocks(committed_lines)
    live_tail_block = _build_live_tail_block(live_lines)
    if live_tail_block is not None:
        blocks.append(live_tail_block)
    return blocks


def is_message_closed_for_render(message: TranscriptMessage, lines: list[TranscriptMessage]) -> bool:
    if message.slot_key is None:
        return False
    latest_in_slot = None
    for candidate in reversed(lines):
        if candidate.slot_key == message.slot_key:
            latest_in_slot = candidate
            break
    if latest_in_slot is None:
        return False
    return latest_in_slot.message_id != message.message_id


def _build_live_tail_block(lines: list[TranscriptMessage]) -> TranscriptRenderBlock | None:
    live_blocks = build_tui_blocks(lines)
    if not live_blocks:
        return None
    rendered_lines: list[str] = []
    for block in live_blocks:
        label = block.title or ("inline" if block.inline else "message")
        prefix = f"[{label}] "
        continuation = " " * len(prefix)
        if not block.lines:
            rendered_lines.append(prefix.rstrip())
            continue
        for index, line in enumerate(block.lines):
            rendered_lines.append(f"{prefix if index == 0 else continuation}{line}")
    return TranscriptRenderBlock(title="live_tail", lines=tuple(rendered_lines))
