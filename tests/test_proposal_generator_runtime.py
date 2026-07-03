from __future__ import annotations

import unittest

from research_system.services.proposal_generator import ProposalGenerator
from research_system.services.proposal_generator_dimension_support import ProposalGeneratorDimensionSupport
from research_system.services.proposal_generator_review_support import ProposalGeneratorReviewSupport
from research_system.services.proposal_generator_valuation_support import ProposalGeneratorValuationSupport


class ProposalGeneratorRuntimeTests(unittest.TestCase):
    def test_generator_wires_type_specific_supports(self) -> None:
        generator = ProposalGenerator()

        self.assertIsInstance(generator.dimension_support, ProposalGeneratorDimensionSupport)
        self.assertIsInstance(generator.valuation_support, ProposalGeneratorValuationSupport)
        self.assertIsInstance(generator.review_support, ProposalGeneratorReviewSupport)


if __name__ == "__main__":
    unittest.main()
