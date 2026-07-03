from __future__ import annotations

import unittest

from research_system.services.proposal_generator import ProposalGenerator
from research_system.services.proposal_generator_dimension_support import ProposalGeneratorDimensionSupport
from research_system.services.proposal_generator_review_support import ProposalGeneratorReviewSupport
from research_system.services.proposal_generator_valuation_support import ProposalGeneratorValuationSupport
from research_system.services.proposal_payload_builders import ProposalPayloadBuilders
from research_system.services.proposal_payload_fields import ProposalPayloadFields
from research_system.services.proposal_payload_renderers import ProposalPayloadRenderers
from research_system.services.proposal_payloads import proposal_display_summary
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
from research_system.services.proposals.payload_builders import ProposalPayloadBuilders as PackageProposalPayloadBuilders
from research_system.services.proposals.payload_fields import ProposalPayloadFields as PackageProposalPayloadFields
from research_system.services.proposals.payload_renderers import ProposalPayloadRenderers as PackageProposalPayloadRenderers
from research_system.services.proposals.payloads import proposal_display_summary as package_proposal_display_summary
from research_system.services.proposals.proposal_generator import ProposalGenerator as PackageProposalGenerator
from research_system.services.proposals.proposal_generator_dimension_support import (
    ProposalGeneratorDimensionSupport as PackageProposalGeneratorDimensionSupport,
)
from research_system.services.proposals.proposal_generator_review_support import (
    ProposalGeneratorReviewSupport as PackageProposalGeneratorReviewSupport,
)
from research_system.services.proposals.proposal_generator_valuation_support import (
    ProposalGeneratorValuationSupport as PackageProposalGeneratorValuationSupport,
)


class ProposalsPackageRuntimeTests(unittest.TestCase):
    def test_top_level_generator_exports_match_package_exports(self) -> None:
        self.assertIs(ProposalGenerator, PackageProposalGenerator)
        self.assertIs(ProposalGeneratorDimensionSupport, PackageProposalGeneratorDimensionSupport)
        self.assertIs(ProposalGeneratorReviewSupport, PackageProposalGeneratorReviewSupport)
        self.assertIs(ProposalGeneratorValuationSupport, PackageProposalGeneratorValuationSupport)
        self.assertIs(ProposalGenerator, GeneratorPackageProposalGenerator)
        self.assertIs(ProposalGeneratorDimensionSupport, GeneratorPackageProposalGeneratorDimensionSupport)
        self.assertIs(ProposalGeneratorReviewSupport, GeneratorPackageProposalGeneratorReviewSupport)
        self.assertIs(ProposalGeneratorValuationSupport, GeneratorPackageProposalGeneratorValuationSupport)

    def test_top_level_payload_exports_match_package_exports(self) -> None:
        self.assertIs(ProposalPayloadBuilders, PackageProposalPayloadBuilders)
        self.assertIs(ProposalPayloadFields, PackageProposalPayloadFields)
        self.assertIs(ProposalPayloadRenderers, PackageProposalPayloadRenderers)
        self.assertIs(proposal_display_summary, package_proposal_display_summary)
        self.assertIs(ProposalPayloadBuilders, PayloadPackageProposalPayloadBuilders)
        self.assertIs(ProposalPayloadFields, PayloadPackageProposalPayloadFields)
        self.assertIs(ProposalPayloadRenderers, PayloadPackageProposalPayloadRenderers)
        self.assertIs(proposal_display_summary, payload_package_proposal_display_summary)


if __name__ == "__main__":
    unittest.main()
