from __future__ import annotations

from research_system.schemas.review import ClaimVerdictBreakdown, PosteriorReviewSummary, ReviewFinding


class ReviewerSummarySupport:
    def build_review_summary(
        self,
        *,
        review_required: bool,
        findings: list[ReviewFinding],
        candidates: list[dict],
        notes: list[str],
        claim_type_counts: dict[str, int],
    ) -> dict:
        verdict_by_claim_type = self.verdict_by_claim_type(findings)
        benchmark_scope_counts = self.benchmark_scope_counts(findings)
        confirmed, rejected, inconclusive = self.verdict_totals(verdict_by_claim_type)
        overall_verdict = self.overall_verdict(
            review_required=review_required,
            finding_count=len(findings),
            confirmed=confirmed,
            rejected=rejected,
            inconclusive=inconclusive,
        )
        summary = PosteriorReviewSummary(
            review_required=review_required,
            finding_count=len(findings),
            reviewable_claim_count=len(candidates),
            claim_type_counts=claim_type_counts,
            benchmark_scope_counts=benchmark_scope_counts,
            benchmark_scope_explanations=self.benchmark_scope_explanations(benchmark_scope_counts),
            verdict_by_claim_type=verdict_by_claim_type,
            overall_verdict=overall_verdict,
            summary_text=self.summary_text(
                overall_verdict=overall_verdict,
                finding_count=len(findings),
                confirmed=confirmed,
                rejected=rejected,
                inconclusive=inconclusive,
                benchmark_scope_counts=benchmark_scope_counts,
            ),
            confirmed=confirmed,
            rejected=rejected,
            inconclusive=inconclusive,
            notes=notes,
        )
        return {"summary": summary.model_dump(mode="json")}

    def verdict_by_claim_type(self, findings: list[ReviewFinding]) -> dict[str, ClaimVerdictBreakdown]:
        buckets: dict[str, ClaimVerdictBreakdown] = {}
        for finding in findings:
            claim_type = str(finding.claim_type or "unknown")
            verdict = str(finding.verdict)
            if claim_type not in buckets:
                buckets[claim_type] = ClaimVerdictBreakdown()
            current = buckets[claim_type]
            if verdict == "confirmed":
                current.confirmed += 1
            elif verdict == "rejected":
                current.rejected += 1
            else:
                current.inconclusive += 1
        return buckets

    def verdict_totals(self, verdict_by_claim_type: dict[str, ClaimVerdictBreakdown]) -> tuple[int, int, int]:
        confirmed = sum(item.confirmed for item in verdict_by_claim_type.values())
        rejected = sum(item.rejected for item in verdict_by_claim_type.values())
        inconclusive = sum(item.inconclusive for item in verdict_by_claim_type.values())
        return confirmed, rejected, inconclusive

    def benchmark_scope_counts(self, findings: list[ReviewFinding]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for finding in findings:
            scope = str(finding.benchmark_scope or "unknown")
            counts[scope] = counts.get(scope, 0) + 1
        return counts

    def benchmark_scope_explanations(self, benchmark_scope_counts: dict[str, int]) -> dict[str, str]:
        explanations: dict[str, str] = {}
        for scope in benchmark_scope_counts:
            explanations[scope] = self.benchmark_scope_explanation(scope)
        return explanations

    def benchmark_scope_explanation(self, scope: str) -> str:
        if scope == "stock_vs_sector":
            return "个股方向主张按个股相对所属板块表现复核。"
        if scope == "sector_vs_hs300":
            return "板块方向主张按板块相对 HS300 表现复核。"
        if scope == "stock_absolute":
            return "缺少板块基准时，个股方向主张按个股自身涨跌做最小复核。"
        return "按当前 review policy 的默认 benchmark 口径复核。"

    def overall_verdict(
        self,
        *,
        review_required: bool,
        finding_count: int,
        confirmed: int,
        rejected: int,
        inconclusive: int,
    ) -> str:
        if review_required:
            return "needs_review"
        if finding_count == 0:
            return "no_findings"
        if rejected > 0 and confirmed == 0:
            return "rejected"
        if rejected > 0 and confirmed > 0:
            return "mixed"
        if inconclusive > 0 and confirmed == 0:
            return "inconclusive"
        if confirmed > 0 and inconclusive > 0:
            return "confirmed_with_caveats"
        if confirmed > 0:
            return "confirmed"
        return "inconclusive"

    def summary_text(
        self,
        *,
        overall_verdict: str,
        finding_count: int,
        confirmed: int,
        rejected: int,
        inconclusive: int,
        benchmark_scope_counts: dict[str, int],
    ) -> str:
        scope_labels = ",".join(sorted(benchmark_scope_counts.keys())) if benchmark_scope_counts else "no_benchmark"
        return (
            f"overall_verdict={overall_verdict}; findings={finding_count}; "
            f"confirmed={confirmed}; rejected={rejected}; inconclusive={inconclusive}; "
            f"benchmark_scopes={scope_labels}"
        )
