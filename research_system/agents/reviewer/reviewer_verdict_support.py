from __future__ import annotations

from research_system.agents.reviewer.reviewer_strategy_support import ReviewerStrategySupport
from research_system.schemas.review import ReviewFinding


class ReviewerVerdictSupport:
    def __init__(self, *, strategy_support: ReviewerStrategySupport) -> None:
        self.strategy_support = strategy_support

    def review_candidate(self, **kwargs) -> dict:
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

    def candidate_verdict(
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
        claim_type = str(candidate.get("claim_type") or "direction")
        if claim_type == "direction":
            return self.direction_verdict(
                expected_direction=str(candidate.get("expected_direction") or ""),
                market_change=self.strategy_support.market_pct_change(latest_market_snapshot),
                excess_metric=excess_metric,
            )
        if claim_type == "catalyst":
            return self.catalyst_verdict(
                latest_notice_snapshot=latest_notice_snapshot,
                policy_signals=policy_signals,
                regulatory_signals=regulatory_signals,
                theme_news=theme_news,
            )
        if claim_type == "time_window":
            return self.time_window_verdict(
                policy_signals=policy_signals,
                regulatory_signals=regulatory_signals,
                theme_news=theme_news,
            )
        return "inconclusive"

    def observed_metric_for_candidate(
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
        claim_type = str(candidate.get("claim_type") or "direction")
        if claim_type == "direction":
            market_pct_change = self.strategy_support.market_pct_change(latest_market_snapshot)
            if market_pct_change is not None:
                return market_pct_change
            return self.strategy_support.to_float(latest_hist.get("pct_change"))
        if claim_type == "catalyst":
            notice_count = self.strategy_support.notice_signal_count(latest_notice_snapshot)
            if notice_count is not None:
                return float(notice_count)
            return float(len(policy_signals) + len(regulatory_signals))
        if claim_type == "time_window":
            if excess_metric is not None:
                return excess_metric
            return float(len(theme_news))
        return None

    def candidate_notes(
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
        claim_type = str(candidate.get("claim_type") or "direction")
        claim_source = str(candidate.get("claim_source") or "unknown")
        parts = [
            f"sector={sector_name or 'unknown'}",
            f"setup_label={setup_label or 'unknown'}",
            f"review_source={claim_source}",
            f"claim_type={claim_type}",
        ]
        market_symbol = str((latest_market_snapshot or {}).get("symbol") or "")
        if market_symbol:
            parts.append(f"market_symbol={market_symbol}")
        claim_text = str(candidate.get("claim_text") or "")
        if claim_text:
            parts.append(f"claim_excerpt={claim_text[:80]}")
        if claim_type == "catalyst":
            top_policy = (catalyst_digest.get("top_policy_titles") or [None])[0]
            top_regulatory = (catalyst_digest.get("top_regulatory_titles") or [None])[0]
            parts.append(f"policy_signal_count={len(policy_signals)}")
            parts.append(f"regulatory_signal_count={len(regulatory_signals)}")
            notice_count = self.strategy_support.notice_signal_count(latest_notice_snapshot)
            if notice_count is not None:
                parts.append(f"notice_signal_count={notice_count}")
            if top_policy:
                parts.append(f"top_policy_title={top_policy}")
            if top_regulatory:
                parts.append(f"top_regulatory_title={top_regulatory}")
        elif claim_type == "time_window":
            parts.append(f"window_size={candidate.get('window_size')}")
            parts.append(f"theme_news_count={len(theme_news)}")
            parts.append(f"policy_signal_count={len(policy_signals)}")
        if latest_fundamental_snapshot is not None:
            parts.append("fundamental_snapshot_present=true")
        return "; ".join(parts)

    def direction_verdict(
        self,
        *,
        expected_direction: str,
        market_change: float | None,
        excess_metric: float | None,
    ) -> str:
        if excess_metric is None:
            if market_change is None:
                return "inconclusive"
            if expected_direction == "outperform":
                return "confirmed" if market_change > 0 else "rejected"
            if expected_direction == "underperform":
                return "confirmed" if market_change < 0 else "rejected"
            return "inconclusive"
        if expected_direction == "outperform":
            return "confirmed" if excess_metric > 0 else "rejected"
        if expected_direction == "underperform":
            return "confirmed" if excess_metric < 0 else "rejected"
        return "inconclusive"

    def catalyst_verdict(
        self,
        *,
        latest_notice_snapshot: dict | None,
        policy_signals: list[dict],
        regulatory_signals: list[dict],
        theme_news: list[dict],
    ) -> str:
        notice_count = self.strategy_support.notice_signal_count(latest_notice_snapshot)
        if notice_count is not None and notice_count > 0:
            return "confirmed"
        if policy_signals or regulatory_signals:
            return "confirmed"
        if theme_news:
            return "inconclusive"
        return "rejected"

    def time_window_verdict(
        self,
        *,
        policy_signals: list[dict],
        regulatory_signals: list[dict],
        theme_news: list[dict],
    ) -> str:
        if policy_signals or regulatory_signals:
            return "confirmed"
        if theme_news:
            return "inconclusive"
        return "rejected"
