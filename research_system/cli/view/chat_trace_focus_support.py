from __future__ import annotations

from typing import Any

from research_system.services.payloads.runs.run_payloads import render_labeled_field_line
from research_system.cli.view.chat_scene_models import TranscriptRenderBlock
from research_system.cli.view.chat_trace_branch_support import (
    collect_trace_child_branch_paths,
    next_trace_branch_focus_path,
    previous_trace_branch_focus_path,
    trace_child_path_segments,
    trace_children_for_focus,
)
from research_system.cli.view.chat_trace_grouping import group_trace_runtime_cards
from research_system.cli.state.chat_types import TuiSession


def build_focused_trace_child_rows_for_session(
    session: TuiSession,
    *,
    focus_index: int | None = None,
) -> tuple[str, ...]:
    if not isinstance(session.trace_payload, dict):
        return ()
    if focus_index is None:
        return ()
    groups = group_trace_runtime_cards(session.trace_payload)
    if not (0 <= focus_index < len(groups)):
        return ()
    child_focus_index = session.trace_child_focus_index
    if child_focus_index is None:
        return ()
    children = list(trace_children_for_focus(groups[focus_index], branch_focus_path=session.trace_branch_focus_path))
    if not (0 <= child_focus_index < len(children)):
        return ()
    return tuple(str(row) for row in (children[child_focus_index].get("rows") or []) if str(row).strip())


def build_focused_trace_branch_rows_for_session(
    session: TuiSession,
    *,
    focus_index: int | None = None,
) -> tuple[str, ...]:
    if not isinstance(session.trace_payload, dict):
        return ()
    if focus_index is None:
        return ()
    branch_focus_path = session.trace_branch_focus_path
    if not branch_focus_path:
        return ()
    groups = group_trace_runtime_cards(session.trace_payload)
    if not (0 <= focus_index < len(groups)):
        return ()
    rows: list[str] = []
    for child in list(groups[focus_index].get("subagent_groups") or []):
        segments = trace_child_path_segments(child)
        if len(segments) < len(branch_focus_path):
            continue
        if tuple(segments[: len(branch_focus_path)]) != branch_focus_path:
            continue
        rows.extend(str(row) for row in (child.get("rows") or []) if str(row).strip())
    return tuple(rows)


def build_focused_task_rows_for_section(
    session: TuiSession,
    section_name: str,
    *,
    focus_index: int | None = None,
) -> tuple[str, ...]:
    if not isinstance(session.trace_payload, dict):
        return ()
    if focus_index is None:
        return ()
    groups = group_trace_runtime_cards(session.trace_payload)
    if not (0 <= focus_index < len(groups)):
        return ()
    group = groups[focus_index]
    if section_name == "subagent_trace":
        return tuple(str(row) for row in (group.get("subagents") or []) if str(row).strip())
    if section_name == "dynamic_trace":
        return tuple(str(row) for row in (group.get("dynamic") or []) if str(row).strip())
    if section_name == "wait_trace":
        return tuple(str(row) for row in (group.get("waiting") or []) if str(row).strip())
    return ()


def build_focused_trace_child_block(
    group: dict[str, Any],
    child: dict[str, Any],
    *,
    linked_event_marker: str = "",
) -> TranscriptRenderBlock:
    title = str(child.get("agent_label") or "").strip() or str(child.get("title") or "").strip() or "child_agent"
    lines = [
        render_labeled_field_line("parent_task", group.get("task_label") or group.get("title") or "-"),
        render_labeled_field_line(
            "work_unit",
            f"{title}  @ linked-event:{linked_event_marker}" if linked_event_marker else title,
        ),
    ]
    invocation_id = str(child.get("invocation_id") or "").strip()
    if invocation_id:
        lines.append(render_labeled_field_line("call_id", invocation_id))
    agent_path = str(child.get("agent_path") or "").strip()
    if agent_path:
        lines.append(render_labeled_field_line("agent_path", agent_path))
    child_status_counts = child.get("status_counts") or {}
    if child_status_counts:
        lines.append(
            render_labeled_field_line(
                "status_counts",
                ", ".join(f"{status}={count}" for status, count in sorted(child_status_counts.items())),
            )
        )
    child_objective = str(child.get("objective") or "").strip()
    if child_objective:
        lines.append(render_labeled_field_line("objective", child_objective))
    rows = list(child.get("rows") or [])
    if rows:
        lines.append("trace_rows:")
        lines.extend(f"  {row}" for row in rows)
    return TranscriptRenderBlock(title="focused_work_unit", lines=tuple(lines))


