from __future__ import annotations

import unittest

from research_system.services.proposals.generator import ProposalGenerator as GeneratorPackageProposalGenerator
from research_system.services.proposals.generator import (
    ProposalGeneratorDimensionSupport as GeneratorPackageProposalGeneratorDimensionSupport,
)
from research_system.services.proposals.generator import (
    ProposalGeneratorReviewSupport as GeneratorPackageProposalGeneratorReviewSupport,
)
from research_system.services.proposals.generator import (
    ProposalGeneratorValuationSupport as GeneratorPackageProposalGeneratorValuationSupport,
)
from research_system.services.proposals.payload import ProposalPayloadBuilders as PayloadPackageProposalPayloadBuilders
from research_system.services.proposals.payload import ProposalPayloadFields as PayloadPackageProposalPayloadFields
from research_system.services.proposals.payload import ProposalPayloadRenderers as PayloadPackageProposalPayloadRenderers
from research_system.services.proposals.payload import proposal_display_summary as payload_package_proposal_display_summary


class ProposalsPackageRuntimeTests(unittest.TestCase):
    def test_generator_package_exports_are_available(self) -> None:
        self.assertTrue(callable(GeneratorPackageProposalGenerator))
        self.assertTrue(callable(GeneratorPackageProposalGeneratorDimensionSupport))
        self.assertTrue(callable(GeneratorPackageProposalGeneratorReviewSupport))
        self.assertTrue(callable(GeneratorPackageProposalGeneratorValuationSupport))

    def test_payload_package_exports_are_available(self) -> None:
        self.assertTrue(callable(PayloadPackageProposalPayloadBuilders))
        self.assertTrue(callable(PayloadPackageProposalPayloadFields))
        self.assertTrue(callable(PayloadPackageProposalPayloadRenderers))
        self.assertTrue(callable(payload_package_proposal_display_summary))


if __name__ == "__main__":
    unittest.main()
