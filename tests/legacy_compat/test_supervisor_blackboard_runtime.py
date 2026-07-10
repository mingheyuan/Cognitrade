from __future__ import annotations

import unittest

from research_system.runtime.supervision.blackboard_review_support import SupervisorBlackboardReviewSupport
from research_system.runtime.supervision.blackboard_screen_support import SupervisorBlackboardScreenSupport
from research_system.runtime.supervision.blackboard_sector_support import SupervisorBlackboardSectorSupport
from research_system.runtime.supervision.blackboard_snapshot_support import SupervisorBlackboardSnapshotSupport
from research_system.runtime.supervision.blackboard_source_support import SupervisorBlackboardSourceSupport
from research_system.runtime.supervision.blackboard_stock_check_support import SupervisorBlackboardStockCheckSupport
from research_system.runtime.supervision.blackboard_view import SupervisorBlackboardView


class SupervisorBlackboardRuntimeTests(unittest.TestCase):
    def test_view_wires_source_snapshot_and_intent_specific_supports(self) -> None:
        view = SupervisorBlackboardView()

        self.assertIsInstance(view.source_support, SupervisorBlackboardSourceSupport)
        self.assertIsInstance(view.snapshot_support, SupervisorBlackboardSnapshotSupport)
        self.assertIsInstance(view.screen_support, SupervisorBlackboardScreenSupport)
        self.assertIsInstance(view.stock_check_support, SupervisorBlackboardStockCheckSupport)
        self.assertIsInstance(view.sector_support, SupervisorBlackboardSectorSupport)
        self.assertIsInstance(view.review_support, SupervisorBlackboardReviewSupport)


if __name__ == "__main__":
    unittest.main()
