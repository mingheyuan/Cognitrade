from __future__ import annotations

from typing import Callable

from research_system.schemas.review import PosteriorReviewSummary, ReviewFinding


class ReviewClaimExtractorSubagent:
    subagent_type = "review_claim_extractor"

    def __init__(self, *, extract_candidates: Callable[[list[dict]], list[dict]]) -> None:
        self.extract_candidates = extract_candidates

    def run(self, *, source_entries: list[dict]) -> dict:
        candidates = self.extract_candidates(source_entries=source_entries)
        return {
            "candidates": candidates,
            "claim_type_counts": self._claim_type_counts(candidates),
        }

    def _claim_type_counts(self, candidates: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for candidate in candidates:
            claim_type = str(candidate.get("claim_type") or "unknown")
            counts[claim_type] = counts.get(claim_type, 0) + 1
        return counts


class BenchmarkResolverSubagent:
    subagent_type = "benchmark_resolver"

    def __init__(
        self,
        *,
        resolve_scope: Callable[..., str],
        resolve_name: Callable[..., str],
        resolve_symbol: Callable[..., str],
        benchmark_pct_change: Callable[..., float | None],
    ) -> None:
        self.resolve_scope = resolve_scope
        self.resolve_name = resolve_name
        self.resolve_symbol = resolve_symbol
        self.benchmark_pct_change = benchmark_pct_change

    def run(
        self,
        *,
        latest_sector_snapshot: dict | None,
        latest_market_snapshot: dict | None,
        default_benchmark_name: str,
        default_benchmark_symbol: str,
    ) -> dict:
        facts = (latest_sector_snapshot or {}).get("facts") or {}
        benchmark = facts.get("benchmark") or {}
        scope = self.resolve_scope(
            latest_sector_snapshot=latest_sector_snapshot,
            latest_market_snapshot=latest_market_snapshot,
        )
        sector_name = str((latest_sector_snapshot or {}).get("sector") or "")
        benchmark_name = self.resolve_name(
            benchmark_scope=scope,
            sector_name=sector_name,
            default_benchmark_name=default_benchmark_name,
        )
        benchmark_symbol = self.resolve_symbol(
            benchmark_scope=scope,
            latest_market_snapshot=latest_market_snapshot,
            default_benchmark_symbol=default_benchmark_symbol,
        )
        return {
            "benchmark_scope": scope,
            "benchmark_name": benchmark_name,
            "benchmark_symbol": benchmark_symbol,
            "observed_benchmark_metric": self.benchmark_pct_change(benchmark=benchmark),
        }


class DirectionReviewerSubagent:
    subagent_type = "direction_reviewer"

    def __init__(
        self,
        *,
        observed_metric_for_candidate: Callable[..., float | None],
        candidate_verdict: Callable[..., str],
        candidate_notes: Callable[..., str],
    ) -> None:
        self.observed_metric_for_candidate = observed_metric_for_candidate
        self.candidate_verdict = candidate_verdict
        self.candidate_notes = candidate_notes

    def run(self, **kwargs) -> dict:
        candidate = kwargs["candidate"]
        finding = ReviewFinding(
            claim_id=candidate["claim_id"],
            claim_type=str(candidate.get("claim_type") or "direction"),
            source_run_id=kwargs["source_run_id"],
            target_entry_id=candidate["entry_id"],
            window_type=str(candidate.get("window_type") or "trading_days"),
            window_size=int(candidate.get("window_size") or 5),
            anchor_date=kwargs["anchor_date"],
            evaluation_start_rule=str(candidate.get("evaluation_start_rule") or "next_trading_day_open"),
            early_invalidation_rule=candidate.get("early_invalidation_rule"),
            benchmark_scope=kwargs["benchmark_scope"],
            benchmark_symbol=kwargs["benchmark_symbol"],
            benchmark_name=kwargs["benchmark_name"],
            expected_direction=candidate.get("expected_direction"),
            observed_metric=self.observed_metric_for_candidate(
                candidate=candidate,
                latest_hist=kwargs["latest_hist"],
                latest_market_snapshot=kwargs["latest_market_snapshot"],
                latest_notice_snapshot=kwargs["latest_notice_snapshot"],
                excess_metric=kwargs["excess_metric"],
                policy_signals=kwargs["policy_signals"],
                regulatory_signals=kwargs["regulatory_signals"],
                theme_news=kwargs["theme_news"],
            ),
            observed_benchmark_metric=kwargs["observed_benchmark_metric"],
            excess_metric=kwargs["excess_metric"],
            verdict=self.candidate_verdict(
                candidate=candidate,
                latest_market_snapshot=kwargs["latest_market_snapshot"],
                latest_notice_snapshot=kwargs["latest_notice_snapshot"],
                excess_metric=kwargs["excess_metric"],
                policy_signals=kwargs["policy_signals"],
                regulatory_signals=kwargs["regulatory_signals"],
                theme_news=kwargs["theme_news"],
            ),
            notes=self.candidate_notes(
                candidate=candidate,
                sector_name=kwargs["sector_name"],
                latest_market_snapshot=kwargs["latest_market_snapshot"],
                latest_fundamental_snapshot=kwargs["latest_fundamental_snapshot"],
                latest_notice_snapshot=kwargs["latest_notice_snapshot"],
                setup_label=kwargs["setup_label"],
                catalyst_digest=kwargs["catalyst_digest"],
                policy_signals=kwargs["policy_signals"],
                regulatory_signals=kwargs["regulatory_signals"],
                theme_news=kwargs["theme_news"],
            ),
        )
        return {"finding": finding.model_dump(mode="json")}


class CatalystReviewerSubagent(DirectionReviewerSubagent):
    subagent_type = "catalyst_reviewer"

    def run(self, **kwargs) -> dict:
        candidate = dict(kwargs["candidate"])
        candidate["claim_type"] = "catalyst"
        kwargs = dict(kwargs)
        kwargs["candidate"] = candidate
        return super().run(**kwargs)


class TimeWindowReviewerSubagent(DirectionReviewerSubagent):
    subagent_type = "time_window_reviewer"

    def run(self, **kwargs) -> dict:
        candidate = dict(kwargs["candidate"])
        candidate["claim_type"] = "time_window"
        kwargs = dict(kwargs)
        kwargs["candidate"] = candidate
        return super().run(**kwargs)


class ReviewSummaryBuilderSubagent:
    subagent_type = "review_summary_builder"

    def __init__(
        self,
        *,
        verdict_by_claim_type: Callable[[list[ReviewFinding]], dict],
        verdict_totals: Callable[[dict], tuple[int, int, int]],
        overall_verdict: Callable[..., str],
        summary_text: Callable[..., str],
        benchmark_scope_counts: Callable[[list[ReviewFinding]], dict[str, int]],
        benchmark_scope_explanations: Callable[[dict[str, int]], dict[str, str]],
    ) -> None:
        self.verdict_by_claim_type = verdict_by_claim_type
        self.verdict_totals = verdict_totals
        self.overall_verdict = overall_verdict
        self.summary_text = summary_text
        self.benchmark_scope_counts = benchmark_scope_counts
        self.benchmark_scope_explanations = benchmark_scope_explanations

    def run(
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
