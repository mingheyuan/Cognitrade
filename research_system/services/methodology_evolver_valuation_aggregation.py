from __future__ import annotations


class MethodologyEvolverValuationAggregation:
    def __init__(self, *, memory_support) -> None:
        self.memory_support = memory_support

    def accumulate(self, groups: dict[tuple[str, str], dict], candidate: dict, run_id: str) -> None:
        methodology_type = str(candidate.get("methodology_type") or "").strip()
        title = str(candidate.get("title") or methodology_type or "").strip()
        key = (methodology_type, title)
        group = groups.setdefault(
            key,
            {
                "candidate_type": "valuation_heuristic",
                "title": title,
                "methodology_type": methodology_type,
                "assumption_tags": set(),
                "sensitivity_tags": set(),
                "run_ids": [],
                "symbols": set(),
                "related_entry_ids": [],
                "warnings_count": 0,
                "confidence_values": [],
                "memory_context": [],
                "memory_match_scores": [],
            },
        )
        group["assumption_tags"].update(candidate.get("assumption_tags") or [])
        group["sensitivity_tags"].update(candidate.get("sensitivity_tags") or [])
        if run_id and run_id not in group["run_ids"]:
            group["run_ids"].append(run_id)
        symbol = str(candidate.get("symbol") or "").strip()
        if symbol:
            group["symbols"].add(symbol)
        entry_id = str(candidate.get("entry_id") or "").strip()
        if entry_id:
            group["related_entry_ids"].append(entry_id)
        evidence_summary = candidate.get("evidence_summary") or {}
        group["warnings_count"] += len(evidence_summary.get("warnings") or [])
        confidence = candidate.get("confidence")
        if confidence is not None:
            group["confidence_values"].append(confidence)
        for item in candidate.get("memory_context") or []:
            group["memory_context"].append(item)
            if item.get("match_score") is not None:
                group["memory_match_scores"].append(item.get("match_score"))

    def build_candidates(self, groups: dict[tuple[str, str], dict]) -> list[dict]:
        aggregated_candidates: list[dict] = []
        for (methodology_type, title), group in groups.items():
            confidence_values = list(group.pop("confidence_values"))
            memory_context = self.memory_support.dedupe_memory_context(group.pop("memory_context"))
            memory_match_scores = list(group.pop("memory_match_scores"))
            run_ids = list(group["run_ids"])
            aggregated_candidates.append(
                {
                    "candidate_type": "valuation_heuristic",
                    "run_id": run_ids[0] if len(run_ids) == 1 else None,
                    "title": title,
                    "methodology_type": methodology_type,
                    "assumption_tags": sorted(group["assumption_tags"]),
                    "sensitivity_tags": sorted(group["sensitivity_tags"]),
                    "sample_size": len(run_ids),
                    "related_run_ids": run_ids,
                    "symbols": sorted(group["symbols"]),
                    "related_entry_ids": list(group["related_entry_ids"]),
                    "aggregated_evidence_summary": {
                        "warnings_count": group["warnings_count"],
                        "symbol_count": len(group["symbols"]),
                    },
                    "confidence": self.memory_support.average(confidence_values),
                    "memory_match_score": self.memory_support.average(memory_match_scores),
                    "memory_support": self.memory_support.memory_support_summary(memory_context),
                    "memory_context": memory_context,
                }
            )
        return aggregated_candidates

