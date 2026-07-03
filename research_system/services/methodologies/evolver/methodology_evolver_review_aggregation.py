from __future__ import annotations


class MethodologyEvolverReviewAggregation:
    def accumulate(self, patterns: list[dict], candidate: dict, run_id: str) -> None:
        enriched = dict(candidate)
        enriched["run_ids"] = [run_id] if run_id else []
        patterns.append(enriched)

    def build_candidate(self, patterns: list[dict]) -> dict | None:
        if not patterns:
            return None
        severity_counts: dict[str, int] = {}
        verdict_counts: dict[str, int] = {}
        sample_size = 0
        run_ids: list[str] = []
        for candidate in patterns:
            sample_size += int(candidate.get("sample_size") or 0)
            for severity, count in (candidate.get("severity_counts") or {}).items():
                severity_text = str(severity).strip()
                if severity_text:
                    severity_counts[severity_text] = severity_counts.get(severity_text, 0) + int(count)
            for verdict, count in (candidate.get("verdict_counts") or {}).items():
                verdict_text = str(verdict).strip()
                if verdict_text:
                    verdict_counts[verdict_text] = verdict_counts.get(verdict_text, 0) + int(count)
            for run_id in candidate.get("run_ids") or []:
                run_id_text = str(run_id).strip()
                if run_id_text and run_id_text not in run_ids:
                    run_ids.append(run_id_text)
        return {
            "candidate_type": "review_pattern",
            "title": "review_pattern_summary",
            "sample_size": sample_size,
            "related_run_ids": run_ids,
            "severity_counts": severity_counts,
            "verdict_counts": verdict_counts,
        }
