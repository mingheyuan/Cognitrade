from __future__ import annotations

from typing import Any, Callable

from research_system.services.payloads.run_payloads import render_labeled_field_line
from research_system.cli.view.chat_scene_models import TranscriptRenderBlock
from research_system.cli.view.chat_threads_focus_support import (
    build_focused_thread_blocks as support_build_focused_thread_blocks,
    build_focused_thread_family_lines as support_build_focused_thread_family_lines,
)
from research_system.cli.view.chat_threads_tree_support import (
    build_thread_lineage_tree_entries as support_build_thread_lineage_tree_entries,
    build_thread_lineage_tree_lines as support_build_thread_lineage_tree_lines,
)
from research_system.cli.state.chat_types import TuiSession

ThreadSummaryLoader = Callable[..., dict[str, Any]]
ThreadListLoader = Callable[..., list[dict[str, Any]]]
ThreadPreviewLoader = Callable[..., dict[str, Any]]


def build_threads_blocks(
    session: TuiSession,
    *,
    current_thread_summary: ThreadSummaryLoader,
    list_chat_threads: ThreadListLoader,
    thread_preview_payload: ThreadPreviewLoader,
) -> list[TranscriptRenderBlock]:
    summary = current_thread_summary(settings=session.settings, state=session.state)
    rows = list_chat_threads(
        settings=session.settings,
        limit=8,
        scope=session.thread_panel_scope,
    )
    current_lines = [
        render_labeled_field_line("thread_id", summary.get("thread_id") or "unset"),
        render_labeled_field_line("thread_source", summary.get("thread_source") or "new"),
        render_labeled_field_line("thread_archived", "yes" if summary.get("archived") else "no"),
        render_labeled_field_line("turn_count", summary.get("turn_count") or 0),
        render_labeled_field_line("lifetime_turn_count", summary.get("lifetime_turn_count") or 0),
    ]
    if summary.get("parent_thread_id"):
        current_lines.append(render_labeled_field_line("parent_thread_id", summary.get("parent_thread_id")))
    if summary.get("fork_turn_count"):
        current_lines.append(render_labeled_field_line("fork_turn_count", summary.get("fork_turn_count")))
    if summary.get("archived_at"):
        current_lines.append(render_labeled_field_line("archived_at", summary.get("archived_at")))
    if summary.get("thread_label"):
        current_lines.append(render_labeled_field_line("thread_name", summary.get("thread_label")))
    if summary.get("topic_title"):
        current_lines.append(render_labeled_field_line("topic", summary.get("topic_title")))
    if summary.get("topic_intent"):
        current_lines.append(render_labeled_field_line("intent", summary.get("topic_intent")))

    blocks = [TranscriptRenderBlock(title="thread", lines=tuple(current_lines))]
    lineage_lines: list[str] = []
    parent_thread_id = str(summary.get("parent_thread_id") or "").strip()
    thread_source = str(summary.get("thread_source") or "").strip() or "new"
    if thread_source == "fork" and parent_thread_id:
        lineage_lines.append(f"parent: {parent_thread_id}")
    if summary.get("fork_turn_count"):
        lineage_lines.append(f"fork_turns: {summary.get('fork_turn_count')}")
    if summary.get("archived"):
        archived_at = str(summary.get("archived_at") or "").strip() or "-"
        lineage_lines.append(f"archive_state: archived at {archived_at}")
    else:
        lineage_lines.append("archive_state: active")
    if lineage_lines:
        blocks.append(TranscriptRenderBlock(title="thread_lineage", lines=tuple(lineage_lines)))
    focus_index = session.thread_focus_index
    blocks.extend(
        support_build_focused_thread_blocks(
            session=session,
            rows=rows,
            focus_index=focus_index,
            thread_preview_payload=thread_preview_payload,
        )
    )
    lineage_tree_lines = build_thread_lineage_tree_lines(
        rows,
        active_thread_id=str(session.state.thread_id or "").strip(),
        focus_index=session.thread_tree_focus_index,
    )
    if lineage_tree_lines:
        blocks.append(TranscriptRenderBlock(title=f"thread_tree:{session.thread_panel_scope}", lines=tuple(lineage_tree_lines)))
    list_lines: list[str] = []
    for index, row in enumerate(rows):
        thread_id = str(row.get("thread_id") or "unknown")
        turn_count = int(row.get("turn_count") or 0)
        thread_label = str(row.get("thread_label") or "").strip()
        topic_title = str(row.get("topic_title") or "").strip()
        topic_intent = str(row.get("topic_intent") or "").strip()
        thread_source = str(row.get("thread_source") or "new").strip()
        parent_thread_id = str(row.get("parent_thread_id") or "").strip()
        archived = bool(row.get("archived"))
        active_marker = "*" if thread_id == str(session.state.thread_id or "") else "-"
        focus_marker = ">" if focus_index == index else " "
        line = f"{focus_marker}{active_marker} {thread_id}  turns={turn_count}"
        if thread_source == "fork":
            line += f"  source=fork:{parent_thread_id or '?'}"
        elif thread_source:
            line += f"  source={thread_source}"
        if topic_intent:
            line += f"  intent={topic_intent}"
        if thread_label:
            line += f"  name={thread_label}"
        if topic_title:
            line += f"  topic={topic_title}"
        if archived:
            line += "  status=archived"
        list_lines.append(line)
    if not list_lines:
        list_lines.append("No threads in the selected scope.")
    blocks.append(TranscriptRenderBlock(title=f"threads:{session.thread_panel_scope}", lines=tuple(list_lines)))
    return blocks


def build_thread_lineage_tree_entries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return support_build_thread_lineage_tree_entries(rows)


def build_focused_thread_family_lines(
    *,
    rows: list[dict[str, Any]],
    focused_thread_id: str,
) -> list[str]:
    return support_build_focused_thread_family_lines(
        rows=rows,
        focused_thread_id=focused_thread_id,
    )


def build_thread_lineage_tree_lines(
    rows: list[dict[str, Any]],
    *,
    active_thread_id: str,
    focus_index: int | None = None,
) -> list[str]:
    return support_build_thread_lineage_tree_lines(
        rows,
        active_thread_id=active_thread_id,
        focus_index=focus_index,
    )

