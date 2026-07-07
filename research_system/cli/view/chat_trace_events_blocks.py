from __future__ import annotations

from typing import Any

from research_system.services.payloads.runs.run_payloads import (
    events_payload_fields,
    render_event_row_text,
    render_labeled_field_line,
)
from research_system.cli.view.chat_scene_models import TranscriptRenderBlock
from research_system.cli.view.chat_trace_events_linking import (
    build_linked_event_for_trace_session,
    build_linked_trace_for_events_session,
)
from research_system.cli.view.chat_trace_events_rows import build_events_rows_for_session
from research_system.cli.state.chat_types import TuiSession


def build_events_blocks(payload: dict | None) -> list[TranscriptRenderBlock]:
    if not payload:
        return [
            TranscriptRenderBlock(
                title="events",
                lines=(
                    "No events timeline loaded for the current or last run.",
                    "Run /events <run_id|last>, or cycle to the events panel with F6 after a completed run.",
                ),
            )
        ]
    blocks = [
        TranscriptRenderBlock(
            title="events",
            lines=tuple(render_labeled_field_line(label, value) for label, value in events_payload_fields(payload)),
        )
    ]
    rows = payload.get("events") or []
    lines = tuple(
        render_event_row_text(row, indent="  ")
        for row in rows
        if isinstance(row, dict)
    ) or ("  -",)
    blocks.append(TranscriptRenderBlock(title="event_timeline", lines=lines))
    return blocks


def build_events_blocks_for_session(session: TuiSession) -> list[TranscriptRenderBlock]:
    payload = session.events_payload
    blocks = build_events_blocks(payload)
    if not isinstance(payload, dict):
        return blocks
    rows = [row for row in (payload.get("events") or []) if isinstance(row, dict)]
    sliced_rows = build_events_rows_for_session(session, rows=rows)
    focus_index = session.events_focus_index
    if focus_index is None or not (0 <= focus_index < len(sliced_rows)):
        timeline_index = next((index for index, block in enumerate(blocks) if block.title == "event_timeline"), None)
        if timeline_index is not None and sliced_rows != rows:
            blocks[timeline_index] = TranscriptRenderBlock(
                title="event_timeline",
                lines=tuple(f"- {render_event_row_text(row)}" for row in sliced_rows) or ("  -",),
            )
        return blocks
    focused_row = sliced_rows[focus_index]
    lines = _focused_event_lines(focused_row)
    blocks.insert(1, TranscriptRenderBlock(title="focused_event", lines=tuple(lines)))
    linked_trace = build_linked_trace_for_events_session(session)
    if isinstance(linked_trace, dict):
        linked_lines = [
            render_labeled_field_line("task", linked_trace.get("task") or "-"),
            render_labeled_field_line("phase", linked_trace.get("phase") or "-"),
            render_labeled_field_line("agent", linked_trace.get("agent") or "-"),
        ]
        if linked_trace.get("branch"):
            linked_lines.append(render_labeled_field_line("branch", linked_trace.get("branch")))
        if linked_trace.get("work_unit"):
            linked_lines.append(render_labeled_field_line("work_unit", linked_trace.get("work_unit")))
        if linked_trace.get("call_id"):
            linked_lines.append(render_labeled_field_line("call_id", linked_trace.get("call_id")))
        if linked_trace.get("objective"):
            linked_lines.append(render_labeled_field_line("objective", linked_trace.get("objective")))
        blocks.insert(2, TranscriptRenderBlock(title="linked_trace", lines=tuple(linked_lines)))
    linked_event = build_linked_event_for_trace_session(session)
    timeline_index = next((index for index, block in enumerate(blocks) if block.title == "event_timeline"), None)
    if timeline_index is not None:
        focused_lines = []
        for index, row in enumerate(sliced_rows):
            linked_marker = "@" if isinstance(linked_event, dict) and row is linked_event else ""
            prefix = ">" if index == focus_index else "-"
            prefix = f"{prefix}{linked_marker}" if linked_marker else prefix
            focused_lines.append(f"{prefix} {render_event_row_text(row)}")
        blocks[timeline_index] = TranscriptRenderBlock(title="event_timeline", lines=tuple(focused_lines))
    return blocks


def _focused_event_lines(focused_row: dict[str, Any]) -> list[str]:
    event_type = str(focused_row.get("event_type") or "").strip() or "event"
    lines = [
        render_labeled_field_line("event_type", event_type),
        render_labeled_field_line("phase", focused_row.get("phase") or "-"),
        render_labeled_field_line("agent", focused_row.get("agent") or "-"),
    ]
    if focused_row.get("message"):
        lines.append(render_labeled_field_line("message", focused_row.get("message")))
    if focused_row.get("task_name"):
        lines.append(render_labeled_field_line("task_name", focused_row.get("task_name")))
    if focused_row.get("agent_path"):
        lines.append(render_labeled_field_line("agent_path", focused_row.get("agent_path")))
    if focused_row.get("node_id"):
        lines.append(render_labeled_field_line("node_id", focused_row.get("node_id")))
    if focused_row.get("invocation_id"):
        lines.append(render_labeled_field_line("call_id", focused_row.get("invocation_id")))
    if focused_row.get("status"):
        lines.append(render_labeled_field_line("status", focused_row.get("status")))
    if focused_row.get("objective"):
        lines.append(render_labeled_field_line("objective", focused_row.get("objective")))
    return lines

