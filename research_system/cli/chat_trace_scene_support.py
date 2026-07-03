from __future__ import annotations

from typing import Any

from research_system.cli.chat_scene_models import TranscriptRenderBlock
from research_system.cli.chat_trace_block_support import (
    build_trace_blocks as support_build_trace_blocks,
    build_trace_task_blocks_for_session as support_build_trace_task_blocks_for_session,
)
from research_system.cli.chat_trace_branch_support import (
    branch_has_nested_children as trace_branch_has_nested_children,
    collect_trace_child_branch_paths as trace_collect_trace_child_branch_paths,
    next_trace_branch_focus_path as trace_next_trace_branch_focus_path,
    previous_trace_branch_focus_path as trace_previous_trace_branch_focus_path,
    trace_child_path_segments as trace_child_path_segments_support,
    trace_children_for_focus as trace_children_for_focus_support,
)
from research_system.cli.chat_trace_events_support import (
    build_events_blocks as trace_build_events_blocks,
    build_events_blocks_for_session as trace_build_events_blocks_for_session,
    build_events_rows_for_session as trace_build_events_rows_for_session,
    build_linked_event_for_trace_session as trace_build_linked_event_for_trace_session,
    build_linked_trace_for_events_session as trace_build_linked_trace_for_events_session,
    linked_event_summary_for_trace_focus as trace_linked_event_summary_for_trace_focus,
)
from research_system.cli.chat_trace_focus_support import (
    build_focused_task_rows_for_section as trace_build_focused_task_rows_for_section,
    build_focused_trace_branch_block_for_session as trace_build_focused_trace_branch_block_for_session,
    build_focused_trace_branch_rows_for_session as trace_build_focused_trace_branch_rows_for_session,
    build_focused_trace_child_block as trace_build_focused_trace_child_block,
    build_focused_trace_child_block_for_session as trace_build_focused_trace_child_block_for_session,
    build_focused_trace_child_rows_for_session as trace_build_focused_trace_child_rows_for_session,
)
from research_system.cli.chat_trace_grouping import group_trace_runtime_cards
from research_system.cli.chat_trace_rendering_support import (
    append_trace_child_group_lines as trace_append_trace_child_group_lines,
    append_trace_child_tree_lines as trace_append_trace_child_tree_lines,
    build_trace_task_blocks as trace_build_trace_task_blocks,
    merge_trace_status_counts as trace_merge_trace_status_counts,
    render_trace_task_groups as trace_render_trace_task_groups,
    summarize_trace_child_tree_node as trace_summarize_trace_child_tree_node,
)
from research_system.cli.chat_types import TuiSession


def build_trace_blocks(payload: dict | None) -> list[TranscriptRenderBlock]:
    return support_build_trace_blocks(payload, build_trace_task_blocks_fn=build_trace_task_blocks)


def build_events_blocks(payload: dict | None) -> list[TranscriptRenderBlock]:
    return trace_build_events_blocks(payload)


def build_events_blocks_for_session(session: TuiSession) -> list[TranscriptRenderBlock]:
    return trace_build_events_blocks_for_session(session)


def build_events_rows_for_session(
    session: TuiSession,
    *,
    rows: list[dict[str, Any]] | None = None,
    respect_output_panel: bool = True,
) -> list[dict[str, Any]]:
    return trace_build_events_rows_for_session(
        session,
        rows=rows,
        respect_output_panel=respect_output_panel,
    )


def build_linked_event_for_trace_session(session: TuiSession) -> dict[str, Any] | None:
    return trace_build_linked_event_for_trace_session(session)


def build_linked_trace_for_events_session(session: TuiSession) -> dict[str, Any] | None:
    return trace_build_linked_trace_for_events_session(session)


def linked_event_summary_for_trace_focus(session: TuiSession) -> tuple[bool, str]:
    return trace_linked_event_summary_for_trace_focus(session)


def build_trace_task_blocks(payload: dict | None, *, focus_index: int | None = None) -> list[TranscriptRenderBlock]:
    return trace_build_trace_task_blocks(payload, focus_index=focus_index)


def render_trace_task_groups(
    groups: list[dict[str, Any]],
    *,
    linked_event_marker: str = "",
    child_focus_index: int | None = None,
    branch_focus_path: tuple[str, ...] | None = None,
    collapsed_branch_paths: set[tuple[str, ...]] | None = None,
    linked_branch_focus_path: tuple[str, ...] | None = None,
    linked_work_unit_invocation_id: str | None = None,
    expand_child_details: bool = True,
    collapsed_task_ids: set[str] | None = None,
) -> list[TranscriptRenderBlock]:
    return trace_render_trace_task_groups(
        groups,
        linked_event_marker=linked_event_marker,
        child_focus_index=child_focus_index,
        branch_focus_path=branch_focus_path,
        collapsed_branch_paths=collapsed_branch_paths,
        linked_branch_focus_path=linked_branch_focus_path,
        linked_work_unit_invocation_id=linked_work_unit_invocation_id,
        expand_child_details=expand_child_details,
        collapsed_task_ids=collapsed_task_ids,
    )


def trace_child_path_segments(child: dict[str, Any]) -> list[str]:
    return trace_child_path_segments_support(child)


def append_trace_child_group_lines(
    lines: list[str],
    child: dict[str, Any],
    *,
    linked_work_unit_invocation_id: str | None = None,
    expand_child_details: bool,
    base_indent: str = "  ",
) -> None:
    trace_append_trace_child_group_lines(
        lines,
        child,
        linked_work_unit_invocation_id=linked_work_unit_invocation_id,
        expand_child_details=expand_child_details,
        base_indent=base_indent,
    )


