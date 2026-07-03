from __future__ import annotations

import unittest

from research_system.cli import follow_up, inspect, interactions, keybindings
from research_system.cli.follow_up.chat_follow_up_markers import (
    is_short_term_memory_query,
    looks_like_follow_up_research_query,
)
from research_system.cli.follow_up.chat_follow_up_memory_support import short_term_memory_reply
from research_system.cli.follow_up.chat_follow_up_resolution_support import (
    FollowUpResolution,
    resolve_follow_up_research_request,
)
from research_system.cli.inspect.chat_inspect_state import (
    InspectPanelRuntime,
    build_inspect_panel_runtime,
    events_rows,
    load_events_panel,
    load_events_payload_for_run,
    load_trace_payload_for_run,
    sync_events_focus_with_current_slice,
    toggle_output_panel,
    toggle_trace_threads_panel,
    trace_branch_paths,
    trace_child_count,
    trace_groups,
    trace_task_count,
)
from research_system.cli.interactions.chat_interaction_output_support import (
    emit_input_update,
    emit_meta_update,
    emit_warning_update,
    write_tui_update,
)
from research_system.cli.interactions.chat_interaction_run_support import (
    request_stop,
    submit_tui_input,
    toggle_commands_popup,
)
from research_system.cli.keybindings.chat_keybinding_bindings import build_chat_keybindings_from_runtime
from research_system.cli.keybindings.chat_keybinding_runtime import (
    ChatKeybindingRuntime,
    build_chat_keybinding_runtime,
    move_output_cursor,
    set_output_mode,
)
from research_system.cli.keybindings.chat_keybindings import build_chat_keybindings
from research_system.cli.keybindings.chat_thread_state import ThreadPanelRuntime, build_thread_panel_runtime


class CliSupportPackagesRuntimeTests(unittest.TestCase):
    def test_interactions_package_exports(self) -> None:
        self.assertIs(interactions.write_tui_update, write_tui_update)
        self.assertIs(interactions.emit_meta_update, emit_meta_update)
        self.assertIs(interactions.emit_input_update, emit_input_update)
        self.assertIs(interactions.emit_warning_update, emit_warning_update)
        self.assertIs(interactions.submit_tui_input, submit_tui_input)
        self.assertIs(interactions.request_stop, request_stop)
        self.assertIs(interactions.toggle_commands_popup, toggle_commands_popup)

    def test_inspect_package_exports(self) -> None:
        self.assertIs(inspect.InspectPanelRuntime, InspectPanelRuntime)
        self.assertIs(inspect.build_inspect_panel_runtime, build_inspect_panel_runtime)
        self.assertIs(inspect.events_rows, events_rows)
        self.assertIs(inspect.trace_task_count, trace_task_count)
        self.assertIs(inspect.trace_groups, trace_groups)
        self.assertIs(inspect.load_trace_payload_for_run, load_trace_payload_for_run)
        self.assertIs(inspect.load_events_payload_for_run, load_events_payload_for_run)
        self.assertIs(inspect.load_events_panel, load_events_panel)
        self.assertIs(inspect.sync_events_focus_with_current_slice, sync_events_focus_with_current_slice)
        self.assertIs(inspect.trace_child_count, trace_child_count)
        self.assertIs(inspect.trace_branch_paths, trace_branch_paths)
        self.assertIs(inspect.toggle_output_panel, toggle_output_panel)
        self.assertIs(inspect.toggle_trace_threads_panel, toggle_trace_threads_panel)

    def test_keybindings_package_exports(self) -> None:
        self.assertIs(keybindings.ChatKeybindingRuntime, ChatKeybindingRuntime)
        self.assertIs(keybindings.ThreadPanelRuntime, ThreadPanelRuntime)
        self.assertIs(keybindings.build_chat_keybinding_runtime, build_chat_keybinding_runtime)
        self.assertIs(keybindings.build_chat_keybindings, build_chat_keybindings)
        self.assertIs(keybindings.build_chat_keybindings_from_runtime, build_chat_keybindings_from_runtime)
        self.assertIs(keybindings.build_thread_panel_runtime, build_thread_panel_runtime)
        self.assertIs(keybindings.move_output_cursor, move_output_cursor)
        self.assertIs(keybindings.set_output_mode, set_output_mode)

    def test_follow_up_package_exports(self) -> None:
        self.assertIs(follow_up.FollowUpResolution, FollowUpResolution)
        self.assertIs(follow_up.looks_like_follow_up_research_query, looks_like_follow_up_research_query)
        self.assertIs(follow_up.is_short_term_memory_query, is_short_term_memory_query)
        self.assertIs(follow_up.resolve_follow_up_research_request, resolve_follow_up_research_request)
        self.assertIs(follow_up.short_term_memory_reply, short_term_memory_reply)


if __name__ == "__main__":
    unittest.main()
