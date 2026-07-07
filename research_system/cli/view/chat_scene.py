from __future__ import annotations

from typing import Any

from research_system.services.payloads.runs.run_payloads import (
    render_labeled_field_line,
    render_trace_row_text,
    trace_payload_fields,
)
from research_system.cli.memory.chat_memory import current_thread_summary, list_chat_threads, thread_preview_payload
from research_system.cli.view.chat_scene_context_support import idle_transcript_context_text as support_idle_transcript_context_text
from research_system.cli.view.chat_scene_models import (
    CommandsPopupView,
    HeaderBarView,
    HintBarView,
    InputBarView,
    PopupLayoutView,
    ShellSceneView,
    StatusBarItem,
    TranscriptRenderBlock,
)
from research_system.cli.view.chat_scene_output_support import build_output_blocks as support_build_output_blocks
from research_system.cli.view.chat_scene_popup_support import (
    build_commands_popup_view as support_build_commands_popup_view,
    commands_popup_text as support_commands_popup_text,
)
from research_system.cli.view.chat_scene_thread_support import (
    build_focused_thread_family_lines as support_build_focused_thread_family_lines,
    build_thread_lineage_tree_entries as support_build_thread_lineage_tree_entries,
    build_thread_lineage_tree_lines as support_build_thread_lineage_tree_lines,
    build_threads_blocks as support_build_threads_blocks,
)
from research_system.cli.view.chat_shell_scene import (
    build_header_bar_view as scene_build_header_bar_view,
    build_hint_bar_view as scene_build_hint_bar_view,
    build_input_bar_view as scene_build_input_bar_view,
    build_popup_layout_view as scene_build_popup_layout_view,
    build_shell_scene_view as scene_build_shell_scene_view,
    build_status_bar_items as scene_build_status_bar_items,
    output_mode_label as scene_output_mode_label,
)
from research_system.cli.view.chat_trace_scene import (
    _branch_has_nested_children,
    _collect_trace_child_branch_paths,
    _group_trace_runtime_cards,
    _next_trace_branch_focus_path,
    _previous_trace_branch_focus_path,
    _trace_children_for_focus,
    build_events_blocks,
    build_events_blocks_for_session,
    build_events_rows_for_session,
    build_focused_task_rows_for_section,
    build_focused_trace_branch_block_for_session,
    build_focused_trace_branch_rows_for_session,
    build_focused_trace_child_block_for_session,
    build_focused_trace_child_rows_for_session,
    build_linked_event_for_trace_session,
    build_linked_trace_for_events_session,
    build_trace_blocks,
    build_trace_task_blocks,
    build_trace_task_blocks_for_session,
)
from research_system.cli.state.chat_types import TuiSession
from research_system.cli.transcript.chat_transcript import TranscriptMessage
from research_system.cli.view.chat_transcript_scene import (
    active_transcript_message_ids as transcript_active_message_ids,
    build_transcript_blocks_for_session as scene_build_transcript_blocks_for_session,
    build_tui_blocks as scene_build_tui_blocks,
    is_message_closed_for_render as scene_is_message_closed_for_render,
)


def commands_popup_text() -> str:
    return support_commands_popup_text()


def build_commands_popup_view() -> CommandsPopupView:
    return support_build_commands_popup_view()


def build_tui_blocks(lines: list[TranscriptMessage]) -> list[TranscriptRenderBlock]:
    return scene_build_tui_blocks(lines)


def _active_transcript_message_ids(session: TuiSession) -> set[str]:
    return transcript_active_message_ids(session)


def _build_live_tail_block(lines: list[TranscriptMessage]) -> TranscriptRenderBlock | None:
    live_blocks = build_tui_blocks(lines)
    if not live_blocks:
        return None
    rendered_lines: list[str] = []
    for block in live_blocks:
        label = block.title or ("inline" if block.inline else "message")
        prefix = f"[{label}] "
        continuation = " " * len(prefix)
        if not block.lines:
            rendered_lines.append(prefix.rstrip())
            continue
        for index, line in enumerate(block.lines):
            rendered_lines.append(f"{prefix if index == 0 else continuation}{line}")
    return TranscriptRenderBlock(title="live_tail", lines=tuple(rendered_lines))


