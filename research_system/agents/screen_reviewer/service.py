from __future__ import annotations

from research_system.schemas.review import PosteriorReviewSummary, ReviewFinding
from research_system.services.planning_context import (
    build_planning_context_view,
    combine_focus_text,
    derive_methodology_focus,
    has_momentum_focus,
    normalize_planning_context_hints,
)


class ScreenReviewerService:
    agent_name = "screen_reviewer"

    def run(
        self,
        *,
        source_run_id: str,
        candidates: list[dict],
        scorecard: dict | None = None,
        planning_context_hints: dict[str, str] | None = None,
    ) -> dict:
        findings: list[dict] = []
        notes: list[str] = []
        normalized_planning_hints = self._normalize_planning_context_hints(planning_context_hints)
        review_strategy = self._derive_review_strategy(
            candidates=candidates,
            scorecard=scorecard,
            planning_context_hints=normalized_planning_hints,
        )
        notes.extend(list(review_strategy.get("notes") or []))

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

    def _normalize_planning_context_hints(self, raw_hints) -> dict[str, str]:
        return normalize_planning_context_hints(raw_hints)

    def _derive_review_strategy(
        self,
        *,
        candidates: list[dict],
        scorecard: dict | None,
        planning_context_hints: dict[str, str],
    ) -> dict[str, object]:
        strategy: dict[str, object] = {
            "focus_area": None,
            "preferred_methodology": None,
            "notes": [],
        }
        if not planning_context_hints:
            return strategy
        candidate_text = " ".join(
            str(candidate.get("main_risk") or "").strip()
            for candidate in candidates
            if str(candidate.get("main_risk") or "").strip()
        )
        scorecard_summary = str((scorecard or {}).get("summary") or "").strip()
        context = build_planning_context_view(
            query=scorecard_summary,
            planning_context_hints=planning_context_hints,
            extra_texts=[candidate_text],
        )
        lowered = combine_focus_text(
            context.query_text,
            candidate_text,
            context.recommended_next_step,
            context.open_question,
            context.previous_summary,
            context.thread_topic,
        )
        preferred_methodology, focus_source = derive_methodology_focus(
            cashflow_text=lowered,
            comparison_text=lowered,
            planning_context_hints=planning_context_hints,
        )
        if preferred_methodology == "cashflow_resilience":
            strategy["focus_area"] = "cashflow_resilience"
            strategy["preferred_methodology"] = preferred_methodology
        elif preferred_methodology == "comps":
            strategy["focus_area"] = "same_basis_compare"
            strategy["preferred_methodology"] = preferred_methodology
        elif preferred_methodology == "dcf":
            strategy["focus_area"] = "valuation"
            strategy["preferred_methodology"] = preferred_methodology
        elif has_momentum_focus(lowered):
            strategy["focus_area"] = "momentum_validation"
        focus_area = str(strategy.get("focus_area") or "").strip()
        preferred_methodology = str(strategy.get("preferred_methodology") or "").strip()
        if focus_area:
            strategy["notes"] = [f"screen_review_focus={focus_area}"]
            if preferred_methodology:
                strategy["notes"].append(f"screen_review_preferred_methodology={preferred_methodology}")
            if focus_source:
                strategy["notes"].append(f"screen_review_focus_source={focus_source}")
        return strategy

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
