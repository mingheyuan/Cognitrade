from __future__ import annotations

from typing import Any

from research_system.services.methodologies.evolver.methodology_evolver_aggregation import MethodologyEvolverAggregation
from research_system.services.methodologies.evolver.methodology_evolver_extraction import MethodologyEvolverExtraction
from research_system.services.methodologies.evolver.methodology_evolver_memory_support import MethodologyEvolverMemorySupport


class MethodologyEvolver:
    def __init__(
        self,
        *,
        memory_support: MethodologyEvolverMemorySupport | None = None,
        extraction: MethodologyEvolverExtraction | None = None,
        aggregation: MethodologyEvolverAggregation | None = None,
    ) -> None:
        self.memory_support = memory_support or MethodologyEvolverMemorySupport()
        self.extraction = extraction or MethodologyEvolverExtraction(memory_support=self.memory_support)
        self.aggregation = aggregation or MethodologyEvolverAggregation(
            extraction=self.extraction,
            memory_support=self.memory_support,
        )

    def build_candidates_from_runs(
        self,
        *,
        runs: list[dict[str, Any]],
        methodology_memory_rows: list[dict[str, Any]] | None = None,
        methodology_memory_search_rows: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        return self.aggregation.build_candidates_from_runs(
            runs=runs,
            methodology_memory_rows=methodology_memory_rows,
            methodology_memory_search_rows=methodology_memory_search_rows,
        )

    def build_candidates(
        self,
        *,
        run_id: str,
        blackboard_entries: list[dict],
        methodology_memory_rows: list[dict[str, Any]] | None = None,
        methodology_memory_search_rows: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        return self.extraction.build_candidates(
            run_id=run_id,
            blackboard_entries=blackboard_entries,
            methodology_memory_rows=methodology_memory_rows,
            methodology_memory_search_rows=methodology_memory_search_rows,
        )
