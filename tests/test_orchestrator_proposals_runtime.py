from __future__ import annotations

import unittest

from research_system.runtime.proposals.workflow import (
    OrchestratorProposalCollectSupport as WorkflowPackageProposalCollectSupport,
)
from research_system.runtime.proposals.workflow import (
    OrchestratorProposalReviewPipeline as WorkflowPackageProposalReviewPipeline,
)
from research_system.runtime.proposals.workflow import (
    OrchestratorProposalSupport as WorkflowPackageProposalSupport,
)
from research_system.runtime.proposals.workflow import (
    OrchestratorProposalCollectSupport,
    OrchestratorProposalReviewPipeline,
    OrchestratorProposalSupport,
)


class OrchestratorProposalRuntimeTests(unittest.TestCase):
    def test_workflow_package_exports_match_compatibility_exports(self) -> None:
        self.assertIs(WorkflowPackageProposalCollectSupport, OrchestratorProposalCollectSupport)
        self.assertIs(WorkflowPackageProposalReviewPipeline, OrchestratorProposalReviewPipeline)
        self.assertIs(WorkflowPackageProposalSupport, OrchestratorProposalSupport)

    def test_proposal_support_wires_collect_and_review_pipeline(self) -> None:
        support = object.__new__(OrchestratorProposalSupport)
        support.review_pipeline = OrchestratorProposalReviewPipeline(host=support)
        support.collect_support = OrchestratorProposalCollectSupport(host=support)

        self.assertIsInstance(support.review_pipeline, OrchestratorProposalReviewPipeline)
        self.assertIsInstance(support.collect_support, OrchestratorProposalCollectSupport)


if __name__ == "__main__":
    unittest.main()
