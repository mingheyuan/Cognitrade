from __future__ import annotations

from typing import Any

from research_system.services.methodologies.evolver.methodology_evolver_memory_matching import MethodologyEvolverMemoryMatching
from research_system.services.methodologies.evolver.methodology_evolver_memory_summary import MethodologyEvolverMemorySummary
from research_system.services.methodologies.evolver.methodology_evolver_protocol_support import MethodologyEvolverProtocolSupport


class MethodologyEvolverMemorySupport:
    def __init__(self) -> None:
        self.protocol_support = MethodologyEvolverProtocolSupport()
        self.matching_support = MethodologyEvolverMemoryMatching(protocol_support=self.protocol_support)
        self.summary_support = MethodologyEvolverMemorySummary()

    def matching_memory_context(
        self,
        *,
        memory_rows: list[dict[str, Any]],
        memory_search_rows: list[dict[str, Any]],
        title: str,
        dimension_family: str | None = None,
        methodology_type: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.matching_support.matching_memory_context(
            memory_rows=memory_rows,
            memory_search_rows=memory_search_rows,
            title=title,
            dimension_family=dimension_family,
            methodology_type=methodology_type,
        )

    def dedupe_memory_context(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.summary_support.dedupe_memory_context(items)

    def memory_support_summary(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        return self.summary_support.memory_support_summary(items)

    def protocol_value(self, row: dict[str, Any], key: str) -> str | None:
        return self.protocol_support.protocol_value(row, key)

    def average(self, values: list[Any]) -> float | None:
        return self.summary_support.average(values)
