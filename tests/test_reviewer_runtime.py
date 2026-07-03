from __future__ import annotations

import unittest

from research_system.agents.reviewer.reviewer_candidate_support import ReviewerCandidateSupport
from research_system.agents.reviewer.reviewer_strategy_support import ReviewerStrategySupport
from research_system.agents.reviewer.reviewer_summary_support import ReviewerSummarySupport
from research_system.agents.reviewer.reviewer_verdict_support import ReviewerVerdictSupport
from research_system.agents.reviewer.service import ReviewerService


class ReviewerRuntimeTests(unittest.TestCase):
    def test_reviewer_service_wires_supports(self) -> None:
        service = ReviewerService({"benchmark_resolution": "sector", "default_broad_index": "HS300"})

        self.assertIsInstance(service.strategy_support, ReviewerStrategySupport)
        self.assertIsInstance(service.candidate_support, ReviewerCandidateSupport)
        self.assertIsInstance(service.verdict_support, ReviewerVerdictSupport)
        self.assertIsInstance(service.summary_support, ReviewerSummarySupport)


if __name__ == "__main__":
    unittest.main()
