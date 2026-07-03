from __future__ import annotations

from typing import Any

from research_system.services.run_payloads import render_labeled_field_line
from research_system.cli.chat_scene_models import TranscriptRenderBlock


def build_focused_thread_family_lines(
    *,
    rows: list[dict[str, Any]],
    focused_thread_id: str,
) -> list[str]:
    indexed_rows = {
        str(row.get("thread_id") or "").strip(): row
        for row in rows
        if str(row.get("thread_id") or "").strip()
    }
    focused = indexed_rows.get(focused_thread_id) or {}

    def _thread_family_detail(row: dict[str, Any]) -> str:
        thread_id = str(row.get("thread_id") or "unknown").strip() or "unknown"
        detail = thread_id
        thread_label = str(row.get("thread_label") or "").strip()
        topic_intent = str(row.get("topic_intent") or "").strip()
        if thread_label:
            detail += f"  name={thread_label}"
        if topic_intent:
            detail += f"  intent={topic_intent}"
        return detail

    family_lines: list[str] = []
    parent_thread_id = str(focused.get("parent_thread_id") or "").strip()
    parent_row = indexed_rows.get(parent_thread_id)
    if parent_thread_id:
        family_lines.append(f"parent: {_thread_family_detail(parent_row or {'thread_id': parent_thread_id})}")
    else:
        family_lines.append("parent: -")

    family_lines.append(f"current: {_thread_family_detail(focused or {'thread_id': focused_thread_id})}")

    ancestry: list[dict[str, Any]] = []
    cursor_parent_id = parent_thread_id
    seen_ancestors: set[str] = set()
    while cursor_parent_id and cursor_parent_id not in seen_ancestors:
        seen_ancestors.add(cursor_parent_id)
        row = indexed_rows.get(cursor_parent_id)
        if row is None:
            ancestry.append({"thread_id": cursor_parent_id})
            break
        ancestry.append(row)
        cursor_parent_id = str(row.get("parent_thread_id") or "").strip()

    if ancestry:
        family_lines.append("ancestry:")
        for depth, row in enumerate(reversed(ancestry)):
            family_lines.append(f"{'  ' * (depth + 1)}- {_thread_family_detail(row)}")
    else:
        family_lines.append("ancestry: -")

    child_rows = [
        row
        for row in rows
        if str(row.get("parent_thread_id") or "").strip() == focused_thread_id
        and str(row.get("thread_id") or "").strip() != focused_thread_id
    ]
    if child_rows:
        family_lines.append("children:")
        for child_row in child_rows:
            family_lines.append(f"  - {_thread_family_detail(child_row)}")
    else:
        family_lines.append("children: -")

    descendants_lines: list[str] = []
    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        thread_id = str(row.get("thread_id") or "").strip()
        parent_id = str(row.get("parent_thread_id") or "").strip()
        if not thread_id or not parent_id:
            continue
        children_by_parent.setdefault(parent_id, []).append(row)

    def _append_descendants(parent_id: str, *, depth: int) -> None:
        for child_row in children_by_parent.get(parent_id, []):
            descendants_lines.append(f"{'  ' * depth}- {_thread_family_detail(child_row)}")
            child_id = str(child_row.get("thread_id") or "").strip()
            if child_id and child_id != parent_id:
                _append_descendants(child_id, depth=depth + 1)

    _append_descendants(focused_thread_id, depth=1)
    if descendants_lines:
        family_lines.append("descendants:")
        family_lines.extend(descendants_lines)
    else:
        family_lines.append("descendants: -")
    return family_lines


def build_focused_thread_blocks(
    *,
    session,
    rows: list[dict[str, Any]],
    focus_index: int | None,
    thread_preview_payload,
) -> list[TranscriptRenderBlock]:
    if focus_index is None or not (0 <= focus_index < len(rows)):
        return []
    focused = rows[focus_index]
    focused_thread_id = str(focused.get("thread_id") or "").strip()
    focused_lines = [
        render_labeled_field_line("thread_id", focused.get("thread_id") or "unknown"),
        render_labeled_field_line("turn_count", focused.get("turn_count") or 0),
        render_labeled_field_line("thread_source", focused.get("thread_source") or "new"),
        render_labeled_field_line("archived", "yes" if focused.get("archived") else "no"),
        "actions: F9 resume | F10 fork | F11 archive/unarchive | Ctrl+F8 tree focus",
    ]
    if focused.get("parent_thread_id"):
        focused_lines.append(render_labeled_field_line("parent_thread_id", focused.get("parent_thread_id")))
    if focused.get("fork_turn_count"):
        focused_lines.append(render_labeled_field_line("fork_turn_count", focused.get("fork_turn_count")))
    if focused.get("thread_label"):
        focused_lines.append(render_labeled_field_line("thread_name", focused.get("thread_label")))
    if focused.get("topic_intent"):
        focused_lines.append(render_labeled_field_line("intent", focused.get("topic_intent")))
    if focused.get("topic_title"):
        focused_lines.append(render_labeled_field_line("topic", focused.get("topic_title")))
    if focused.get("archived_at"):
        focused_lines.append(render_labeled_field_line("archived_at", focused.get("archived_at")))

    blocks = [TranscriptRenderBlock(title="focused_thread", lines=tuple(focused_lines))]
    family_lines = build_focused_thread_family_lines(
        rows=rows,
        focused_thread_id=focused_thread_id,
    )
    blocks.append(TranscriptRenderBlock(title="focused_thread_family", lines=tuple(family_lines)))

    preview = thread_preview_payload(
        settings=session.settings,
        thread_id=str(focused.get("thread_id") or ""),
        turn_limit=3,
    )
    preview_lines: list[str] = []
    summary_text = str(preview.get("summary_text") or "").strip()
    thread_label = str(preview.get("thread_label") or "").strip()
    if thread_label:
        preview_lines.append(f"thread_name: {thread_label}")
    if summary_text:
        preview_lines.append(f"summary_turns: {int(preview.get('summary_turn_count') or 0)}")
        for line in summary_text.splitlines()[:4]:
            preview_lines.append(f"  {line}")
    recent_turns = list(preview.get("recent_turns") or [])
    if recent_turns:
        if preview_lines:
            preview_lines.append("recent_turns:")
        for row in recent_turns[-3:]:
            role = str(row.get("role") or "turn").strip() or "turn"
            content = " ".join(str(row.get("content") or "").split())
            if len(content) > 96:
                content = content[:93] + "..."
            preview_lines.append(f"  {role}: {content or '-'}")
    if preview.get("has_checkpoint"):
        preview_lines.append("checkpoint: available")
    if preview_lines:
        blocks.append(TranscriptRenderBlock(title="focused_thread_preview", lines=tuple(preview_lines)))
    return blocks
