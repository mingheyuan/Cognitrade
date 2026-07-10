from __future__ import annotations

import unittest

from research_system.runtime.history.session.steps.step_builder import SessionHistoryStepBuilder
from research_system.runtime.history.session.steps.step_rows import SessionHistoryStepRows


class SessionHistoryStepRuntimeTests(unittest.TestCase):
    def test_step_builder_wires_execution_graph_step_rows(self) -> None:
        builder = SessionHistoryStepBuilder()

        self.assertIsInstance(builder.step_rows_builder, SessionHistoryStepRows)


if __name__ == "__main__":
    unittest.main()
