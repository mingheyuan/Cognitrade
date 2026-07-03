from __future__ import annotations

import unittest

from research_system.agents.screen_reviewer.screen_reviewer_finding_support import ScreenReviewerFindingSupport
from research_system.agents.screen_reviewer.screen_reviewer_strategy_support import ScreenReviewerStrategySupport
from research_system.agents.screen_reviewer.screen_reviewer_summary_support import ScreenReviewerSummarySupport
from research_system.agents.screen_reviewer.service import ScreenReviewerService


class ScreenReviewerRuntimeTests(unittest.TestCase):
    def test_screen_reviewer_service_wires_supports(self) -> None:
        service = ScreenReviewerService()

        self.assertIsInstance(service.strategy_support, ScreenReviewerStrategySupport)
        self.assertIsInstance(service.finding_support, ScreenReviewerFindingSupport)
        self.assertIsInstance(service.summary_support, ScreenReviewerSummarySupport)


if __name__ == "__main__":
    unittest.main()