def build_focused_trace_child_block(
    group: dict[str, Any],
    child: dict[str, Any],
    *,
    linked_event_marker: str = "",
) -> TranscriptRenderBlock:
    return trace_build_focused_trace_child_block(
        group,
        child,
        linked_event_marker=linked_event_marker,
    )


def build_trace_child_tree(children: list[dict[str, Any]]) -> dict[str, Any]:
    root: dict[str, Any] = {"branches": {}, "children": []}
    for child in children:
        segments = trace_child_path_segments(child)
        branch_segments = segments[:-1] if segments else []
        cursor = root
        for segment in branch_segments:
            branches = cursor.setdefault("branches", {})
            if segment not in branches:
                branches[segment] = {"label": segment, "branches": {}, "children": []}
            cursor = branches[segment]
        cursor.setdefault("children", []).append(child)
    return root


def merge_trace_status_counts(
    counts: dict[str, int],
    incoming: dict[str, Any],
) -> None:
    trace_merge_trace_status_counts(counts, incoming)


def summarize_trace_child_tree_node(node: dict[str, Any]) -> dict[str, Any]:
    return trace_summarize_trace_child_tree_node(node)


def collect_trace_child_branch_paths(children: list[dict[str, Any]]) -> list[tuple[str, ...]]:
    return trace_collect_trace_child_branch_paths(children)


def next_trace_branch_focus_path(
    branch_paths: list[tuple[str, ...]],
    current_path: tuple[str, ...] | None,
) -> tuple[str, ...] | None:
    return trace_next_trace_branch_focus_path(branch_paths, current_path)


def previous_trace_branch_focus_path(
    branch_paths: list[tuple[str, ...]],
    current_path: tuple[str, ...] | None,
) -> tuple[str, ...] | None:
    return trace_previous_trace_branch_focus_path(branch_paths, current_path)


def append_trace_child_tree_lines(
    lines: list[str],
    children: list[dict[str, Any]],
    *,
    branch_focus_path: tuple[str, ...] | None = None,
    collapsed_branch_paths: set[tuple[str, ...]] | None = None,
    linked_branch_focus_path: tuple[str, ...] | None = None,
    linked_work_unit_invocation_id: str | None = None,
    expand_child_details: bool,
) -> None:
    trace_append_trace_child_tree_lines(
        lines,
        children,
        branch_focus_path=branch_focus_path,
        collapsed_branch_paths=collapsed_branch_paths,
        linked_branch_focus_path=linked_branch_focus_path,
        linked_work_unit_invocation_id=linked_work_unit_invocation_id,
        expand_child_details=expand_child_details,
    )


def trace_children_for_focus(
    group: dict[str, Any],
    *,
    branch_focus_path: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    return trace_children_for_focus_support(
        group,
        branch_focus_path=branch_focus_path,
    )


def branch_has_nested_children(
    group: dict[str, Any],
    branch_focus_path: tuple[str, ...],
) -> bool:
    return trace_branch_has_nested_children(group, branch_focus_path)


def build_trace_task_blocks_for_session(session: TuiSession, *, focus_index: int | None = None) -> list[TranscriptRenderBlock]:
    return support_build_trace_task_blocks_for_session(
        session,
        focus_index=focus_index,
        group_trace_runtime_cards_fn=group_trace_runtime_cards_wrapper,
        linked_event_summary_for_trace_focus_fn=linked_event_summary_for_trace_focus,
        trace_children_for_focus_fn=trace_children_for_focus,
        render_trace_task_groups_fn=render_trace_task_groups,
    )


def build_focused_trace_child_rows_for_session(
    session: TuiSession,
    *,
    focus_index: int | None = None,
) -> tuple[str, ...]:
    return trace_build_focused_trace_child_rows_for_session(
        session,
        focus_index=focus_index,
    )


def build_focused_trace_branch_rows_for_session(
    session: TuiSession,
    *,
    focus_index: int | None = None,
) -> tuple[str, ...]:
    return trace_build_focused_trace_branch_rows_for_session(
        session,
        focus_index=focus_index,
    )


def build_focused_task_rows_for_section(
    session: TuiSession,
    section_name: str,
    *,
    focus_index: int | None = None,
) -> tuple[str, ...]:
    return trace_build_focused_task_rows_for_section(
        session,
        section_name,
        focus_index=focus_index,
    )


def build_focused_trace_child_block_for_session(
    session: TuiSession,
    *,
    focus_index: int | None = None,
) -> TranscriptRenderBlock | None:
    linked_active, linked_summary = linked_event_summary_for_trace_focus(session)
    return trace_build_focused_trace_child_block_for_session(
        session,
        focus_index=focus_index,
        linked_event_marker=linked_summary if linked_active else "",
    )


def build_focused_trace_branch_block_for_session(
    session: TuiSession,
    *,
    focus_index: int | None = None,
) -> TranscriptRenderBlock | None:
    linked_active, linked_summary = linked_event_summary_for_trace_focus(session)
    return trace_build_focused_trace_branch_block_for_session(
        session,
        focus_index=focus_index,
        linked_event_marker=linked_summary if linked_active else "",
    )


def group_trace_runtime_cards_wrapper(payload: dict) -> list[dict[str, Any]]:
    return group_trace_runtime_cards(payload)
