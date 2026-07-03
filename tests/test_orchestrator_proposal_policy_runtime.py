from __future__ import annotations

import unittest

from research_system.runtime.orchestrator_proposal_approval_policy import (
    OrchestratorProposalApprovalPolicy,
)
from research_system.runtime.orchestrator_proposal_auto_approval_support import (
    OrchestratorProposalAutoApprovalSupport,
)
from research_system.runtime.orchestrator_proposal_memory_gate_support import (
    OrchestratorProposalMemoryGateSupport,
)
from research_system.runtime.orchestrator_proposal_target_support import (
    OrchestratorProposalTargetSupport,
)


class OrchestratorProposalPolicyRuntimeTests(unittest.TestCase):
    def test_policy_wires_target_memory_and_auto_approval_supports(self) -> None:
        policy = OrchestratorProposalApprovalPolicy(orchestrator_error_cls=RuntimeError)

        self.assertIsInstance(policy.target_support, OrchestratorProposalTargetSupport)
        self.assertIsInstance(policy.memory_gate_support, OrchestratorProposalMemoryGateSupport)
        self.assertIsInstance(policy.auto_approval_support, OrchestratorProposalAutoApprovalSupport)


if __name__ == "__main__":
    unittest.main()
