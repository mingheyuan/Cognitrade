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
        for entry in entries:
            payload = entry.get("payload") or {}
            methodology_result = payload.get("methodology_result") or {}
            candidates.append(
                {
                    "candidate_type": "valuation_heuristic",
                    "run_id": run_id,
                    "entry_id": entry.get("entry_id"),
                    "title": str(payload.get("title") or payload.get("methodology_type") or "valuation"),
                    "methodology_type": payload.get("methodology_type"),
                    "symbol": payload.get("symbol"),
                    "assumption_tags": list(payload.get("assumption_tags") or []),
                    "sensitivity_tags": list(payload.get("sensitivity_tags") or []),
                    "valuation_band_label": payload.get("valuation_band_label"),
                    "confidence": payload.get("confidence"),
                    "evidence_summary": {
                        "warnings": list(methodology_result.get("warnings") or []),
                        "outputs": dict(methodology_result.get("outputs") or {}),
                    },
                    "memory_context": self.memory_support.matching_memory_context(
                        memory_rows=memory_rows,
                        memory_search_rows=memory_search_rows,
                        title=str(payload.get("title") or payload.get("methodology_type") or "valuation"),
                        methodology_type=str(payload.get("methodology_type") or "").strip() or None,
                    ),
                }
            )
        return candidates


__all__ = ["MethodologyEvolverValuationCandidates"]
