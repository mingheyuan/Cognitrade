from __future__ import annotations

import unittest

from research_system.cli.chat_trace_scene import (
    _append_trace_child_group_lines,
    _append_trace_child_tree_lines,
    _branch_has_nested_children,
    _build_focused_trace_child_block,
    _build_trace_child_tree,
    _collect_trace_child_branch_paths,
    _group_trace_runtime_cards,
    _linked_event_summary_for_trace_focus,
    _merge_trace_status_counts,
    _next_trace_branch_focus_path,
    _previous_trace_branch_focus_path,
    _render_trace_task_groups,
    _summarize_trace_child_tree_node,
    _trace_child_path_segments,
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
from research_system.cli.chat_trace_scene_support import (
    append_trace_child_group_lines,
    append_trace_child_tree_lines,
    branch_has_nested_children,
    build_events_blocks as support_build_events_blocks,
    build_events_blocks_for_session as support_build_events_blocks_for_session,
    build_events_rows_for_session as support_build_events_rows_for_session,
    build_focused_task_rows_for_section as support_build_focused_task_rows_for_section,
    build_focused_trace_branch_block_for_session as support_build_focused_trace_branch_block_for_session,
    build_focused_trace_branch_rows_for_session as support_build_focused_trace_branch_rows_for_session,
    build_focused_trace_child_block,
    build_focused_trace_child_block_for_session as support_build_focused_trace_child_block_for_session,
    build_focused_trace_child_rows_for_session as support_build_focused_trace_child_rows_for_session,
    build_linked_event_for_trace_session as support_build_linked_event_for_trace_session,
    build_linked_trace_for_events_session as support_build_linked_trace_for_events_session,
    build_trace_blocks as support_build_trace_blocks,
    build_trace_child_tree,
    build_trace_task_blocks as support_build_trace_task_blocks,
    build_trace_task_blocks_for_session as support_build_trace_task_blocks_for_session,
    collect_trace_child_branch_paths,
    group_trace_runtime_cards_wrapper,
    linked_event_summary_for_trace_focus,
    merge_trace_status_counts,
    next_trace_branch_focus_path,
    previous_trace_branch_focus_path,
    render_trace_task_groups,
    summarize_trace_child_tree_node,
    trace_child_path_segments,
    trace_children_for_focus,
)


class ChatTraceSceneRuntimeTests(unittest.TestCase):
    def test_trace_scene_facade_exports_split_supports(self) -> None:
        self.assertIsNotNone(build_trace_blocks)
        self.assertIsNotNone(build_events_blocks)
        self.assertIsNotNone(build_events_blocks_for_session)
        self.assertIsNotNone(build_events_rows_for_session)
        self.assertIsNotNone(build_linked_event_for_trace_session)
        self.assertIsNotNone(build_linked_trace_for_events_session)
        self.assertIsNotNone(_linked_event_summary_for_trace_focus)
        self.assertIsNotNone(build_trace_task_blocks)
        self.assertIsNotNone(_render_trace_task_groups)
        self.assertIsNotNone(_trace_child_path_segments)
        self.assertIsNotNone(_append_trace_child_group_lines)
        self.assertIsNotNone(_build_focused_trace_child_block)
        self.assertIsNotNone(_build_trace_child_tree)
        self.assertIsNotNone(_merge_trace_status_counts)
        self.assertIsNotNone(_summarize_trace_child_tree_node)
        self.assertIsNotNone(_collect_trace_child_branch_paths)
        self.assertIsNotNone(_next_trace_branch_focus_path)
        self.assertIsNotNone(_previous_trace_branch_focus_path)
        self.assertIsNotNone(_append_trace_child_tree_lines)
        self.assertIsNotNone(_trace_children_for_focus)
        self.assertIsNotNone(_branch_has_nested_children)
        self.assertIsNotNone(build_trace_task_blocks_for_session)
        self.assertIsNotNone(build_focused_trace_child_rows_for_session)
        self.assertIsNotNone(build_focused_trace_branch_rows_for_session)
        self.assertIsNotNone(build_focused_task_rows_for_section)
        self.assertIsNotNone(build_focused_trace_child_block_for_session)
        self.assertIsNotNone(build_focused_trace_branch_block_for_session)
        self.assertIsNotNone(_group_trace_runtime_cards)
        self.assertIsNotNone(support_build_trace_blocks)
        self.assertIsNotNone(support_build_events_blocks)
        self.assertIsNotNone(support_build_events_blocks_for_session)
        self.assertIsNotNone(support_build_events_rows_for_session)
        self.assertIsNotNone(support_build_linked_event_for_trace_session)
        self.assertIsNotNone(support_build_linked_trace_for_events_session)
        self.assertIsNotNone(linked_event_summary_for_trace_focus)
        self.assertIsNotNone(support_build_trace_task_blocks)
        self.assertIsNotNone(render_trace_task_groups)
        self.assertIsNotNone(trace_child_path_segments)
        self.assertIsNotNone(append_trace_child_group_lines)
        self.assertIsNotNone(build_focused_trace_child_block)
        self.assertIsNotNone(build_trace_child_tree)
        self.assertIsNotNone(merge_trace_status_counts)
        self.assertIsNotNone(summarize_trace_child_tree_node)
        self.assertIsNotNone(collect_trace_child_branch_paths)
        self.assertIsNotNone(next_trace_branch_focus_path)
        self.assertIsNotNone(previous_trace_branch_focus_path)
        self.assertIsNotNone(append_trace_child_tree_lines)
        self.assertIsNotNone(trace_children_for_focus)
        self.assertIsNotNone(branch_has_nested_children)
        self.assertIsNotNone(support_build_trace_task_blocks_for_session)
        self.assertIsNotNone(support_build_focused_trace_child_rows_for_session)
        self.assertIsNotNone(support_build_focused_trace_branch_rows_for_session)
        self.assertIsNotNone(support_build_focused_task_rows_for_section)
        self.assertIsNotNone(support_build_focused_trace_child_block_for_session)
        self.assertIsNotNone(support_build_focused_trace_branch_block_for_session)
        self.assertIsNotNone(group_trace_runtime_cards_wrapper)


if __name__ == "__main__":
    unittest.main()
