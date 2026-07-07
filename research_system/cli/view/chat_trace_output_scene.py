from __future__ import annotations

from research_system.services.payloads.runs.run_payloads import (
    render_labeled_field_line,
    render_trace_row_text,
    trace_payload_fields,
)
from research_system.cli.view.chat_scene_models import TranscriptRenderBlock
from research_system.cli.view.chat_trace_events_support import build_linked_event_for_trace_session
from research_system.cli.view.chat_trace_focus_support import (
    build_focused_task_rows_for_section,
    build_focused_trace_branch_block_for_session,
    build_focused_trace_branch_rows_for_session,
    build_focused_trace_child_block_for_session,
    build_focused_trace_child_rows_for_session,
)
from research_system.cli.view.chat_trace_scene import (
    _linked_event_summary_for_trace_focus,
    build_trace_blocks,
    build_trace_task_blocks_for_session,
)
from research_system.cli.state.chat_types import TuiSession


def build_trace_output_blocks(session: TuiSession) -> list[TranscriptRenderBlock]:
    if not isinstance(session.trace_payload, dict):
        return build_trace_blocks(session.trace_payload)
    blocks = [
        TranscriptRenderBlock(
            title="trace",
            lines=tuple(
                render_labeled_field_line(label, value)
                for label, value in trace_payload_fields(session.trace_payload)
            ),
        ),
        *build_trace_task_blocks_for_session(session),
    ]
    focus_index = session.trace_focus_index
    linked_active, linked_summary = _linked_event_summary_for_trace_focus(session)
    focused_branch_block = build_focused_trace_branch_block_for_session(
        session,
        focus_index=focus_index,
        linked_event_marker=linked_summary if linked_active else "",
    )
    if focused_branch_block is not None:
        blocks.insert(2, focused_branch_block)
    focused_child_block = build_focused_trace_child_block_for_session(
        session,
        focus_index=focus_index,
        linked_event_marker=linked_summary if linked_active else "",
    )
    if focused_child_block is not None:
        blocks.insert(3 if focused_branch_block is not None else 2, focused_child_block)
    linked_event = build_linked_event_for_trace_session(session)
    if isinstance(linked_event, dict):
        linked_lines = [
            render_labeled_field_line("event_type", linked_event.get("event_type") or "-"),
            render_labeled_field_line("phase", linked_event.get("phase") or "-"),
            render_labeled_field_line("agent", linked_event.get("agent") or "-"),
        ]
        if linked_event.get("message"):
            linked_lines.append(render_labeled_field_line("message", linked_event.get("message")))
        if linked_event.get("task_name"):
            linked_lines.append(render_labeled_field_line("task_name", linked_event.get("task_name")))
        if linked_event.get("invocation_id"):
            linked_lines.append(render_labeled_field_line("call_id", linked_event.get("invocation_id")))
        if linked_event.get("status"):
            linked_lines.append(render_labeled_field_line("status", linked_event.get("status")))
        linked_event_block = TranscriptRenderBlock(title="linked_event", lines=tuple(linked_lines))
        insert_at = 4 if focused_child_block is not None else (3 if focused_branch_block is not None else 2)
        blocks.insert(insert_at, linked_event_block)
    focused_child_rows = build_focused_trace_child_rows_for_session(session, focus_index=focus_index)
    focused_branch_rows = build_focused_trace_branch_rows_for_session(session, focus_index=focus_index)
    for section_name in ("subagent_trace", "dynamic_trace", "wait_trace"):
        rows = session.trace_payload.get(section_name) or []
        if not isinstance(rows, list):
            continue
        rendered_lines = [
            render_trace_row_text(row, indent="  ")
            for row in rows
            if isinstance(row, dict)
        ]
        focused_task_rows = build_focused_task_rows_for_section(
            session,
            section_name,
            focus_index=focus_index,
        )
        if focus_index is not None and focused_task_rows:
            focused_set = set(focused_task_rows)
            rendered_lines = [line for line in rendered_lines if line.strip() in focused_set]
        if section_name == "subagent_trace" and focused_child_rows:
            focused_set = set(focused_child_rows)
            rendered_lines = [line for line in rendered_lines if line.strip() in focused_set]
        elif section_name == "subagent_trace" and focused_branch_rows:
            focused_set = set(focused_branch_rows)
            rendered_lines = [line for line in rendered_lines if line.strip() in focused_set]
        lines = tuple(rendered_lines) or ("  -",)
        blocks.append(TranscriptRenderBlock(title=section_name, lines=lines))
    if not session.trace_show_raw_sections:
        blocks = [
            block for block in blocks
            if block.title not in {"subagent_trace", "dynamic_trace", "wait_trace"}
        ]
    if focus_index is None:
        return blocks
    focused_task_blocks = build_trace_task_blocks_for_session(session, focus_index=focus_index)
    if not focused_task_blocks:
        return blocks
    focused_titles = {block.title for block in focused_task_blocks}
    filtered_blocks = [blocks[0], *focused_task_blocks]
    for block in blocks[1:]:
        if block.title in focused_titles:
            continue
        if block.title == "focused_branch":
            filtered_blocks.append(block)
            continue
        if block.title == "focused_work_unit":
            filtered_blocks.append(block)
            continue
        if block.title == "linked_event":
            filtered_blocks.append(block)
            continue
        if block.title in {"subagent_trace", "dynamic_trace", "wait_trace"}:
            filtered_blocks.append(block)
    return filtered_blocks

