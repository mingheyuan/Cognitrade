from __future__ import annotations

import unittest

from research_system.runtime.supervision import RuntimeSupervisor
from research_system.runtime.supervision._action_phase_controller import ActionPhaseController
from research_system.runtime.supervision.review_thesis_support import (
    SupervisorReviewThesisSupport,
)
from research_system.runtime.supervision.sector_research_support import (
    SupervisorSectorResearchSupport,
)
from research_system.runtime.supervision.stock_check_support import SupervisorStockCheckSupport
from research_system.runtime.supervision.stock_screen_support import (
    SupervisorStockScreenSupport,
)


class SupervisorRuntimeTests(unittest.TestCase):
    def test_supervisor_wires_intent_specific_supports(self) -> None:
        supervisor = RuntimeSupervisor()

        self.assertIsInstance(supervisor.stock_screen, SupervisorStockScreenSupport)
        self.assertIsInstance(supervisor.stock_check, SupervisorStockCheckSupport)
        self.assertIsInstance(supervisor.sector_research, SupervisorSectorResearchSupport)
        self.assertIsInstance(supervisor.review_thesis, SupervisorReviewThesisSupport)
        self.assertIsInstance(supervisor.phase_controller, ActionPhaseController)


if __name__ == "__main__":
    unittest.main()
