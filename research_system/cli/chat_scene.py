from __future__ import annotations

from research_system.cli.memory.chat_memory import current_thread_summary, list_chat_threads, thread_preview_payload
from research_system.cli.view.chat_scene import (
    Any,
    CommandsPopupView,
    HeaderBarView,
    HintBarView,
    InputBarView,
    PopupLayoutView,
    ShellSceneView,
    StatusBarItem,
    TranscriptMessage,
    TranscriptRenderBlock,
    TuiSession,
    _active_transcript_message_ids,
    _build_focused_thread_family_lines,
    _build_live_tail_block,
    _build_thread_lineage_tree_entries,
    _build_thread_lineage_tree_lines,
    annotations,
    build_commands_popup_view,
    build_events_blocks,
    build_events_blocks_for_session,
    build_events_rows_for_session,
    build_focused_task_rows_for_section,
    build_focused_trace_branch_block_for_session,
    build_focused_trace_branch_rows_for_session,
    build_focused_trace_child_block_for_session,
    build_focused_trace_child_rows_for_session,
    build_header_bar_view,
    build_hint_bar_view,
    build_hint_bar_view_runtime,
    build_input_bar_view,
    build_linked_event_for_trace_session,
    build_linked_trace_for_events_session,
    build_output_blocks,
    build_output_blocks_runtime,
    build_popup_layout_view,
    build_shell_scene_view,
    build_shell_scene_view_runtime,
    build_status_bar_items,
    build_threads_blocks,
    build_threads_blocks_runtime,
    build_trace_blocks,
    build_trace_task_blocks,
    build_trace_task_blocks_for_session,
    build_transcript_blocks_for_session,
    build_tui_blocks,
    commands_popup_text,
    idle_transcript_context_text_runtime,
    is_message_closed_for_render,
    output_mode_label,
    render_labeled_field_line,
    render_trace_row_text,
    scene_build_header_bar_view,
    scene_build_hint_bar_view,
    scene_build_input_bar_view,
    scene_build_popup_layout_view,
    scene_build_shell_scene_view,
    scene_build_status_bar_items,
    scene_build_transcript_blocks_for_session,
    scene_build_tui_blocks,
    scene_is_message_closed_for_render,
    scene_output_mode_label,
    support_build_commands_popup_view,
    support_build_focused_thread_family_lines,
    support_build_output_blocks,
    support_build_thread_lineage_tree_entries,
    support_build_thread_lineage_tree_lines,
    support_build_threads_blocks,
    support_commands_popup_text,
    support_idle_transcript_context_text,
    thread_preview_payload,
    trace_payload_fields,
    transcript_active_message_ids,
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


def build_threads_blocks(session):
    return build_threads_blocks_runtime(
        session,
        current_thread_summary_fn=current_thread_summary,
        list_chat_threads_fn=list_chat_threads,
        thread_preview_payload_fn=thread_preview_payload,
    )


def build_output_blocks(session):
    return build_output_blocks_runtime(
        session,
        build_threads_blocks_fn=build_threads_blocks,
    )


def _idle_transcript_context_text(session):
    return idle_transcript_context_text_runtime(
        session,
        current_thread_summary_fn=current_thread_summary,
    )


def build_hint_bar_view(session):
    return build_hint_bar_view_runtime(
        session,
        current_thread_summary_fn=current_thread_summary,
    )


def build_shell_scene_view(session):
    return build_shell_scene_view_runtime(
        session,
        build_output_blocks_fn=build_output_blocks,
        current_thread_summary_fn=current_thread_summary,
    )
