from __future__ import annotations


class MethodologyEvolverDimensionAggregation:
    def __init__(self, *, memory_support) -> None:
        self.memory_support = memory_support

    def accumulate(self, groups: dict[tuple[str, str], dict], candidate: dict, run_id: str) -> None:
        title = str(candidate.get("title") or "").strip()
        family = str(candidate.get("dimension_family") or "").strip()
        key = (title, family)
        group = groups.setdefault(
            key,
            {
                "candidate_type": "dimension",
                "title": title,
                "dimension_family": family,
                "scenario_tags": set(),
                "methodology_tags": set(),
                "run_ids": [],
                "related_artifact_ids": [],
                "supported_claim_count": 0,
                "evidence_block_count": 0,
                "confidence_values": [],
                "memory_context": [],
                "memory_match_scores": [],
            },
        )
        group["scenario_tags"].update(candidate.get("scenario_tags") or [])
        group["methodology_tags"].update(candidate.get("methodology_tags") or [])
        if run_id and run_id not in group["run_ids"]:
            group["run_ids"].append(run_id)
        artifact_id = str(candidate.get("artifact_id") or "").strip()
        if artifact_id:
            group["related_artifact_ids"].append(artifact_id)
        evidence_summary = candidate.get("evidence_summary") or {}
        group["supported_claim_count"] += len(evidence_summary.get("supported_claim_block_ids") or [])
        group["evidence_block_count"] += len(evidence_summary.get("evidence_block_ids") or [])
        confidence = candidate.get("confidence")
        if confidence is not None:
            group["confidence_values"].append(confidence)
        for item in candidate.get("memory_context") or []:
            group["memory_context"].append(item)
            if item.get("match_score") is not None:
                group["memory_match_scores"].append(item.get("match_score"))

    def build_candidates(self, groups: dict[tuple[str, str], dict]) -> list[dict]:
        aggregated_candidates: list[dict] = []
        for (title, family), group in groups.items():
            confidence_values = list(group.pop("confidence_values"))
            memory_context = self.memory_support.dedupe_memory_context(group.pop("memory_context"))
            memory_match_scores = list(group.pop("memory_match_scores"))
            run_ids = list(group["run_ids"])
            aggregated_candidates.append(
                {
                    "candidate_type": "dimension",
                    "run_id": run_ids[0] if len(run_ids) == 1 else None,
                    "title": title,
                    "dimension_family": family,
                    "scenario_tags": sorted(group["scenario_tags"]),
                    "methodology_tags": sorted(group["methodology_tags"]),
                    "sample_size": len(run_ids),
                    "related_run_ids": run_ids,
                    "related_artifact_ids": list(group["related_artifact_ids"]),
                    "aggregated_evidence_summary": {
                        "supported_claim_count": group["supported_claim_count"],
                        "evidence_block_count": group["evidence_block_count"],
                    },
                    "confidence": self.memory_support.average(confidence_values),
                    "memory_match_score": self.memory_support.average(memory_match_scores),
                    "memory_support": self.memory_support.memory_support_summary(memory_context),
                    "memory_context": memory_context,
                }
            )
        return aggregated_candidates
