from __future__ import annotations

from typing import Any, Callable

from research_system.cli.chat_scene_models import TranscriptRenderBlock
from research_system.cli.chat_threads_scene import (
    build_focused_thread_family_lines as scene_build_focused_thread_family_lines,
    build_thread_lineage_tree_entries as scene_build_thread_lineage_tree_entries,
    build_thread_lineage_tree_lines as scene_build_thread_lineage_tree_lines,
    build_threads_blocks as scene_build_threads_blocks,
)
from research_system.cli.chat_types import TuiSession


ThreadSummaryLoader = Callable[..., dict[str, Any]]
ThreadListLoader = Callable[..., list[dict[str, Any]]]
ThreadPreviewLoader = Callable[..., dict[str, Any] | None]


def build_threads_blocks(
    session: TuiSession,
    *,
    current_thread_summary: ThreadSummaryLoader,
    list_chat_threads: ThreadListLoader,
    thread_preview_payload: ThreadPreviewLoader,
) -> list[TranscriptRenderBlock]:
    return scene_build_threads_blocks(
        session,
        current_thread_summary=current_thread_summary,
        list_chat_threads=list_chat_threads,
        thread_preview_payload=thread_preview_payload,
    )


def build_thread_lineage_tree_entries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return scene_build_thread_lineage_tree_entries(rows)


def build_focused_thread_family_lines(
    *,
    rows: list[dict[str, Any]],
    focused_thread_id: str,
) -> list[str]:
    return scene_build_focused_thread_family_lines(
        rows=rows,
        focused_thread_id=focused_thread_id,
    )


def build_thread_lineage_tree_lines(
    rows: list[dict[str, Any]],
    *,
    active_thread_id: str,
    focus_index: int | None = None,
) -> list[str]:
    return scene_build_thread_lineage_tree_lines(
        rows,
        active_thread_id=active_thread_id,
        focus_index=focus_index,
    )
