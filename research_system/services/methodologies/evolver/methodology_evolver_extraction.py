from __future__ import annotations

from typing import Any

from research_system.services.methodologies.evolver.methodology_evolver_dimension_candidates import MethodologyEvolverDimensionCandidates
from research_system.services.methodologies.evolver.methodology_evolver_memory_support import MethodologyEvolverMemorySupport
from research_system.services.methodologies.evolver.methodology_evolver_review_candidates import MethodologyEvolverReviewCandidates
from research_system.services.methodologies.evolver.methodology_evolver_valuation_candidates import MethodologyEvolverValuationCandidates


class MethodologyEvolverExtraction:
    def __init__(self, *, memory_support: MethodologyEvolverMemorySupport | None = None) -> None:
        self.memory_support = memory_support or MethodologyEvolverMemorySupport()
        self.dimension_candidates = MethodologyEvolverDimensionCandidates(memory_support=self.memory_support)
        self.valuation_candidates = MethodologyEvolverValuationCandidates(memory_support=self.memory_support)
        self.review_candidates = MethodologyEvolverReviewCandidates()

    def build_candidates(
        self,
        *,
        run_id: str,
        blackboard_entries: list[dict],
        methodology_memory_rows: list[dict[str, Any]] | None = None,
        methodology_memory_search_rows: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        memory_rows = methodology_memory_rows or []
        memory_search_rows = methodology_memory_search_rows or []
        dimension_entries = [
            entry for entry in blackboard_entries
            if entry.get("kind") == "distilled_dimension"
        ]
        methodology_entries = [
            entry for entry in blackboard_entries
            if entry.get("kind") == "source_record"
            and str((entry.get("payload") or {}).get("source_type") or "").startswith("methodology_")
        ]
        review_findings = [
            entry for entry in blackboard_entries
            if entry.get("kind") == "review_finding"
        ]

        candidates.extend(
            self.dimension_candidates.build_candidates(
                run_id=run_id,
                entries=dimension_entries,
                memory_rows=memory_rows,
                memory_search_rows=memory_search_rows,
            )
        )
        candidates.extend(
            self.valuation_candidates.build_candidates(
                run_id=run_id,
                entries=methodology_entries,
                memory_rows=memory_rows,
                memory_search_rows=memory_search_rows,
            )
        )
        candidates.extend(
            self.review_candidates.build_candidates(
                run_id=run_id,
                entries=review_findings,
            )
        )
        return candidates
