from __future__ import annotations

import unittest

from research_system.cli.inspect.chat_inspect_state import InspectPanelRuntime as InspectPackageRuntime
from research_system.cli.inspect.chat_inspect_counts_support import (
    events_rows_for_session,
    trace_branch_paths_for_session,
    trace_child_count_for_session,
    trace_groups_for_session,
    trace_task_count_for_session,
)
from research_system.cli.inspect.chat_inspect_panel_support import (
    load_events_panel_for_session,
    sync_events_focus_for_session,
    toggle_output_panel_for_session,
    toggle_trace_threads_panel_for_session,
)
from research_system.cli.inspect.chat_inspect_payload_support import (
    load_events_payload_for_session,
    load_trace_payload_for_session,
)
from research_system.cli.inspect import (
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


class ChatInspectRuntimeTests(unittest.TestCase):
    def test_top_level_inspect_runtime_remains_compatible_with_inspect_package(self) -> None:
        self.assertIs(InspectPanelRuntime, InspectPackageRuntime)

    def test_build_runtime_exposes_compat_entrypoints(self) -> None:
        runtime = build_inspect_panel_runtime(
            session=object(),
            execute_cli_command=lambda *args, **kwargs: None,
            emit_warning_update=lambda *args, **kwargs: None,
            move_output_cursor=lambda *args, **kwargs: None,
        )

        self.assertIsInstance(runtime, InspectPanelRuntime)
        self.assertIsNotNone(events_rows)
        self.assertIsNotNone(trace_task_count)
        self.assertIsNotNone(trace_groups)
        self.assertIsNotNone(load_trace_payload_for_run)
        self.assertIsNotNone(load_events_payload_for_run)
        self.assertIsNotNone(load_events_panel)
        self.assertIsNotNone(sync_events_focus_with_current_slice)
        self.assertIsNotNone(trace_child_count)
        self.assertIsNotNone(trace_branch_paths)
        self.assertIsNotNone(toggle_output_panel)
        self.assertIsNotNone(toggle_trace_threads_panel)
        self.assertIsNotNone(events_rows_for_session)
        self.assertIsNotNone(trace_task_count_for_session)
        self.assertIsNotNone(trace_groups_for_session)
        self.assertIsNotNone(load_trace_payload_for_session)
        self.assertIsNotNone(load_events_payload_for_session)
        self.assertIsNotNone(load_events_panel_for_session)
        self.assertIsNotNone(sync_events_focus_for_session)
        self.assertIsNotNone(trace_child_count_for_session)
        self.assertIsNotNone(trace_branch_paths_for_session)
        self.assertIsNotNone(toggle_output_panel_for_session)
        self.assertIsNotNone(toggle_trace_threads_panel_for_session)


if __name__ == "__main__":
    unittest.main()
