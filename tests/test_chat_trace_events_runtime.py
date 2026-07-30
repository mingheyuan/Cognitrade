from __future__ import annotations

import unittest

from research_system.cli.chat_trace_events_blocks import build_events_blocks, build_events_blocks_for_session
from research_system.cli.chat_trace_events_linking import (
    build_linked_event_for_trace_session,
    build_linked_trace_for_events_session,
    linked_event_summary_for_trace_focus,
)
from research_system.cli.chat_trace_events_rows import build_events_rows_for_session
from research_system.cli.chat_trace_events_support import (
    build_events_blocks as facade_build_events_blocks,
    build_events_blocks_for_session as facade_build_events_blocks_for_session,
    build_events_rows_for_session as facade_build_events_rows_for_session,
    build_linked_event_for_trace_session as facade_build_linked_event_for_trace_session,
    build_linked_trace_for_events_session as facade_build_linked_trace_for_events_session,
    linked_event_summary_for_trace_focus as facade_linked_event_summary_for_trace_focus,
)


class ChatTraceEventsRuntimeTests(unittest.TestCase):
    def test_trace_events_facade_exports_split_supports(self) -> None:
        self.assertIs(facade_build_events_blocks, build_events_blocks)
        self.assertIs(facade_build_events_blocks_for_session, build_events_blocks_for_session)
        self.assertIs(facade_build_events_rows_for_session, build_events_rows_for_session)
        self.assertIs(facade_build_linked_event_for_trace_session, build_linked_event_for_trace_session)
        self.assertIs(facade_build_linked_trace_for_events_session, build_linked_trace_for_events_session)
        self.assertIs(facade_linked_event_summary_for_trace_focus, linked_event_summary_for_trace_focus)


if __name__ == "__main__":
    unittest.main()
