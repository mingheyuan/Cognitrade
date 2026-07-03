from __future__ import annotations

from typing import Any

from research_system.cli.chat_scene_models import TranscriptRenderBlock
from research_system.cli.chat_trace_scene_support import (
    append_trace_child_group_lines as support_append_trace_child_group_lines,
    append_trace_child_tree_lines as support_append_trace_child_tree_lines,
    branch_has_nested_children as support_branch_has_nested_children,
    build_events_blocks as support_build_events_blocks,
    build_events_blocks_for_session as support_build_events_blocks_for_session,
    build_events_rows_for_session as support_build_events_rows_for_session,
    build_focused_task_rows_for_section as support_build_focused_task_rows_for_section,
    build_focused_trace_branch_block_for_session as support_build_focused_trace_branch_block_for_session,
    build_focused_trace_branch_rows_for_session as support_build_focused_trace_branch_rows_for_session,
    build_focused_trace_child_block as support_build_focused_trace_child_block,
    build_focused_trace_child_block_for_session as support_build_focused_trace_child_block_for_session,
    build_focused_trace_child_rows_for_session as support_build_focused_trace_child_rows_for_session,
    build_linked_event_for_trace_session as support_build_linked_event_for_trace_session,
    build_linked_trace_for_events_session as support_build_linked_trace_for_events_session,
    build_trace_blocks as support_build_trace_blocks,
    build_trace_child_tree as support_build_trace_child_tree,
    build_trace_task_blocks as support_build_trace_task_blocks,
    build_trace_task_blocks_for_session as support_build_trace_task_blocks_for_session,
    collect_trace_child_branch_paths as support_collect_trace_child_branch_paths,
    group_trace_runtime_cards_wrapper as support_group_trace_runtime_cards,
    linked_event_summary_for_trace_focus as support_linked_event_summary_for_trace_focus,
    merge_trace_status_counts as support_merge_trace_status_counts,
    next_trace_branch_focus_path as support_next_trace_branch_focus_path,
    previous_trace_branch_focus_path as support_previous_trace_branch_focus_path,
    render_trace_task_groups as support_render_trace_task_groups,
    summarize_trace_child_tree_node as support_summarize_trace_child_tree_node,
    trace_child_path_segments as support_trace_child_path_segments,
    trace_children_for_focus as support_trace_children_for_focus,
)
from research_system.cli.chat_types import TuiSession


def build_trace_blocks(payload: dict | None) -> list[TranscriptRenderBlock]:
    return support_build_trace_blocks(payload)


def build_events_blocks(payload: dict | None) -> list[TranscriptRenderBlock]:
    return support_build_events_blocks(payload)


def build_events_blocks_for_session(session: TuiSession) -> list[TranscriptRenderBlock]:
    return support_build_events_blocks_for_session(session)


def build_events_rows_for_session(
    session: TuiSession,
    *,
    rows: list[dict[str, Any]] | None = None,
    respect_output_panel: bool = True,
) -> list[dict[str, Any]]:
    return support_build_events_rows_for_session(
        session,
        rows=rows,
        respect_output_panel=respect_output_panel,
    )


def build_linked_event_for_trace_session(session: TuiSession) -> dict[str, Any] | None:
    return support_build_linked_event_for_trace_session(session)


def build_linked_trace_for_events_session(session: TuiSession) -> dict[str, Any] | None:
    return support_build_linked_trace_for_events_session(session)


def _linked_event_summary_for_trace_focus(session: TuiSession) -> tuple[bool, str]:
    return support_linked_event_summary_for_trace_focus(session)


def build_trace_task_blocks(payload: dict | None, *, focus_index: int | None = None) -> list[TranscriptRenderBlock]:
    return support_build_trace_task_blocks(payload, focus_index=focus_index)


