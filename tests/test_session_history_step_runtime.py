from __future__ import annotations

import unittest

from research_system.runtime.history.session.steps.step_builder import SessionHistoryStepBuilder
from research_system.runtime.history.session.steps.step_rows import SessionHistoryStepRows
from research_system.runtime.history.session.steps.supervisor_state import SessionHistorySupervisorState


class SessionHistoryStepRuntimeTests(unittest.TestCase):
    def test_step_builder_wires_supervisor_state_and_step_rows_helpers(self) -> None:
        builder = SessionHistoryStepBuilder()

        self.assertIsInstance(builder.supervisor_state_builder, SessionHistorySupervisorState)
        self.assertIsInstance(builder.step_rows_builder, SessionHistoryStepRows)


if __name__ == "__main__":
    unittest.main()
