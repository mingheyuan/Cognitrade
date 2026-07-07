from __future__ import annotations

import unittest

from research_system.runtime.history.session.trace.run_trace_row_builder import RunTraceRowBuilder
from research_system.runtime.history.session.trace.debug_support import SessionHistoryDebugSupport
from research_system.runtime.history.session.trace.subagent_payload_builder import SessionHistorySubagentPayloadBuilder
from research_system.runtime.history.session.trace.trace_rebuilder import SessionHistoryTraceRebuilder
from research_system.runtime.history.session.trace.trace_sections import SessionHistoryTraceSections


class SessionHistoryTraceRuntimeTests(unittest.TestCase):
    def test_trace_rebuilder_wires_debug_trace_sections_and_subagent_builder(self) -> None:
        rebuilder = SessionHistoryTraceRebuilder()

        self.assertIsInstance(rebuilder.row_builder, RunTraceRowBuilder)
        self.assertIsInstance(rebuilder.debug_support, SessionHistoryDebugSupport)
        self.assertIsInstance(rebuilder.trace_sections, SessionHistoryTraceSections)
        self.assertIsInstance(rebuilder.subagent_payload_builder, SessionHistorySubagentPayloadBuilder)


if __name__ == "__main__":
    unittest.main()