def _render_trace_task_groups(
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
    return support_render_trace_task_groups(
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


def _trace_child_path_segments(child: dict[str, Any]) -> list[str]:
    return support_trace_child_path_segments(child)


def _append_trace_child_group_lines(
    lines: list[str],
    child: dict[str, Any],
    *,
    linked_work_unit_invocation_id: str | None = None,
    expand_child_details: bool,
    base_indent: str = "  ",
) -> None:
    support_append_trace_child_group_lines(
        lines,
        child,
        linked_work_unit_invocation_id=linked_work_unit_invocation_id,
        expand_child_details=expand_child_details,
        base_indent=base_indent,
    )


def _build_focused_trace_child_block(
    group: dict[str, Any],
    child: dict[str, Any],
    *,
    linked_event_marker: str = "",
) -> TranscriptRenderBlock:
    return support_build_focused_trace_child_block(
        group,
        child,
        linked_event_marker=linked_event_marker,
    )


def _build_trace_child_tree(children: list[dict[str, Any]]) -> dict[str, Any]:
    return support_build_trace_child_tree(children)


def _merge_trace_status_counts(
    counts: dict[str, int],
    incoming: dict[str, Any],
) -> None:
    support_merge_trace_status_counts(counts, incoming)


def _summarize_trace_child_tree_node(node: dict[str, Any]) -> dict[str, Any]:
    return support_summarize_trace_child_tree_node(node)


def _collect_trace_child_branch_paths(children: list[dict[str, Any]]) -> list[tuple[str, ...]]:
    return support_collect_trace_child_branch_paths(children)


def _next_trace_branch_focus_path(
    branch_paths: list[tuple[str, ...]],
    current_path: tuple[str, ...] | None,
) -> tuple[str, ...] | None:
    return support_next_trace_branch_focus_path(branch_paths, current_path)


def _previous_trace_branch_focus_path(
    branch_paths: list[tuple[str, ...]],
    current_path: tuple[str, ...] | None,
) -> tuple[str, ...] | None:
    return support_previous_trace_branch_focus_path(branch_paths, current_path)


def _append_trace_child_tree_lines(
    lines: list[str],
    children: list[dict[str, Any]],
    *,
    branch_focus_path: tuple[str, ...] | None = None,
    collapsed_branch_paths: set[tuple[str, ...]] | None = None,
    linked_branch_focus_path: tuple[str, ...] | None = None,
    linked_work_unit_invocation_id: str | None = None,
    expand_child_details: bool,
) -> None:
    support_append_trace_child_tree_lines(
        lines,
        children,
        branch_focus_path=branch_focus_path,
        collapsed_branch_paths=collapsed_branch_paths,
        linked_branch_focus_path=linked_branch_focus_path,
        linked_work_unit_invocation_id=linked_work_unit_invocation_id,
        expand_child_details=expand_child_details,
    )


def _trace_children_for_focus(
    group: dict[str, Any],
    *,
    branch_focus_path: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    return support_trace_children_for_focus(
        group,
        branch_focus_path=branch_focus_path,
    )


def _branch_has_nested_children(
    group: dict[str, Any],
    branch_focus_path: tuple[str, ...],
) -> bool:
    return support_branch_has_nested_children(group, branch_focus_path)


def build_trace_task_blocks_for_session(session: TuiSession, *, focus_index: int | None = None) -> list[TranscriptRenderBlock]:
    return support_build_trace_task_blocks_for_session(session, focus_index=focus_index)


def build_focused_trace_child_rows_for_session(
    session: TuiSession,
    *,
    focus_index: int | None = None,
) -> tuple[str, ...]:
    return support_build_focused_trace_child_rows_for_session(
        session,
        focus_index=focus_index,
    )


def build_focused_trace_branch_rows_for_session(
    session: TuiSession,
    *,
    focus_index: int | None = None,
) -> tuple[str, ...]:
    return support_build_focused_trace_branch_rows_for_session(
        session,
        focus_index=focus_index,
    )


def build_focused_task_rows_for_section(
    session: TuiSession,
    section_name: str,
    *,
    focus_index: int | None = None,
) -> tuple[str, ...]:
    return support_build_focused_task_rows_for_section(
        session,
        section_name,
        focus_index=focus_index,
    )


def build_focused_trace_child_block_for_session(
    session: TuiSession,
    *,
    focus_index: int | None = None,
) -> TranscriptRenderBlock | None:
    return support_build_focused_trace_child_block_for_session(session, focus_index=focus_index)


def build_focused_trace_branch_block_for_session(
    session: TuiSession,
    *,
    focus_index: int | None = None,
) -> TranscriptRenderBlock | None:
    return support_build_focused_trace_branch_block_for_session(session, focus_index=focus_index)


def _group_trace_runtime_cards(payload: dict) -> list[dict[str, Any]]:
    return support_group_trace_runtime_cards(payload)
