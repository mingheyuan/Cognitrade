from __future__ import annotations

from research_system.schemas.review import ReviewFinding


class ScreenReviewerFindingSupport:
    def build_finding(
        self,
        *,
        source_run_id: str,
        candidate: dict,
    ) -> dict:
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
            observed_metric=self.to_float(candidate.get("score")),
            verdict=verdict,
            notes=self.finding_notes(
                candidate_tier=candidate_tier,
                missing_evidence=missing_evidence,
                degraded_sources=degraded_sources,
                deep_checked=deep_checked,
                latest_pct_change=latest_pct_change,
                main_risk=main_risk,
            ),
        )
        return finding.model_dump(mode="json")

    def finding_notes(
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

    def to_float(self, value):
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None