def build_focused_trace_child_block_for_session(
    session: TuiSession,
    *,
    focus_index: int | None = None,
    linked_event_marker: str = "",
) -> TranscriptRenderBlock | None:
    if not isinstance(session.trace_payload, dict):
        return None
    if focus_index is None:
        return None
    groups = group_trace_runtime_cards(session.trace_payload)
    if not (0 <= focus_index < len(groups)):
        return None
    child_focus_index = session.trace_child_focus_index
    if child_focus_index is None:
        return None
    children = list(trace_children_for_focus(groups[focus_index], branch_focus_path=session.trace_branch_focus_path))
    if not (0 <= child_focus_index < len(children)):
        return None
    return build_focused_trace_child_block(
        groups[focus_index],
        children[child_focus_index],
        linked_event_marker=linked_event_marker,
    )


def build_focused_trace_branch_block_for_session(
    session: TuiSession,
    *,
    focus_index: int | None = None,
    linked_event_marker: str = "",
) -> TranscriptRenderBlock | None:
    if not isinstance(session.trace_payload, dict):
        return None
    if focus_index is None:
        return None
    branch_focus_path = session.trace_branch_focus_path
    if not branch_focus_path:
        return None
    groups = group_trace_runtime_cards(session.trace_payload)
    if not (0 <= focus_index < len(groups)):
        return None
    matched_children: list[dict[str, Any]] = []
    for child in list(groups[focus_index].get("subagent_groups") or []):
        segments = trace_child_path_segments(child)
        if len(segments) < len(branch_focus_path):
            continue
        if tuple(segments[: len(branch_focus_path)]) == branch_focus_path:
            matched_children.append(child)
    if not matched_children:
        return None
    branch_paths = collect_trace_child_branch_paths(list(groups[focus_index].get("subagent_groups") or []))
    next_branch_focus = next_trace_branch_focus_path(branch_paths, branch_focus_path)
    up_branch_focus = previous_trace_branch_focus_path(branch_paths, branch_focus_path)
    branch_label = "/".join(branch_focus_path)
    child_branch_labels: set[str] = set()
    direct_children: list[dict[str, Any]] = []
    for child in matched_children:
        segments = trace_child_path_segments(child)
        if len(segments) == len(branch_focus_path) + 1:
            direct_children.append(child)
        elif len(segments) > len(branch_focus_path) + 1:
            child_branch_labels.add(segments[len(branch_focus_path)])
    next_work_unit_focus = None
    if direct_children:
        next_work_unit_focus = str(direct_children[0].get("title") or direct_children[0].get("agent_label") or "").strip() or "child_agent"
        next_work_unit_call = str(direct_children[0].get("invocation_id") or "").strip()
        if next_work_unit_call:
            next_work_unit_focus += f" [{next_work_unit_call}]"
    branch_has_nested_children = bool(child_branch_labels)
    next_drill_target = "-"
    if branch_has_nested_children and next_branch_focus:
        next_drill_target = "/".join(next_branch_focus)
    elif next_work_unit_focus:
        next_drill_target = next_work_unit_focus
    elif next_branch_focus:
        next_drill_target = "/".join(next_branch_focus)
    if linked_event_marker:
        branch_label += f"  @ linked-event:{linked_event_marker}"
    lines = [
        render_labeled_field_line("parent_task", groups[focus_index].get("task_label") or groups[focus_index].get("title") or "-"),
        render_labeled_field_line("branch", branch_label),
        render_labeled_field_line("parent_branch", "/".join(branch_focus_path[:-1]) or "-"),
        render_labeled_field_line("up_branch_focus", "/".join(up_branch_focus) if up_branch_focus else "-"),
        render_labeled_field_line("next_branch_focus", "/".join(next_branch_focus) if next_branch_focus else "-"),
        render_labeled_field_line("next_work_unit_focus", next_work_unit_focus or "-"),
        render_labeled_field_line("next_drill_target", next_drill_target),
        render_labeled_field_line("work_units", len(matched_children)),
    ]
    status_counts: dict[str, int] = {}
    for child in matched_children:
        for status, count in (child.get("status_counts") or {}).items():
            status_counts[str(status)] = int(status_counts.get(str(status)) or 0) + int(count or 0)
    if status_counts:
        lines.append(
            render_labeled_field_line(
                "status_counts",
                ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items())),
            )
        )
    if child_branch_labels:
        lines.append(
            render_labeled_field_line(
                "child_branches",
                ", ".join(sorted(child_branch_labels)),
            )
        )
        lines.append(
            render_labeled_field_line(
                "next_branch_targets",
                ", ".join("/".join(branch_focus_path + (label,)) for label in sorted(child_branch_labels)),
            )
        )
    lines.append(render_labeled_field_line("direct_work_units", len(direct_children)))
    lines.append("work_units:")
    for child in matched_children:
        title = str(child.get("agent_label") or "").strip() or str(child.get("title") or "").strip() or "child_agent"
        invocation_id = str(child.get("invocation_id") or "").strip()
        detail = title
        if invocation_id:
            detail += f"  call_id={invocation_id}"
        agent_path = str(child.get("agent_path") or "").strip()
        if agent_path:
            detail += f"  path={agent_path}"
        lines.append(f"  {detail}")
    return TranscriptRenderBlock(title="focused_branch", lines=tuple(lines))

