from __future__ import annotations

import unittest

from research_system.runtime.orchestrator_proposal_collect_support import (
    OrchestratorProposalCollectSupport,
)
from research_system.runtime.orchestrator_proposal_review_pipeline import (
    OrchestratorProposalReviewPipeline,
)
from research_system.runtime.orchestrator_research_proposals import OrchestratorProposalSupport


class OrchestratorProposalRuntimeTests(unittest.TestCase):
    def test_proposal_support_wires_collect_and_review_pipeline(self) -> None:
        support = object.__new__(OrchestratorProposalSupport)
        support.review_pipeline = OrchestratorProposalReviewPipeline(host=support)
        support.collect_support = OrchestratorProposalCollectSupport(host=support)

        self.assertIsInstance(support.review_pipeline, OrchestratorProposalReviewPipeline)
        self.assertIsInstance(support.collect_support, OrchestratorProposalCollectSupport)


if __name__ == "__main__":
    unittest.main()
