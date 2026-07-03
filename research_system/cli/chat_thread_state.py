from __future__ import annotations

from dataclasses import dataclass

from research_system.cli.chat_rendering import apply_tui_update
from research_system.cli.chat_types import TuiSession
from research_system.cli.chat_updates import meta_update


@dataclass(frozen=True)
class ThreadPanelRuntime:
    session: TuiSession
    emit_warning_update: object
    move_output_cursor: object
    list_threads: object
    resume_thread: object
    fork_thread: object
    archive_thread: object
    unarchive_thread: object

    def rows(self) -> list[dict]:
        if self.session.output_panel != "threads":
            return []
        rows = self.list_threads(settings=self.session.settings, limit=8, scope=self.session.thread_panel_scope)
        return rows if isinstance(rows, list) else []

    def count(self) -> int:
        return len(self.rows())

    def tree_entries(self) -> list[dict]:
        if self.session.output_panel != "threads":
            return []
        from research_system.cli.chat_scene import _build_thread_lineage_tree_entries

        entries = _build_thread_lineage_tree_entries(self.rows())
        return entries if isinstance(entries, list) else []

    def emit_meta(self, message: str) -> None:
        apply_tui_update(self.session, meta_update(message))

    def invalidate(self) -> None:
        if self.session.app is not None:
            self.session.app.invalidate()


def build_thread_panel_runtime(
    *,
    session: TuiSession,
    emit_warning_update,
    move_output_cursor,
    list_threads,
    resume_thread,
    fork_thread,
    archive_thread,
    unarchive_thread,
) -> ThreadPanelRuntime:
    return ThreadPanelRuntime(
        session=session,
        emit_warning_update=emit_warning_update,
        move_output_cursor=move_output_cursor,
        list_threads=list_threads,
        resume_thread=resume_thread,
        fork_thread=fork_thread,
        archive_thread=archive_thread,
        unarchive_thread=unarchive_thread,
    )


def set_thread_tree_focus_by_id(runtime: ThreadPanelRuntime, thread_id: str | None) -> bool:
    resolved = str(thread_id or "").strip()
    if not resolved:
        runtime.session.thread_tree_focus_index = None
        return False
    entries = runtime.tree_entries()
    for index, entry in enumerate(entries):
        if str(entry.get("thread_id") or "").strip() == resolved:
            runtime.session.thread_tree_focus_index = index
            return True
    runtime.session.thread_tree_focus_index = None
    return False


def set_thread_focus_by_id(runtime: ThreadPanelRuntime, thread_id: str | None) -> bool:
    resolved = str(thread_id or "").strip()
    if not resolved:
        runtime.session.thread_focus_index = None
        runtime.session.thread_tree_focus_index = None
        return False
    rows = runtime.rows()
    for index, row in enumerate(rows):
        if str(row.get("thread_id") or "").strip() == resolved:
            runtime.session.thread_focus_index = index
            set_thread_tree_focus_by_id(runtime, resolved)
            return True
    runtime.session.thread_focus_index = None
    runtime.session.thread_tree_focus_index = None
    return False


def stabilize_thread_focus(runtime: ThreadPanelRuntime, *, preferred_thread_id: str | None = None) -> None:
    if set_thread_focus_by_id(runtime, preferred_thread_id):
        return
    current_thread_id = str(getattr(runtime.session.state, "thread_id", None) or "").strip() or None
    if set_thread_focus_by_id(runtime, current_thread_id):
        return
    rows = runtime.rows()
    if rows:
        runtime.session.thread_focus_index = 0
        first_thread_id = str(rows[0].get("thread_id") or "").strip() or None
        set_thread_tree_focus_by_id(runtime, first_thread_id)
    else:
        runtime.session.thread_focus_index = None
        runtime.session.thread_tree_focus_index = None


def focused_thread_row(runtime: ThreadPanelRuntime) -> dict | None:
    focus_index = runtime.session.thread_focus_index
    if focus_index is None:
        return None
    rows = runtime.rows()
    if not (0 <= focus_index < len(rows)):
        return None
    row = rows[focus_index]
    return row if isinstance(row, dict) else None


def cycle_thread_panel_scope(runtime: ThreadPanelRuntime) -> None:
    if runtime.session.output_panel != "threads":
        return
    scope_order = ("active", "all", "archived")
    current_scope = str(runtime.session.thread_panel_scope or "active").strip() or "active"
    try:
        current_index = scope_order.index(current_scope)
    except ValueError:
        current_index = 0
    runtime.session.thread_panel_scope = scope_order[(current_index + 1) % len(scope_order)]
    runtime.session.thread_focus_index = None
    runtime.session.thread_tree_focus_index = None
    runtime.session.follow_output = False
    runtime.move_output_cursor(runtime.session, absolute="start")


def focus_next_thread(runtime: ThreadPanelRuntime) -> None:
    if runtime.session.output_panel != "threads":
        return
    count = runtime.count()
    if count <= 0:
        runtime.session.thread_focus_index = None
        runtime.session.thread_tree_focus_index = None
        runtime.invalidate()
        return
    current_index = runtime.session.thread_focus_index
    if current_index is None or current_index >= count - 1:
        runtime.session.thread_focus_index = 0
    else:
        runtime.session.thread_focus_index = current_index + 1
    row = focused_thread_row(runtime)
    set_thread_tree_focus_by_id(runtime, str((row or {}).get("thread_id") or "").strip() or None)
    runtime.session.follow_output = False
    runtime.move_output_cursor(runtime.session, absolute="start")


def focus_next_thread_tree_node(runtime: ThreadPanelRuntime) -> None:
    if runtime.session.output_panel != "threads":
        return
    entries = runtime.tree_entries()
    if not entries:
        runtime.session.thread_tree_focus_index = None
        runtime.invalidate()
        return
    current_index = runtime.session.thread_tree_focus_index
    if current_index is None or current_index >= len(entries) - 1:
        runtime.session.thread_tree_focus_index = 0
    else:
        runtime.session.thread_tree_focus_index = current_index + 1
    focused_entry = entries[runtime.session.thread_tree_focus_index]
    set_thread_focus_by_id(runtime, str(focused_entry.get("thread_id") or "").strip() or None)
    runtime.session.follow_output = False
    runtime.move_output_cursor(runtime.session, absolute="start")
