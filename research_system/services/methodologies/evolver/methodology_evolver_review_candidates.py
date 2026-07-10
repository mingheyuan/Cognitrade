from __future__ import annotations


class MethodologyEvolverReviewCandidates:
    def build_candidates(self, *, run_id: str, entries: list[dict]) -> list[dict]:
        if not entries:
            return []
        severity_counts: dict[str, int] = {}
        verdict_counts: dict[str, int] = {}
        for artifact in entries:
            content = artifact.get("content") or {}
            severity = str(content.get("severity") or "")
            verdict = str(content.get("verdict") or "")
            if severity:
                severity_counts[severity] = severity_counts.get(severity, 0) + 1
            if verdict:
                verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        return [
            {
                "candidate_type": "review_pattern",
                "run_id": run_id,
                "title": "review_pattern_summary",
                "severity_counts": severity_counts,
                "verdict_counts": verdict_counts,
                "sample_size": len(entries),
            }
        ]


__all__ = ["MethodologyEvolverReviewCandidates"]
