from __future__ import annotations

from research_system.schemas.review import PosteriorReviewSummary, ReviewFinding


class ScreenReviewerService:
    agent_name = "screen_reviewer"

    def run(
        self,
        *,
        source_run_id: str,
        candidates: list[dict],
        scorecard: dict | None = None,
    ) -> dict:
        findings: list[dict] = []
        notes: list[str] = []

        for candidate in candidates:
            symbol = str(candidate.get("symbol") or "")
            claim_id = f"screen_candidate:{symbol}"
            candidate_tier = str(candidate.get("candidate_tier") or "core")
            missing_evidence = list(candidate.get("missing_evidence") or [])
            degraded_sources = list(candidate.get("degraded_sources") or [])
            latest_pct_change = candidate.get("latest_pct_change")
            deep_checked = bool(candidate.get("deep_checked"))
            main_risk = str(candidate.get("main_risk") or "")

            verdict = "confirmed"
            if missing_evidence or degraded_sources or not deep_checked:
                verdict = "inconclusive"

            finding = ReviewFinding(
                claim_id=claim_id,
                claim_type="catalyst",
                source_run_id=source_run_id,
                target_entry_id=claim_id,
                window_type="trading_days",
                window_size=5,
                anchor_date="",
                evaluation_start_rule="same_run_screening",
                benchmark_scope="screen_candidate",
                benchmark_symbol=symbol or None,
                benchmark_name=str(candidate.get("name") or symbol or "candidate"),
                observed_metric=self._to_float(candidate.get("score")),
                verdict=verdict,
                notes=self._finding_notes(
                    candidate_tier=candidate_tier,
                    missing_evidence=missing_evidence,
                    degraded_sources=degraded_sources,
                    deep_checked=deep_checked,
                    latest_pct_change=latest_pct_change,
                    main_risk=main_risk,
                ),
            )
            findings.append(finding.model_dump(mode="json"))

        if not candidates:
            notes.append("no_screen_candidates")

        if scorecard is not None:
            notes.append(
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
                "screen_candidate": "按筛选候选的证据完整度、降级状态和主风险做轻量复核。"
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
            notes=notes,
        )

        return {
            "summary": summary.model_dump(mode="json"),
            "findings": findings,
        }

    def _finding_notes(
        self,
        *,
        candidate_tier: str,
        missing_evidence: list[str],
        degraded_sources: list[str],
        deep_checked: bool,
        latest_pct_change,
        main_risk: str,
    ) -> str:
        parts = [f"candidate_tier={candidate_tier}", f"deep_checked={str(deep_checked).lower()}"]
        if missing_evidence:
            parts.append(f"missing_evidence={','.join(missing_evidence)}")
        if degraded_sources:
            parts.append(f"degraded_sources={','.join(degraded_sources)}")
        if latest_pct_change is not None:
            parts.append(f"latest_pct_change={latest_pct_change}")
        if main_risk:
            parts.append(f"main_risk={main_risk}")
        return "; ".join(parts)

    def _to_float(self, value):
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None
