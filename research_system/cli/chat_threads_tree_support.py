from __future__ import annotations

from typing import Any


def build_thread_lineage_tree_entries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed_rows: dict[str, dict[str, Any]] = {}
    children_by_parent: dict[str, list[str]] = {}

    for row in rows:
        thread_id = str(row.get("thread_id") or "").strip()
        if not thread_id:
            continue
        indexed_rows[thread_id] = row
    for row in rows:
        thread_id = str(row.get("thread_id") or "").strip()
        if not thread_id:
            continue
        parent_thread_id = str(row.get("parent_thread_id") or "").strip()
        if parent_thread_id:
            children_by_parent.setdefault(parent_thread_id, []).append(thread_id)

    ordered_roots: list[str] = []
    seen_roots: set[str] = set()
    for row in rows:
        thread_id = str(row.get("thread_id") or "").strip()
        if not thread_id or thread_id in seen_roots:
            continue
        parent_thread_id = str(row.get("parent_thread_id") or "").strip()
        if not parent_thread_id or parent_thread_id not in indexed_rows:
            ordered_roots.append(thread_id)
            seen_roots.add(thread_id)

    entries: list[dict[str, Any]] = []

    def _render_node(thread_id: str, *, depth: int = 0) -> None:
        row = indexed_rows.get(thread_id)
        if row is None:
            return
        entries.append(
            {
                "thread_id": thread_id,
                "depth": depth,
                "thread_source": str(row.get("thread_source") or "new").strip(),
                "archived": bool(row.get("archived")),
                "thread_label": str(row.get("thread_label") or "").strip(),
                "topic_intent": str(row.get("topic_intent") or "").strip(),
                "topic_title": str(row.get("topic_title") or "").strip(),
            }
        )
        for child_thread_id in children_by_parent.get(thread_id, []):
            if child_thread_id == thread_id or child_thread_id in visited:
                continue
            visited.add(child_thread_id)
            _render_node(child_thread_id, depth=depth + 1)

    visited: set[str] = set()
    for root_id in ordered_roots:
        if root_id in visited:
            continue
        visited.add(root_id)
        _render_node(root_id, depth=0)
    return entries


def build_thread_lineage_tree_lines(
    rows: list[dict[str, Any]],
    *,
    active_thread_id: str,
    focus_index: int | None = None,
) -> list[str]:
    entries = build_thread_lineage_tree_entries(rows)
    lines: list[str] = []
    for index, entry in enumerate(entries):
        indent = "  " * int(entry.get("depth") or 0)
        thread_id = str(entry.get("thread_id") or "").strip()
        active_marker = "*" if thread_id == active_thread_id else "-"
        focus_marker = ">" if focus_index == index else " "
        archived_suffix = " [archived]" if bool(entry.get("archived")) else ""
        source = str(entry.get("thread_source") or "new").strip()
        thread_label = str(entry.get("thread_label") or "").strip()
        intent = str(entry.get("topic_intent") or "").strip()
        topic = str(entry.get("topic_title") or "").strip()
        summary = f"{indent}{focus_marker}{active_marker} {thread_id} ({source}){archived_suffix}"
        if intent:
            summary += f" intent={intent}"
        if thread_label:
            summary += f" name={thread_label}"
        if topic:
            summary += f" topic={topic}"
        lines.append(summary)
    return lines