def build_transcript_blocks_for_session(session: TuiSession) -> list[TranscriptRenderBlock]:
    return scene_build_transcript_blocks_for_session(session)


def build_threads_blocks_runtime(
    session: TuiSession,
    *,
    current_thread_summary_fn=current_thread_summary,
    list_chat_threads_fn=list_chat_threads,
    thread_preview_payload_fn=thread_preview_payload,
) -> list[TranscriptRenderBlock]:
    return support_build_threads_blocks(
        session,
        current_thread_summary=current_thread_summary_fn,
        list_chat_threads=list_chat_threads_fn,
        thread_preview_payload=thread_preview_payload_fn,
    )


def build_threads_blocks(session: TuiSession) -> list[TranscriptRenderBlock]:
    return build_threads_blocks_runtime(session)


def _build_thread_lineage_tree_entries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return support_build_thread_lineage_tree_entries(rows)


def _build_focused_thread_family_lines(
    *,
    rows: list[dict[str, Any]],
    focused_thread_id: str,
) -> list[str]:
    return support_build_focused_thread_family_lines(
        rows=rows,
        focused_thread_id=focused_thread_id,
    )


def _build_thread_lineage_tree_lines(
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


def build_output_blocks(session: TuiSession) -> list[TranscriptRenderBlock]:
    return build_output_blocks_runtime(session)


def build_output_blocks_runtime(
    session: TuiSession,
    *,
    build_threads_blocks_fn=build_threads_blocks,
    build_events_blocks_for_session_fn=build_events_blocks_for_session,
    build_transcript_blocks_for_session_fn=build_transcript_blocks_for_session,
) -> list[TranscriptRenderBlock]:
    return support_build_output_blocks(
        session,
        build_threads_blocks=build_threads_blocks_fn,
        build_events_blocks_for_session=build_events_blocks_for_session_fn,
        build_transcript_blocks_for_session=build_transcript_blocks_for_session_fn,
    )


def output_mode_label(session: TuiSession) -> str:
    return scene_output_mode_label(session)


def _idle_transcript_context_text(session: TuiSession) -> str:
    return idle_transcript_context_text_runtime(session)


def idle_transcript_context_text_runtime(
    session: TuiSession,
    *,
    current_thread_summary_fn=current_thread_summary,
) -> str:
    return support_idle_transcript_context_text(
        session,
        current_thread_summary=current_thread_summary_fn,
    )


def is_message_closed_for_render(message: TranscriptMessage, lines: list[TranscriptMessage]) -> bool:
    return scene_is_message_closed_for_render(message, lines)


def build_status_bar_items(session: TuiSession) -> list[StatusBarItem]:
    return scene_build_status_bar_items(session)


def build_header_bar_view(session: TuiSession, layout_mode: str) -> HeaderBarView:
    return scene_build_header_bar_view(session, layout_mode)


def build_hint_bar_view(session: TuiSession) -> HintBarView:
    return build_hint_bar_view_runtime(session)


def build_hint_bar_view_runtime(
    session: TuiSession,
    *,
    current_thread_summary_fn=current_thread_summary,
) -> HintBarView:
    return scene_build_hint_bar_view(
        session,
        current_thread_summary=current_thread_summary_fn,
    )


def build_input_bar_view(session: TuiSession) -> InputBarView:
    return scene_build_input_bar_view(session)


def build_popup_layout_view(session: TuiSession) -> PopupLayoutView:
    return scene_build_popup_layout_view(session)


def build_shell_scene_view(session: TuiSession) -> ShellSceneView:
    return build_shell_scene_view_runtime(session)


def build_shell_scene_view_runtime(
    session: TuiSession,
    *,
    build_output_blocks_fn=build_output_blocks,
    build_commands_popup_view_fn=build_commands_popup_view,
    current_thread_summary_fn=current_thread_summary,
) -> ShellSceneView:
    return scene_build_shell_scene_view(
        session,
        build_output_blocks=build_output_blocks_fn,
        build_commands_popup_view=build_commands_popup_view_fn,
        current_thread_summary=current_thread_summary_fn,
    )

