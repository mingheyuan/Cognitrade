from __future__ import annotations

import unittest

from research_system.services.methodology_evolver import MethodologyEvolver
from research_system.services.methodology_evolver_aggregation import MethodologyEvolverAggregation
from research_system.services.methodology_evolver_dimension_aggregation import (
    MethodologyEvolverDimensionAggregation,
)
from research_system.services.methodology_evolver_extraction import MethodologyEvolverExtraction
from research_system.services.methodology_evolver_dimension_candidates import (
    MethodologyEvolverDimensionCandidates,
)
from research_system.services.methodology_evolver_memory_matching import MethodologyEvolverMemoryMatching
from research_system.services.methodology_evolver_memory_support import MethodologyEvolverMemorySupport
from research_system.services.methodology_evolver_memory_summary import MethodologyEvolverMemorySummary
from research_system.services.methodology_evolver_protocol_support import MethodologyEvolverProtocolSupport
from research_system.services.methodology_evolver_review_candidates import (
    MethodologyEvolverReviewCandidates,
)
from research_system.services.methodology_evolver_review_aggregation import (
    MethodologyEvolverReviewAggregation,
)
from research_system.services.methodology_evolver_valuation_candidates import (
    MethodologyEvolverValuationCandidates,
)
from research_system.services.methodology_evolver_valuation_aggregation import (
    MethodologyEvolverValuationAggregation,
)


class MethodologyEvolverRuntimeTests(unittest.TestCase):
    def test_evolver_wires_memory_extraction_and_aggregation_supports(self) -> None:
        evolver = MethodologyEvolver()

        self.assertIsInstance(evolver.memory_support, MethodologyEvolverMemorySupport)
        self.assertIsInstance(evolver.extraction, MethodologyEvolverExtraction)
        self.assertIsInstance(evolver.aggregation, MethodologyEvolverAggregation)
        self.assertIsInstance(
            evolver.aggregation.dimension_aggregation,
            MethodologyEvolverDimensionAggregation,
        )
        self.assertIsInstance(
            evolver.aggregation.valuation_aggregation,
            MethodologyEvolverValuationAggregation,
        )
        self.assertIsInstance(
            evolver.aggregation.review_aggregation,
            MethodologyEvolverReviewAggregation,
        )
        self.assertIs(evolver.extraction.memory_support, evolver.memory_support)
        self.assertIs(evolver.aggregation.memory_support, evolver.memory_support)
        self.assertIs(evolver.aggregation.extraction, evolver.extraction)
        self.assertIsInstance(
            evolver.extraction.dimension_candidates,
            MethodologyEvolverDimensionCandidates,
        )
        self.assertIsInstance(
            evolver.extraction.valuation_candidates,
            MethodologyEvolverValuationCandidates,
        )
        self.assertIsInstance(
            evolver.extraction.review_candidates,
            MethodologyEvolverReviewCandidates,
        )

    def test_memory_support_wires_protocol_matching_and_summary_helpers(self) -> None:
        support = MethodologyEvolverMemorySupport()

        self.assertIsInstance(support.protocol_support, MethodologyEvolverProtocolSupport)
        self.assertIsInstance(support.matching_support, MethodologyEvolverMemoryMatching)
        self.assertIsInstance(support.summary_support, MethodologyEvolverMemorySummary)


if __name__ == "__main__":
    unittest.main()
