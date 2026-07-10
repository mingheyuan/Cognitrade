from __future__ import annotations

import unittest

from research_system.runtime.proposals import OrchestratorProposalSupport as PackageProposalSupport
from research_system.runtime.proposals import OrchestratorProposalTransitionSupport
from research_system.runtime.proposals.workflow import OrchestratorProposalSupport


class OrchestratorProposalRuntimeTests(unittest.TestCase):
    def test_workflow_exports_current_proposal_lifecycle(self) -> None:
        self.assertIs(PackageProposalSupport, OrchestratorProposalSupport)
        self.assertTrue(hasattr(OrchestratorProposalSupport, "persist_proposal_transition"))
        self.assertTrue(hasattr(OrchestratorProposalTransitionSupport, "persist_proposal_transition"))

    def test_removed_automatic_proposal_pipeline_exports_are_absent(self) -> None:
        import research_system.runtime.proposals.workflow as workflow

        self.assertFalse(hasattr(workflow, "OrchestratorProposalCollectSupport"))
        self.assertFalse(hasattr(workflow, "OrchestratorProposalEntryFactory"))
        self.assertFalse(hasattr(workflow, "OrchestratorProposalReviewPipeline"))


if __name__ == "__main__":
    unittest.main()
