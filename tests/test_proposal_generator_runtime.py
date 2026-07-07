from __future__ import annotations

import unittest

from research_system.services.proposals.generator import ProposalGenerator
from research_system.services.proposals.generator import ProposalGeneratorDimensionSupport
from research_system.services.proposals.generator import ProposalGeneratorReviewSupport
from research_system.services.proposals.generator import ProposalGeneratorValuationSupport


class ProposalGeneratorRuntimeTests(unittest.TestCase):
    def test_generator_wires_type_specific_supports(self) -> None:
        generator = ProposalGenerator()

        self.assertIsInstance(generator.dimension_support, ProposalGeneratorDimensionSupport)
        self.assertIsInstance(generator.valuation_support, ProposalGeneratorValuationSupport)
        self.assertIsInstance(generator.review_support, ProposalGeneratorReviewSupport)


if __name__ == "__main__":
    unittest.main()
