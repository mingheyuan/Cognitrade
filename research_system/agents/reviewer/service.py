from __future__ import annotations

from typing import Any

from research_system.agents.reviewer.reviewer_candidate_support import ReviewerCandidateSupport
from research_system.agents.reviewer.reviewer_strategy_support import ReviewerStrategySupport
from research_system.agents.reviewer.reviewer_summary_support import ReviewerSummarySupport
from research_system.agents.reviewer.reviewer_verdict_support import ReviewerVerdictSupport
from research_system.schemas.review import ClaimVerdictBreakdown, ReviewFinding


class ReviewerService:
    agent_name = "reviewer"

    def __init__(self, review_policy: dict | None = None) -> None:
        self.review_policy = review_policy or {}
        self.strategy_support = ReviewerStrategySupport(self.review_policy)
        self.candidate_support = ReviewerCandidateSupport()
        self.verdict_support = ReviewerVerdictSupport(strategy_support=self.strategy_support)
        self.summary_support = ReviewerSummarySupport()

    def run(
        self,
        *,
        source_run_id: str,
        query: str,
        source_entries: list[dict],
        latest_sector_snapshot: dict | None,
        latest_market_snapshot: dict | None = None,
        latest_fundamental_snapshot: dict | None = None,
        latest_notice_snapshot: dict | None = None,
        planning_context_hints: dict[str, str] | None = None,
    ) -> dict:
        normalized_planning_hints = self._normalize_planning_context_hints(planning_context_hints)
        review_strategy = self._derive_review_strategy(
            query=query,
            planning_context_hints=normalized_planning_hints,
        )
        claim_outputs = self._extract_review_claims(source_entries=source_entries)
        candidates = claim_outputs["candidates"]
        claim_type_counts = claim_outputs["claim_type_counts"]
        findings: list[ReviewFinding] = []
        notes: list[str] = list(review_strategy.get("notes") or [])

        if not candidates:
            notes.append("no_reviewable_claims_detected")

        if latest_sector_snapshot is None:
            notes.append("missing_sector_snapshot")
            summary_outputs = self._build_review_summary(
                review_required=True,
                findings=[],
                candidates=candidates,
                notes=notes,
                claim_type_counts=claim_type_counts,
            )
            return {
                "summary": summary_outputs["summary"],
                "findings": [],
            }

        facts = latest_sector_snapshot.get("facts") or {}
        relative_strength = facts.get("relative_strength") or {}
        signal_scorecard = facts.get("signal_scorecard") or {}
        benchmark = facts.get("benchmark") or {}
        latest_hist = facts.get("latest_hist") or {}
        catalyst_digest = facts.get("catalyst_digest") or {}
        policy_signals = facts.get("policy_signals") or []
        regulatory_signals = facts.get("regulatory_signals") or []
        theme_news = facts.get("theme_news") or []

        excess_metric = self._to_float(relative_strength.get("latest_excess_pct_change"))
        benchmark_outputs = self._resolve_benchmark(
            latest_sector_snapshot=latest_sector_snapshot,
            latest_market_snapshot=latest_market_snapshot,
            default_benchmark_name=str(benchmark.get("benchmark_name") or self.review_policy.get("default_broad_index") or "HS300"),
            default_benchmark_symbol=str(benchmark.get("benchmark_code") or "000300"),
        )
        benchmark_scope = benchmark_outputs["benchmark_scope"]
        benchmark_name = benchmark_outputs["benchmark_name"]
        benchmark_symbol = benchmark_outputs["benchmark_symbol"]
        observed_benchmark_metric = benchmark_outputs["observed_benchmark_metric"]
        anchor_date = self._parse_anchor_date(
            candidates=candidates,
            snapshot_date=str(latest_sector_snapshot.get("snapshot_date") or ""),
        )
        setup_label = str(signal_scorecard.get("setup_label") or "")
        sector_name = str(latest_sector_snapshot.get("sector") or "")

        for candidate in candidates:
            finding_outputs = self._review_candidate(
                candidate=candidate,
                source_run_id=source_run_id,
                anchor_date=anchor_date,
                benchmark_scope=benchmark_scope,
                benchmark_symbol=benchmark_symbol,
                benchmark_name=benchmark_name,
                observed_benchmark_metric=observed_benchmark_metric,
                latest_hist=latest_hist,
                latest_market_snapshot=latest_market_snapshot,
                latest_fundamental_snapshot=latest_fundamental_snapshot,
                latest_notice_snapshot=latest_notice_snapshot,
                excess_metric=excess_metric,
                policy_signals=policy_signals,
                regulatory_signals=regulatory_signals,
                theme_news=theme_news,
                setup_label=setup_label,
                sector_name=sector_name,
                catalyst_digest=catalyst_digest,
            )
            findings.append(ReviewFinding.model_validate(finding_outputs["finding"]))

        focus_validation = self._validate_review_strategy_requirements(
            review_strategy=review_strategy,
            latest_market_snapshot=latest_market_snapshot,
            latest_fundamental_snapshot=latest_fundamental_snapshot,
            latest_notice_snapshot=latest_notice_snapshot,
            policy_signals=policy_signals,
            regulatory_signals=regulatory_signals,
        )
        notes.extend(focus_validation["notes"])
        summary_outputs = self._build_review_summary(
            review_required=bool(focus_validation["review_required"]),
            findings=findings,
            candidates=candidates,
            notes=notes,
            claim_type_counts=claim_type_counts,
        )
        return {
            "summary": summary_outputs["summary"],
            "findings": [finding.model_dump(mode="json") for finding in findings],
        }

    def _normalize_planning_context_hints(self, raw_hints) -> dict[str, str]:
        return self.strategy_support.normalize_planning_context_hints(raw_hints)

    def _derive_review_strategy(
        self,
        *,
        query: str,
        planning_context_hints: dict[str, str],
    ) -> dict[str, Any]:
        return self.strategy_support.derive_review_strategy(
            query=query,
            planning_context_hints=planning_context_hints,
        )

    def _validate_review_strategy_requirements(
        self,
        *,
        review_strategy: dict[str, Any],
        latest_market_snapshot: dict | None,
        latest_fundamental_snapshot: dict | None,
        latest_notice_snapshot: dict | None,
        policy_signals: list[dict],
        regulatory_signals: list[dict],
    ) -> dict[str, Any]:
        return self.strategy_support.validate_review_strategy_requirements(
            review_strategy=review_strategy,
            latest_market_snapshot=latest_market_snapshot,
            latest_fundamental_snapshot=latest_fundamental_snapshot,
            latest_notice_snapshot=latest_notice_snapshot,
            policy_signals=policy_signals,
            regulatory_signals=regulatory_signals,
        )

    def _extract_review_claims(self, *, source_entries: list[dict]) -> dict:
        return self.candidate_support.extract_review_claims(source_entries=source_entries)

    def _resolve_benchmark(
        self,
        *,
        latest_sector_snapshot: dict | None,
        latest_market_snapshot: dict | None,
        default_benchmark_name: str,
        default_benchmark_symbol: str,
    ) -> dict:
        return self.strategy_support.resolve_benchmark(
            latest_sector_snapshot=latest_sector_snapshot,
            latest_market_snapshot=latest_market_snapshot,
            default_benchmark_name=default_benchmark_name,
            default_benchmark_symbol=default_benchmark_symbol,
        )

    def _review_candidate(self, **kwargs) -> dict:
        return self.verdict_support.review_candidate(**kwargs)

    def _build_review_summary(
        self,
        *,
        review_required: bool,
        findings: list[ReviewFinding],
        candidates: list[dict],
        notes: list[str],
        claim_type_counts: dict[str, int],
    ) -> dict:
        return self.summary_support.build_review_summary(
            review_required=review_required,
            findings=findings,
            candidates=candidates,
            notes=notes,
            claim_type_counts=claim_type_counts,
        )

    def _extract_candidates(self, *, source_entries: list[dict]) -> list[dict]:
        return self.candidate_support.extract_candidates(source_entries=source_entries)

    def _extract_report_fragment_candidates(self, *, entry_id: str, payload: dict) -> list[dict]:
        return self.candidate_support.extract_report_fragment_candidates(entry_id=entry_id, payload=payload)

    def _extract_scorecard_candidates(self, *, entry_id: str, payload: dict) -> list[dict]:
        return self.candidate_support.extract_scorecard_candidates(entry_id=entry_id, payload=payload)

    def _extract_logic_block_candidates(self, *, entry_id: str, payload: dict) -> list[dict]:
        return self.candidate_support.extract_logic_block_candidates(entry_id=entry_id, payload=payload)

    def _dedupe_candidates(self, candidates: list[dict]) -> list[dict]:
        return self.candidate_support.dedupe_candidates(candidates)

    def _claim_type_counts(self, candidates: list[dict]) -> dict[str, int]:
        return self.candidate_support.claim_type_counts(candidates)

    def _verdict_by_claim_type(self, findings: list[ReviewFinding]) -> dict[str, ClaimVerdictBreakdown]:
        return self.summary_support.verdict_by_claim_type(findings)

    def _verdict_totals(self, verdict_by_claim_type: dict[str, ClaimVerdictBreakdown]) -> tuple[int, int, int]:
        return self.summary_support.verdict_totals(verdict_by_claim_type)

    def _benchmark_scope_counts(self, findings: list[ReviewFinding]) -> dict[str, int]:
        return self.summary_support.benchmark_scope_counts(findings)

    def _benchmark_scope_explanations(self, benchmark_scope_counts: dict[str, int]) -> dict[str, str]:
        return self.summary_support.benchmark_scope_explanations(benchmark_scope_counts)

    def _benchmark_scope_explanation(self, scope: str) -> str:
        return self.summary_support.benchmark_scope_explanation(scope)

    def _overall_verdict(
        self,
        *,
        review_required: bool,
        finding_count: int,
        confirmed: int,
        rejected: int,
        inconclusive: int,
    ) -> str:
        return self.summary_support.overall_verdict(
            review_required=review_required,
            finding_count=finding_count,
            confirmed=confirmed,
            rejected=rejected,
            inconclusive=inconclusive,
        )

    def _summary_text(
        self,
        *,
        overall_verdict: str,
        finding_count: int,
        confirmed: int,
        rejected: int,
        inconclusive: int,
        benchmark_scope_counts: dict[str, int],
    ) -> str:
        return self.summary_support.summary_text(
            overall_verdict=overall_verdict,
            finding_count=finding_count,
            confirmed=confirmed,
            rejected=rejected,
            inconclusive=inconclusive,
            benchmark_scope_counts=benchmark_scope_counts,
        )

    def _candidate_verdict(
        self,
        *,
        candidate: dict,
        excess_metric: float | None,
        latest_market_snapshot: dict | None,
        latest_notice_snapshot: dict | None,
        policy_signals: list[dict],
        regulatory_signals: list[dict],
        theme_news: list[dict],
    ) -> str:
        return self.verdict_support.candidate_verdict(
            candidate=candidate,
            excess_metric=excess_metric,
            latest_market_snapshot=latest_market_snapshot,
            latest_notice_snapshot=latest_notice_snapshot,
            policy_signals=policy_signals,
            regulatory_signals=regulatory_signals,
            theme_news=theme_news,
        )

    def _observed_metric_for_candidate(
        self,
        *,
        candidate: dict,
        latest_hist: dict,
        latest_market_snapshot: dict | None,
        latest_notice_snapshot: dict | None,
        excess_metric: float | None,
        policy_signals: list[dict],
        regulatory_signals: list[dict],
        theme_news: list[dict],
    ) -> float | None:
        return self.verdict_support.observed_metric_for_candidate(
            candidate=candidate,
            latest_hist=latest_hist,
            latest_market_snapshot=latest_market_snapshot,
            latest_notice_snapshot=latest_notice_snapshot,
            excess_metric=excess_metric,
            policy_signals=policy_signals,
            regulatory_signals=regulatory_signals,
            theme_news=theme_news,
        )

    def _candidate_notes(
        self,
        *,
        candidate: dict,
        sector_name: str,
        latest_market_snapshot: dict | None,
        latest_fundamental_snapshot: dict | None,
        latest_notice_snapshot: dict | None,
        setup_label: str,
        catalyst_digest: dict,
        policy_signals: list[dict],
        regulatory_signals: list[dict],
        theme_news: list[dict],
    ) -> str:
        return self.verdict_support.candidate_notes(
            candidate=candidate,
            sector_name=sector_name,
            latest_market_snapshot=latest_market_snapshot,
            latest_fundamental_snapshot=latest_fundamental_snapshot,
            latest_notice_snapshot=latest_notice_snapshot,
            setup_label=setup_label,
            catalyst_digest=catalyst_digest,
            policy_signals=policy_signals,
            regulatory_signals=regulatory_signals,
            theme_news=theme_news,
        )

    def _infer_expected_direction(self, text: str) -> str | None:
        return self.candidate_support.infer_expected_direction(text)

    def _expected_direction_from_setup(self, setup_label: str) -> str | None:
        return self.candidate_support.expected_direction_from_setup(setup_label)

    def _expected_direction_from_logic_block(self, *, text: str, direction: str) -> str | None:
        return self.candidate_support.expected_direction_from_logic_block(text=text, direction=direction)

    def _contains_catalyst_claim(self, text: str) -> bool:
        return self.candidate_support.contains_catalyst_claim(text)

    def _contains_time_window_claim(self, text: str) -> bool:
        return self.candidate_support.contains_time_window_claim(text)

    def _infer_window_size(self, text: str) -> int:
        return self.candidate_support.infer_window_size(text)

    def _infer_window_size_from_horizon(self, time_horizon: str, text: str) -> int:
        return self.candidate_support.infer_window_size_from_horizon(time_horizon, text)

    def _direction_verdict(self, *, expected_direction: str, market_change: float | None, excess_metric: float | None) -> str:
        return self.verdict_support.direction_verdict(
            expected_direction=expected_direction,
            market_change=market_change,
            excess_metric=excess_metric,
        )

    def _catalyst_verdict(
        self,
        *,
        latest_notice_snapshot: dict | None,
        policy_signals: list[dict],
        regulatory_signals: list[dict],
        theme_news: list[dict],
    ) -> str:
        return self.verdict_support.catalyst_verdict(
            latest_notice_snapshot=latest_notice_snapshot,
            policy_signals=policy_signals,
            regulatory_signals=regulatory_signals,
            theme_news=theme_news,
        )

    def _time_window_verdict(
        self,
        *,
        policy_signals: list[dict],
        regulatory_signals: list[dict],
        theme_news: list[dict],
    ) -> str:
        return self.verdict_support.time_window_verdict(
            policy_signals=policy_signals,
            regulatory_signals=regulatory_signals,
            theme_news=theme_news,
        )

    def _parse_anchor_date(self, *, candidates: list[dict], snapshot_date: str) -> str:
        return self.strategy_support.parse_anchor_date(candidates=candidates, snapshot_date=snapshot_date)

    def _resolve_benchmark_scope(self, *, latest_sector_snapshot: dict | None, latest_market_snapshot: dict | None) -> str:
        return self.strategy_support.resolve_benchmark_scope(
            latest_sector_snapshot=latest_sector_snapshot,
            latest_market_snapshot=latest_market_snapshot,
        )

    def _resolve_benchmark_name(
        self,
        *,
        benchmark_scope: str,
        sector_name: str,
        default_benchmark_name: str,
    ) -> str:
        return self.strategy_support.resolve_benchmark_name(
            benchmark_scope=benchmark_scope,
            sector_name=sector_name,
            default_benchmark_name=default_benchmark_name,
        )

    def _resolve_benchmark_symbol(
        self,
        *,
        benchmark_scope: str,
        latest_market_snapshot: dict | None,
        default_benchmark_symbol: str,
    ) -> str:
        return self.strategy_support.resolve_benchmark_symbol(
            benchmark_scope=benchmark_scope,
            latest_market_snapshot=latest_market_snapshot,
            default_benchmark_symbol=default_benchmark_symbol,
        )

    def _benchmark_pct_change(self, *, benchmark: dict) -> float | None:
        return self.strategy_support.benchmark_pct_change(benchmark=benchmark)

    def _market_pct_change(self, snapshot: dict | None) -> float | None:
        return self.strategy_support.market_pct_change(snapshot)

    def _notice_signal_count(self, snapshot: dict | None) -> int | None:
        return self.strategy_support.notice_signal_count(snapshot)

    def _to_float(self, value: Any) -> float | None:
        return self.strategy_support.to_float(value)
