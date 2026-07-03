from __future__ import annotations

from typing import Callable

from research_system.cli.chat_scene_models import TranscriptRenderBlock
from research_system.cli.chat_trace_output_scene import build_trace_output_blocks
from research_system.cli.chat_types import TuiSession


ThreadsBlocksBuilder = Callable[[TuiSession], list[TranscriptRenderBlock]]
EventsBlocksBuilder = Callable[[TuiSession], list[TranscriptRenderBlock]]
TranscriptBlocksBuilder = Callable[[TuiSession], list[TranscriptRenderBlock]]


def build_output_blocks(
    session: TuiSession,
    *,
    build_threads_blocks: ThreadsBlocksBuilder,
    build_events_blocks_for_session: EventsBlocksBuilder,
    build_transcript_blocks_for_session: TranscriptBlocksBuilder,
) -> list[TranscriptRenderBlock]:
    if session.output_panel == "threads":
        return build_threads_blocks(session)
    if session.output_panel == "events":
        return build_events_blocks_for_session(session)
    if session.output_panel == "trace":
        return build_trace_output_blocks(session)
    return build_transcript_blocks_for_session(session)
