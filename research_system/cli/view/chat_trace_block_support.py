from __future__ import annotations

from typing import Any

from research_system.services.payloads.runs.run_payloads import (
    render_labeled_field_line,
    render_trace_row_text,
    trace_payload_fields,
)
from research_system.cli.view.chat_scene_models import TranscriptRenderBlock


def build_trace_blocks(
    payload: dict | None,
    *,
    build_trace_task_blocks_fn,
) -> list[TranscriptRenderBlock]:
    if not payload:
        return [
            TranscriptRenderBlock(
                title="trace",
                lines=(
                    "No trace loaded for the current or last run.",
                    "Run /trace <run_id|last>, or press F2 after a completed run.",
                ),
            )
        ]
    blocks = [
        TranscriptRenderBlock(
            title="trace",
            lines=tuple(render_labeled_field_line(label, value) for label, value in trace_payload_fields(payload)),
        )
    ]
    task_blocks = build_trace_task_blocks_fn(payload)
    if task_blocks:
        blocks.extend(task_blocks)
    for section_name in ("subagent_trace", "dynamic_trace", "wait_trace"):
        rows = payload.get(section_name) or []
        if not isinstance(rows, list):
            continue
        lines = tuple(
            render_trace_row_text(row, indent="  ")
            for row in rows
            if isinstance(row, dict)
        ) or ("  -",)
        blocks.append(TranscriptRenderBlock(title=section_name, lines=lines))
    return blocks


def build_trace_task_blocks_for_session(
    session,
    *,
    focus_index: int | None = None,
    group_trace_runtime_cards_fn,
    linked_event_summary_for_trace_focus_fn,
    trace_children_for_focus_fn,
    render_trace_task_groups_fn,
) -> list[TranscriptRenderBlock]:
    if not isinstance(session.trace_payload, dict):
        return []
    groups = group_trace_runtime_cards_fn(session.trace_payload)
    if not groups:
        return []
    if focus_index is not None:
        if 0 <= focus_index < len(groups):
            groups = [groups[focus_index]]
        else:
            groups = []
    child_focus_index = session.trace_child_focus_index if focus_index is not None else None
    branch_focus_path = session.trace_branch_focus_path if focus_index is not None else None
    linked = ""
    linked_branch_focus_path = None
    linked_work_unit_invocation_id = None
    if focus_index is not None:
        linked_active, linked_summary = linked_event_summary_for_trace_focus_fn(session)
        if linked_active:
            linked = linked_summary
        linked_branch_focus_path = session.trace_branch_focus_path
        if (
            isinstance(session.trace_payload, dict)
            and session.trace_child_focus_index is not None
            and 0 <= focus_index < len(group_trace_runtime_cards_fn(session.trace_payload))
        ):
            groups_for_link = group_trace_runtime_cards_fn(session.trace_payload)
            children_for_link = trace_children_for_focus_fn(
                groups_for_link[focus_index],
                branch_focus_path=session.trace_branch_focus_path,
            )
            if 0 <= session.trace_child_focus_index < len(children_for_link):
                linked_work_unit_invocation_id = str(
                    children_for_link[session.trace_child_focus_index].get("invocation_id") or ""
                ).strip() or None
    return render_trace_task_groups_fn(
        groups,
        linked_event_marker=linked,
        child_focus_index=child_focus_index,
        branch_focus_path=branch_focus_path,
        collapsed_branch_paths=session.trace_collapsed_branches if focus_index is not None else None,
        linked_branch_focus_path=linked_branch_focus_path,
        linked_work_unit_invocation_id=linked_work_unit_invocation_id,
        expand_child_details=session.trace_expand_child_details,
        collapsed_task_ids=session.trace_collapsed_tasks,
    )

