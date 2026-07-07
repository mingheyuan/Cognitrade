from __future__ import annotations

import unittest

from research_system.runtime.proposals.policy import (
    OrchestratorProposalApprovalPolicy as PolicyPackageApprovalPolicy,
)
from research_system.runtime.proposals.policy import (
    OrchestratorProposalAutoApprovalSupport as PolicyPackageAutoApprovalSupport,
)
from research_system.runtime.proposals.policy import (
    OrchestratorProposalMemoryGateSupport as PolicyPackageMemoryGateSupport,
)
from research_system.runtime.proposals.policy import (
    OrchestratorProposalTargetSupport as PolicyPackageTargetSupport,
)
from research_system.runtime.proposals.policy import (
    OrchestratorProposalApprovalPolicy,
    OrchestratorProposalAutoApprovalSupport,
    OrchestratorProposalMemoryGateSupport,
    OrchestratorProposalTargetSupport,
)


class OrchestratorProposalPolicyRuntimeTests(unittest.TestCase):
    def test_policy_package_exports_match_compatibility_exports(self) -> None:
        self.assertIs(PolicyPackageApprovalPolicy, OrchestratorProposalApprovalPolicy)
        self.assertIs(PolicyPackageAutoApprovalSupport, OrchestratorProposalAutoApprovalSupport)
        self.assertIs(PolicyPackageMemoryGateSupport, OrchestratorProposalMemoryGateSupport)
        self.assertIs(PolicyPackageTargetSupport, OrchestratorProposalTargetSupport)

    def test_policy_wires_target_memory_and_auto_approval_supports(self) -> None:
        policy = OrchestratorProposalApprovalPolicy(orchestrator_error_cls=RuntimeError)

        self.assertIsInstance(policy.target_support, OrchestratorProposalTargetSupport)
        self.assertIsInstance(policy.memory_gate_support, OrchestratorProposalMemoryGateSupport)
        self.assertIsInstance(policy.auto_approval_support, OrchestratorProposalAutoApprovalSupport)


if __name__ == "__main__":
    unittest.main()
