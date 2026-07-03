from __future__ import annotations

from research_system.schemas.review import PosteriorReviewSummary


class ScreenReviewerSummarySupport:
    BENCHMARK_SCOPE_EXPLANATION = "按筛选候选的证据完整度、降级状态和主风险做轻量复核。"

    def build_review_summary(
        self,
        *,
        candidates: list[dict],
        findings: list[dict],
        notes: list[str],
        scorecard: dict | None,
    ) -> dict:
        summary_notes = list(notes)
        if not candidates:
            summary_notes.append("no_screen_candidates")
        if scorecard is not None:
            summary_notes.append(
                "core_candidates="
                f"{int(scorecard.get('top_candidate_count') or 0)}"
                f"; watchlist_candidates={int(scorecard.get('watchlist_candidate_count') or 0)}"
            )

        confirmed = sum(1 for item in findings if item["verdict"] == "confirmed")
        inconclusive = sum(1 for item in findings if item["verdict"] == "inconclusive")
        rejected = sum(1 for item in findings if item["verdict"] == "rejected")

        if not findings:
            overall_verdict = "no_findings"
        elif inconclusive == 0 and rejected == 0:
            overall_verdict = "confirmed"
        else:
            overall_verdict = "confirmed_with_caveats"

        summary = PosteriorReviewSummary(
            review_required=inconclusive > 0,
            finding_count=len(findings),
            reviewable_claim_count=len(candidates),
            claim_type_counts={"catalyst": len(findings)} if findings else {},
            benchmark_scope_counts={"screen_candidate": len(findings)} if findings else {},
            benchmark_scope_explanations={
                "screen_candidate": self.BENCHMARK_SCOPE_EXPLANATION,
            }
            if findings
            else {},
            verdict_by_claim_type={
                "catalyst": {
                    "confirmed": confirmed,
                    "rejected": rejected,
                    "inconclusive": inconclusive,
                }
            }
            if findings
            else {},
            overall_verdict=overall_verdict,
            summary_text=(
                f"overall_verdict={overall_verdict}; findings={len(findings)}; "
                f"confirmed={confirmed}; rejected={rejected}; inconclusive={inconclusive}; "
                "benchmark_scopes=screen_candidate"
            ),
            confirmed=confirmed,
            rejected=rejected,
            inconclusive=inconclusive,
            notes=summary_notes,
        )
        return summary.model_dump(mode="json")
