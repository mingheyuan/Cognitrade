from __future__ import annotations

from typing import Any

from research_system.cli.chat_scene_models import TranscriptRenderBlock
from research_system.cli.chat_trace_branch_support import trace_child_path_segments
from research_system.cli.chat_trace_grouping import group_trace_runtime_cards


def build_trace_task_blocks(payload: dict | None, *, focus_index: int | None = None) -> list[TranscriptRenderBlock]:
    if not isinstance(payload, dict):
        return []
    groups = group_trace_runtime_cards(payload)
    if not groups:
        return []
    if focus_index is not None:
        if 0 <= focus_index < len(groups):
            groups = [groups[focus_index]]
        else:
            groups = []
    return render_trace_task_groups(groups)


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
    blocks: list[TranscriptRenderBlock] = []
    for group in groups:
        task_id = str(group.get("task_id") or "").strip()
        collapsed = bool(task_id and task_id in set(collapsed_task_ids or set()))
        header = group["title"] + (" [collapsed]" if collapsed else "")
        lines: list[str] = []
        status_counts = group.get("status_counts") or {}
        task_line = f"task: {group['task_label']}"
        if linked_event_marker:
            task_line += f"  @ linked-event:{linked_event_marker}"
        lines.append(task_line)
        if status_counts:
            lines.append(
                "status_counts: "
                + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
            )
        if group.get("objective"):
            lines.append(f"objective: {group['objective']}")
        if group.get("waiting"):
            lines.append("waits:")
            lines.extend(f"  {line}" for line in group["waiting"])
        if group.get("dynamic"):
            lines.append("dynamic_inserts:")
            lines.extend(f"  {line}" for line in group["dynamic"])
        if collapsed:
            child_count = len(list(group.get("subagent_groups") or []))
            if child_count:
                lines.append(f"spawned_subagents: {child_count} hidden")
        elif group.get("subagent_groups"):
            children = list(group["subagent_groups"])
            if child_focus_index is not None:
                if 0 <= child_focus_index < len(children):
                    children = [children[child_focus_index]]
                else:
                    children = []
            lines.append("spawned_subagents:")
            append_trace_child_tree_lines(
                lines,
                children,
                branch_focus_path=branch_focus_path,
                collapsed_branch_paths=collapsed_branch_paths,
                linked_branch_focus_path=linked_branch_focus_path,
                linked_work_unit_invocation_id=linked_work_unit_invocation_id,
                expand_child_details=expand_child_details,
            )
        elif group.get("subagents") and child_focus_index is None:
            lines.append("spawned_subagents:")
            lines.extend(f"  {line}" for line in group["subagents"])
        blocks.append(TranscriptRenderBlock(title=header, lines=tuple(lines or ("-",))))
    return blocks


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
    def _render_node(node: dict[str, Any], indent: str, branch_path: tuple[str, ...]) -> None:
        for branch in node.get("branches", {}).values():
            label = str(branch.get("label") or "").strip() or "child_group"
            current_path = branch_path + (label,)
            branch_summary = summarize_trace_child_tree_node(branch)
            collapsed = current_path in set(collapsed_branch_paths or set())
            branch_suffix = " [collapsed]" if collapsed else ""
            if linked_branch_focus_path is not None and current_path == linked_branch_focus_path:
                branch_suffix += "  @ linked-event"
            if branch_focus_path is not None:
                if current_path == branch_focus_path:
                    prefix = ">"
                    lines.append(f"{indent}{prefix} {label}/{branch_suffix}")
                    _append_branch_summary(lines, indent + "  ", branch_summary)
                    if not collapsed:
                        _render_node(branch, indent + "  ", current_path)
                elif current_path == branch_focus_path[: len(current_path)]:
                    lines.append(f"{indent}- {label}/")
                    if not collapsed:
                        _render_node(branch, indent + "  ", current_path)
                else:
                    continue
            else:
                lines.append(f"{indent}{label}/{branch_suffix}")
                _append_branch_summary(lines, indent + "  ", branch_summary)
                if not collapsed:
                    _render_node(branch, indent + "  ", current_path)
        for child in node.get("children", []):
            append_trace_child_group_lines(
                lines,
                child,
                linked_work_unit_invocation_id=linked_work_unit_invocation_id,
                expand_child_details=expand_child_details,
                base_indent=indent,
            )

    _render_node(_build_trace_child_tree(children), "  ", ())


def append_trace_child_group_lines(
    lines: list[str],
    child: dict[str, Any],
    *,
    linked_work_unit_invocation_id: str | None = None,
    expand_child_details: bool,
    base_indent: str = "  ",
) -> None:
    child_title = str(child.get("agent_label") or "").strip() or str(child.get("title") or "").strip() or "child_agent"
    invocation_id = str(child.get("invocation_id") or "").strip()
    if linked_work_unit_invocation_id and invocation_id and invocation_id == linked_work_unit_invocation_id:
        child_title += "  @ linked-event"
    lines.append(f"{base_indent}{child_title}")
    if not expand_child_details:
        return

    detail_indent = f"{base_indent}  "
    agent_path = str(child.get("agent_path") or "").strip()
    if agent_path:
        lines.append(f"{detail_indent}path: {agent_path}")
    child_status_counts = child.get("status_counts") or {}
    if child_status_counts:
        lines.append(
            f"{detail_indent}status_counts: "
            + ", ".join(f"{status}={count}" for status, count in sorted(child_status_counts.items()))
        )
    child_objective = str(child.get("objective") or "").strip()
    if child_objective:
        lines.append(f"{detail_indent}objective: {child_objective}")
    if invocation_id:
        lines.append(f"{detail_indent}call_id: {invocation_id}")
    for detail in child.get("rows") or []:
        lines.append(f"{detail_indent}{detail}")


def summarize_trace_child_tree_node(node: dict[str, Any]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    direct_children = list(node.get("children") or [])
    total_work_units = 0
    for child in direct_children:
        total_work_units += 1
        merge_trace_status_counts(status_counts, child.get("status_counts") or {})

    branches = node.get("branches") or {}
    for branch in branches.values():
        branch_summary = summarize_trace_child_tree_node(branch)
        total_work_units += int(branch_summary.get("total_work_units") or 0)
        merge_trace_status_counts(status_counts, branch_summary.get("status_counts") or {})

    return {
        "child_branches": len(branches),
        "direct_work_units": len(direct_children),
        "total_work_units": total_work_units,
        "status_counts": status_counts,
    }


def merge_trace_status_counts(
    counts: dict[str, int],
    incoming: dict[str, Any],
) -> None:
    for status, value in incoming.items():
        resolved_status = str(status).strip()
        if not resolved_status:
            continue
        counts[resolved_status] = int(counts.get(resolved_status) or 0) + int(value or 0)


def _build_trace_child_tree(children: list[dict[str, Any]]) -> dict[str, Any]:
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


def _append_branch_summary(lines: list[str], indent: str, summary: dict[str, Any]) -> None:
    lines.append(
        f"{indent}subtree: "
        f"child_branches={int(summary.get('child_branches') or 0)}, "
        f"direct_work_units={int(summary.get('direct_work_units') or 0)}, "
        f"total_work_units={int(summary.get('total_work_units') or 0)}"
    )
    status_counts = summary.get("status_counts") or {}
    if status_counts:
        lines.append(
            f"{indent}status_counts: "
            + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
        )
