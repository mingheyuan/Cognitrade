from __future__ import annotations

from research_system.adapters.vendors.akshare_utils import coerce_numeric
from research_system.runtime.methodology.banking.bank_highlight_support import (
    OrchestratorMethodologyBankHighlightSupport,
)
from research_system.runtime.methodology.banking.bank_metric_support import (
    OrchestratorMethodologyBankMetricSupport,
)
from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import BlackboardEntry


class OrchestratorMethodologyBankScorecardSupport:
    def __init__(self) -> None:
        self.metric_support = OrchestratorMethodologyBankMetricSupport()
        self.highlight_support = OrchestratorMethodologyBankHighlightSupport()

    def build_bank_compare_scorecard_entry(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        phase: str,
        agent: str,
        compare_targets: list[dict],
        peer_candidates: list[dict],
    ) -> BlackboardEntry | None:
        if len(peer_candidates) < 2:
            return None
        bank_candidates = [item for item in peer_candidates if bool((item.get("banking_metrics") or {}).get("is_bank_like"))]
        if len(bank_candidates) < 2:
            return None
        scorecard = self.bank_compare_scorecard(compare_targets=compare_targets, peer_candidates=bank_candidates)
        return BlackboardEntry(
            run_id=context.run_id,
            request_id=request.request_id,
            kind="source_record",
            agent=agent,
            phase=phase,
            payload={
                "title": "bank_compare_scorecard",
                "content": str(scorecard),
                "source_type": "bank_compare_scorecard",
                "scorecard": scorecard,
            },
            source_refs=[{"type": "stock_compare_scorecard", "run_id": context.run_id}],
            confidence=1.0,
            risk_flags=list(scorecard.get("missing_metrics") or []),
        )

    def bank_compare_scorecard(self, *, compare_targets: list[dict], peer_candidates: list[dict]) -> dict:
        target_names = {
            str(item.get("symbol") or "").strip(): str(item.get("name") or item.get("symbol") or "").strip()
            for item in compare_targets
            if str(item.get("symbol") or "").strip()
        }
        metric_weights = self.metric_support.METRIC_WEIGHTS
        direction = self.metric_support.METRIC_DIRECTION
        metric_series = self.metric_support.build_metric_series(peer_candidates)

        ranking_rows: list[dict] = []
        metric_presence_counts: dict[str, int] = {key: 0 for key in metric_weights}
        for candidate in peer_candidates:
            symbol = str(candidate.get("symbol") or "").strip()
            metrics = self.metric_support.bank_compare_metrics(candidate)
            score = 0.0
            weight_used = 0.0
            metric_breakdown: dict[str, float | None] = {}
            for key, weight in metric_weights.items():
                value = coerce_numeric(metrics.get(key))
                metric_breakdown[key] = value
                if value is not None:
                    metric_presence_counts[key] += 1
                if value is None or len(metric_series[key]) < 2:
                    continue
                percentile = self.metric_support.bank_metric_percentile(
                    value=value,
                    series=metric_series[key],
                    preference=direction[key],
                )
                if percentile is None:
                    continue
                score += percentile * weight
                weight_used += weight
            normalized_score = round((score / weight_used), 4) if weight_used > 0 else 0.0
            ranking_rows.append(
                {
                    "symbol": symbol,
                    "name": target_names.get(symbol) or str(candidate.get("name") or symbol),
                    "score": normalized_score,
                    "weight_coverage": round(weight_used, 4),
                    "latest_price": candidate.get("latest_price"),
                    "latest_pct_change": candidate.get("latest_pct_change"),
                    "latest_amount": candidate.get("latest_amount"),
                    "pb": candidate.get("pb"),
                    "pe_ttm": candidate.get("pe_ttm"),
                    "market_cap": candidate.get("market_cap"),
                    "float_market_cap": candidate.get("float_market_cap"),
                    "market_total_shares": candidate.get("market_total_shares"),
                    "market_float_shares": candidate.get("market_float_shares"),
                    "banking_metrics": metric_breakdown,
                    "highlights": self.highlight_support.build_candidate_highlights(metric_breakdown),
                    "missing_metrics": sorted(key for key, value in metric_breakdown.items() if value is None),
                }
            )
        ranking_rows.sort(key=lambda item: (item.get("score") or 0.0, item.get("weight_coverage") or 0.0), reverse=True)
        for index, row in enumerate(ranking_rows, start=1):
            row["rank"] = index
        top_names = [str(item.get("name") or item.get("symbol") or "") for item in ranking_rows[:3]]
        candidate_count = len(ranking_rows)
        coverage_threshold = max(candidate_count - 1, 1)
        missing_metrics = sorted(
            key for key, count in metric_presence_counts.items()
            if count < coverage_threshold
        )
        summary = (
            f"bank peer comparison ranked {len(ranking_rows)} banks using same-basis banking metrics and valuation anchors; "
            f"top_tier={top_names}"
        )
        return {
            "scorecard_type": "bank_compare_scorecard",
            "candidate_count": len(ranking_rows),
            "ranked_candidates": ranking_rows,
            "metric_weights": metric_weights,
            "missing_metrics": sorted(missing_metrics),
            "summary": summary,
        }
