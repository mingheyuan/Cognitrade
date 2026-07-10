from __future__ import annotations

from typing import Any


class MethodologyEvolverValuationCandidates:
    def __init__(self, *, memory_support) -> None:
        self.memory_support = memory_support

    def build_candidates(
        self,
        *,
        run_id: str,
        entries: list[dict],
        memory_rows: list[dict[str, Any]],
        memory_search_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for artifact in entries:
            content = artifact.get("content") or {}
            methodology_result = content.get("methodology_result") or {}
            candidates.append(
                {
                    "candidate_type": "valuation_heuristic",
                    "run_id": run_id,
                    "artifact_id": artifact.get("artifact_id"),
                    "title": str(content.get("title") or content.get("methodology_type") or "valuation"),
                    "methodology_type": content.get("methodology_type"),
                    "symbol": content.get("symbol"),
                    "assumption_tags": list(content.get("assumption_tags") or []),
                    "sensitivity_tags": list(content.get("sensitivity_tags") or []),
                    "valuation_band_label": content.get("valuation_band_label"),
                    "confidence": artifact.get("confidence", content.get("confidence")),
                    "evidence_summary": {
                        "warnings": list(methodology_result.get("warnings") or []),
                        "outputs": dict(methodology_result.get("outputs") or {}),
                    },
                    "memory_context": self.memory_support.matching_memory_context(
                        memory_rows=memory_rows,
                        memory_search_rows=memory_search_rows,
                        title=str(content.get("title") or content.get("methodology_type") or "valuation"),
                        methodology_type=str(content.get("methodology_type") or "").strip() or None,
                    ),
                }
            )
        return candidates


__all__ = ["MethodologyEvolverValuationCandidates"]
