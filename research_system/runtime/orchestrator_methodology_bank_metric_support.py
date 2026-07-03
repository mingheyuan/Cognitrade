from __future__ import annotations

from typing import Any

from research_system.adapters.vendors.akshare_utils import coerce_numeric


class OrchestratorMethodologyBankMetricSupport:
    METRIC_WEIGHTS = {
        "pb": 0.20,
        "peer_pb_mrq": 0.08,
        "peer_pb_rank": 0.05,
        "roe": 0.16,
        "peer_roe_3y_avg": 0.14,
        "peer_roe_rank": 0.05,
        "asset_liability_ratio": 0.08,
        "capital_adequacy_ratio": 0.05,
        "core_tier1_capital_adequacy_ratio": 0.05,
        "net_interest_spread": 0.05,
        "nonperforming_loan_ratio": 0.07,
        "provision_coverage_ratio": 0.06,
        "dividend_yield_proxy": 0.08,
        "dividend_payout_ratio": 0.03,
        "revenue_yoy": 0.04,
        "peer_revenue_growth_ttm": 0.06,
        "peer_revenue_growth_ttm_vs_industry_avg": 0.03,
        "net_profit_yoy": 0.06,
        "peer_net_profit_growth_ttm": 0.08,
        "peer_net_profit_growth_ttm_vs_industry_avg": 0.05,
        "interest_margin_proxy": 0.04,
        "peer_net_margin_3y_avg": 0.03,
        "net_interest_spread_proxy": 0.03,
        "deposit_growth_proxy": 0.02,
        "loan_to_deposit_proxy": 0.02,
        "provision_coverage_proxy": 0.03,
    }

    METRIC_DIRECTION = {
        "pb": "lower",
        "peer_pb_mrq": "lower",
        "peer_pb_rank": "lower",
        "roe": "higher",
        "peer_roe_3y_avg": "higher",
        "peer_roe_rank": "lower",
        "asset_liability_ratio": "lower",
        "capital_adequacy_ratio": "higher",
        "core_tier1_capital_adequacy_ratio": "higher",
        "net_interest_spread": "higher",
        "nonperforming_loan_ratio": "lower",
        "provision_coverage_ratio": "higher",
        "dividend_yield_proxy": "higher",
        "dividend_payout_ratio": "higher",
        "revenue_yoy": "higher",
        "peer_revenue_growth_ttm": "higher",
        "peer_revenue_growth_ttm_vs_industry_avg": "higher",
        "net_profit_yoy": "higher",
        "peer_net_profit_growth_ttm": "higher",
        "peer_net_profit_growth_ttm_vs_industry_avg": "higher",
        "interest_margin_proxy": "higher",
        "peer_net_margin_3y_avg": "higher",
        "net_interest_spread_proxy": "higher",
        "deposit_growth_proxy": "higher",
        "loan_to_deposit_proxy": "neutral",
        "provision_coverage_proxy": "higher",
    }

    def build_metric_series(self, peer_candidates: list[dict]) -> dict[str, list[float]]:
        metric_series: dict[str, list[float]] = {key: [] for key in self.METRIC_WEIGHTS}
        for candidate in peer_candidates:
            metrics = self.bank_compare_metrics(candidate)
            for key in self.METRIC_WEIGHTS:
                value = coerce_numeric(metrics.get(key))
                if value is not None:
                    metric_series[key].append(value)
        return metric_series

    def bank_compare_metrics(self, candidate: dict) -> dict[str, Any]:
        metrics = dict(candidate.get("banking_metrics") or {})
        metrics["pb"] = coerce_numeric(candidate.get("pb"))
        metrics["pe_ttm"] = coerce_numeric(candidate.get("pe_ttm"))
        metrics["market_cap"] = coerce_numeric(candidate.get("market_cap"))
        metrics["float_market_cap"] = coerce_numeric(candidate.get("float_market_cap"))
        metrics["dividend_yield_proxy"] = self.bank_dividend_yield_proxy(candidate)
        return metrics

    def bank_dividend_yield_proxy(self, candidate: dict) -> float | None:
        metrics = dict(candidate.get("banking_metrics") or {})
        dividend_cash = coerce_numeric(metrics.get("pay_dividends_profits_interest_cash"))
        latest_price = coerce_numeric(candidate.get("latest_price"))
        total_shares = coerce_numeric(candidate.get("market_total_shares"))
        if dividend_cash in (None, 0) or latest_price in (None, 0) or total_shares in (None, 0):
            return None
        dividend_per_share = dividend_cash / total_shares
        return dividend_per_share / latest_price

    def bank_metric_percentile(self, *, value: float, series: list[float], preference: str) -> float | None:
        if not series:
            return None
        ordered = sorted(series)
        if preference == "neutral":
            median = ordered[len(ordered) // 2]
            max_distance = max(abs(item - median) for item in ordered) if ordered else 0.0
            if max_distance == 0:
                return 1.0
            return max(0.0, 1.0 - abs(value - median) / max_distance)
        below = sum(1 for item in ordered if item <= value)
        percentile = below / len(ordered)
        if preference == "lower":
            percentile = 1.0 - percentile
        return round(percentile, 4)
