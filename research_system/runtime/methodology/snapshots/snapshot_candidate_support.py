from __future__ import annotations

from typing import Any


class OrchestratorMethodologySnapshotCandidateSupport:
    def __init__(self, *, fields) -> None:
        self.fields = fields

    def methodology_candidate_payload(
        self,
        *,
        symbol: str,
        name: str,
        snapshots: dict[str, dict],
        sector: Any,
    ) -> dict:
        market_snapshot = snapshots.get("market")
        fundamental_snapshot = snapshots.get("fundamental")
        banking_metrics = self.fields.banking_metrics(fundamental_snapshot)
        return {
            "symbol": symbol,
            "name": name or symbol,
            "sector": sector,
            "latest_price": self.fields.methodology_market_price(market_snapshot),
            "latest_pct_change": self.fields.methodology_market_pct_change(market_snapshot),
            "latest_amount": self.fields.methodology_market_amount(market_snapshot),
            "pb": self.fields.methodology_market_pb(market_snapshot),
            "pe_ttm": self.fields.methodology_market_pe_ttm(market_snapshot),
            "market_cap": self.fields.methodology_market_cap(market_snapshot),
            "float_market_cap": self.fields.methodology_market_float_market_cap(market_snapshot),
            "market_total_shares": self.fields.methodology_market_total_shares(market_snapshot),
            "market_float_shares": self.fields.methodology_market_float_shares(market_snapshot),
            "revenue_proxy": self.fields.methodology_revenue_proxy(fundamental_snapshot),
            "banking_metrics": banking_metrics,
            "bank_like": bool(banking_metrics.get("is_bank_like")),
            "score": 0.0,
        }
