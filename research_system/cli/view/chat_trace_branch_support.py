from __future__ import annotations

from typing import Any


def trace_child_path_segments(child: dict[str, Any]) -> list[str]:
    agent_path = str(child.get("agent_path") or "").strip()
    if not agent_path:
        return []
    return [segment for segment in agent_path.split("/") if segment]


def collect_trace_child_branch_paths(children: list[dict[str, Any]]) -> list[tuple[str, ...]]:
    paths: list[tuple[str, ...]] = []

    def _walk(node: dict[str, Any], prefix: tuple[str, ...]) -> None:
        for branch in node.get("branches", {}).values():
            label = str(branch.get("label") or "").strip()
            if not label:
                continue
            current = prefix + (label,)
            paths.append(current)
            _walk(branch, current)

    _walk(_build_trace_child_tree(children), ())
    return paths


def next_trace_branch_focus_path(
    branch_paths: list[tuple[str, ...]],
    current_path: tuple[str, ...] | None,
) -> tuple[str, ...] | None:
    if not branch_paths:
        return None
    if current_path not in branch_paths:
        return branch_paths[0]
    immediate_children = [
        path
        for path in branch_paths
        if len(path) == len(current_path) + 1 and path[: len(current_path)] == current_path
    ]
    if immediate_children:
        return immediate_children[0]
    current_index = branch_paths.index(current_path)
    return branch_paths[(current_index + 1) % len(branch_paths)]


def previous_trace_branch_focus_path(
    branch_paths: list[tuple[str, ...]],
    current_path: tuple[str, ...] | None,
) -> tuple[str, ...] | None:
    if not branch_paths or current_path is None:
        return None
    if len(current_path) > 1:
        parent_path = current_path[:-1]
        if parent_path in branch_paths:
            return parent_path
    return None


def trace_children_for_focus(
    group: dict[str, Any],
    *,
    branch_focus_path: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    children = list(group.get("subagent_groups") or [])
    if not branch_focus_path:
        return children
    filtered: list[dict[str, Any]] = []
    for child in children:
        segments = trace_child_path_segments(child)
        if len(segments) < len(branch_focus_path):
            continue
        if tuple(segments[: len(branch_focus_path)]) == branch_focus_path:
            filtered.append(child)
    return filtered


def branch_has_nested_children(
    group: dict[str, Any],
    branch_focus_path: tuple[str, ...],
) -> bool:
    for child in trace_children_for_focus(group, branch_focus_path=branch_focus_path):
        segments = trace_child_path_segments(child)
        if len(segments) > len(branch_focus_path) + 1:
            return True
    return False


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

