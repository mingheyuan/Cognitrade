from __future__ import annotations

from typing import Any

from research_system.services.methodologies.evolver.methodology_evolver_dimension_aggregation import (
    MethodologyEvolverDimensionAggregation,
)
from research_system.services.methodologies.evolver.methodology_evolver_extraction import MethodologyEvolverExtraction
from research_system.services.methodologies.evolver.methodology_evolver_memory_support import MethodologyEvolverMemorySupport
from research_system.services.methodologies.evolver.methodology_evolver_review_aggregation import (
    MethodologyEvolverReviewAggregation,
)
from research_system.services.methodologies.evolver.methodology_evolver_valuation_aggregation import (
    MethodologyEvolverValuationAggregation,
)


class MethodologyEvolverAggregation:
    def __init__(
        self,
        *,
        extraction: MethodologyEvolverExtraction | None = None,
        memory_support: MethodologyEvolverMemorySupport | None = None,
    ) -> None:
        self.memory_support = memory_support or MethodologyEvolverMemorySupport()
        self.extraction = extraction or MethodologyEvolverExtraction(memory_support=self.memory_support)
        self.dimension_aggregation = MethodologyEvolverDimensionAggregation(
            memory_support=self.memory_support
        )
        self.valuation_aggregation = MethodologyEvolverValuationAggregation(
            memory_support=self.memory_support
        )
        self.review_aggregation = MethodologyEvolverReviewAggregation()

    def build_candidates_from_runs(
        self,
        *,
        runs: list[dict[str, Any]],
        methodology_memory_rows: list[dict[str, Any]] | None = None,
        methodology_memory_search_rows: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        memory_rows = methodology_memory_rows or []
        memory_search_rows = methodology_memory_search_rows or []
        aggregated_candidates: list[dict[str, Any]] = []
        dimension_groups: dict[tuple[str, str], dict[str, Any]] = {}
        valuation_groups: dict[tuple[str, str], dict[str, Any]] = {}
        review_patterns: list[dict[str, Any]] = []

        for run in runs:
            run_id = str(run.get("run_id") or "").strip()
            blackboard_entries = list(run.get("blackboard_entries") or [])
            for candidate in self.extraction.build_candidates(
                run_id=run_id,
                blackboard_entries=blackboard_entries,
                methodology_memory_rows=memory_rows,
                methodology_memory_search_rows=memory_search_rows,
            ):
                candidate_type = str(candidate.get("candidate_type") or "")
                if candidate_type == "dimension":
                    self.dimension_aggregation.accumulate(dimension_groups, candidate, run_id)
                elif candidate_type == "valuation_heuristic":
                    self.valuation_aggregation.accumulate(valuation_groups, candidate, run_id)
                elif candidate_type == "review_pattern":
                    self.review_aggregation.accumulate(review_patterns, candidate, run_id)

        aggregated_candidates.extend(self.dimension_aggregation.build_candidates(dimension_groups))
        aggregated_candidates.extend(self.valuation_aggregation.build_candidates(valuation_groups))
        review_pattern_candidate = self.review_aggregation.build_candidate(review_patterns)
        if review_pattern_candidate is not None:
            aggregated_candidates.append(review_pattern_candidate)
        return aggregated_candidates
