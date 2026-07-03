from __future__ import annotations

from research_system.adapters.vendors.akshare_utils import coerce_numeric


class OrchestratorMethodologySnapshotFields:
    _MARKET_PRICE_KEYS = ("latest_price", "close")
    _MARKET_CLOSE_KEYS = ("close",)
    _MARKET_PCT_CHANGE_KEYS = ("pct_change", "change_percent")
    _MARKET_AMOUNT_KEYS = ("amount", "turnover", "成交额")
    _MARKET_PB_KEYS = ("pb", "pb_mrq")
    _MARKET_PE_KEYS = ("pe_ttm", "pe")
    _MARKET_CAP_KEYS = ("market_cap", "total_market_cap")
    _MARKET_FLOAT_CAP_KEYS = ("float_market_cap", "circulating_market_cap")
    _MARKET_TOTAL_SHARES_KEYS = ("total_shares",)
    _MARKET_FLOAT_SHARES_KEYS = ("float_shares", "circulating_shares")
    _REVENUE_KEYS = ("revenue", "营业总收入", "营业收入", "营收")
    _REVENUE_TOKENS = ("revenue", "收入", "营收")

    def methodology_market_price(self, market_snapshot: dict | None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        spot = overview.get("spot") or overview
        value = self._first_numeric(spot, *self._MARKET_PRICE_KEYS)
        if value is not None:
            return value
        latest_bar = (facts.get("market_hist") or {}).get("latest_bar") or facts.get("latest_bar") or {}
        return self._first_numeric(latest_bar, *self._MARKET_CLOSE_KEYS)

    def methodology_market_pct_change(self, market_snapshot: dict | None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        spot = overview.get("spot") or overview
        value = self._first_numeric(spot, *self._MARKET_PCT_CHANGE_KEYS)
        if value is not None:
            return value
        latest_bar = (facts.get("market_hist") or {}).get("latest_bar") or facts.get("latest_bar") or {}
        return self._first_numeric(latest_bar, *self._MARKET_PCT_CHANGE_KEYS)

    def methodology_market_amount(self, market_snapshot: dict | None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        spot = overview.get("spot") or overview
        return self._first_numeric(spot, *self._MARKET_AMOUNT_KEYS)

    def methodology_market_pb(self, market_snapshot: dict | None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        value = self._first_numeric(overview, *self._MARKET_PB_KEYS)
        if value is not None:
            return value
        valuation = overview.get("valuation") or {}
        return self._first_numeric(valuation, *self._MARKET_PB_KEYS)

    def methodology_market_pe_ttm(self, market_snapshot: dict | None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        value = self._first_numeric(overview, *self._MARKET_PE_KEYS)
        if value is not None:
            return value
        valuation = overview.get("valuation") or {}
        return self._first_numeric(valuation, *self._MARKET_PE_KEYS)

    def methodology_market_cap(self, market_snapshot: dict | None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        value = self._first_numeric(overview, *self._MARKET_CAP_KEYS)
        if value is not None:
            return value
        valuation = overview.get("valuation") or {}
        return self._first_numeric(valuation, *self._MARKET_CAP_KEYS)

    def methodology_market_float_market_cap(self, market_snapshot: dict | None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        value = self._first_numeric(overview, *self._MARKET_FLOAT_CAP_KEYS)
        if value is not None:
            return value
        valuation = overview.get("valuation") or {}
        return self._first_numeric(valuation, *self._MARKET_FLOAT_CAP_KEYS)

    def methodology_market_total_shares(self, market_snapshot: dict | None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        value = self._first_numeric(overview, *self._MARKET_TOTAL_SHARES_KEYS)
        if value is not None:
            return value
        valuation = overview.get("valuation") or {}
        return self._first_numeric(valuation, *self._MARKET_TOTAL_SHARES_KEYS)

    def methodology_market_float_shares(self, market_snapshot: dict | None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        value = self._first_numeric(overview, *self._MARKET_FLOAT_SHARES_KEYS)
        if value is not None:
            return value
        valuation = overview.get("valuation") or {}
        return self._first_numeric(valuation, *self._MARKET_FLOAT_SHARES_KEYS)

    def methodology_revenue_proxy(self, fundamental_snapshot: dict | None) -> float | None:
        facts = (fundamental_snapshot or {}).get("facts") or {}
        banking_metrics = facts.get("banking_metrics") or {}
        for key in ("interest_income", "deposits_and_deposits", "customer_loan_advance_net_addition"):
            value = coerce_numeric(banking_metrics.get(key))
            if value is not None:
                return value
        abstract = facts.get("financial_abstract") or {}
        value = self._first_numeric(abstract, *self._REVENUE_KEYS)
        if value is not None:
            return value
        for item_key, item_value in abstract.items():
            key_text = str(item_key)
            if any(token in key_text for token in self._REVENUE_TOKENS):
                value = coerce_numeric(item_value)
                if value is not None:
                    return value
        return None

    def banking_metrics(self, fundamental_snapshot: dict | None) -> dict:
        facts = (fundamental_snapshot or {}).get("facts") or {}
        metrics = facts.get("banking_metrics") or {}
        return dict(metrics) if isinstance(metrics, dict) else {}

    def _first_numeric(self, payload: dict | None, *keys: str) -> float | None:
        mapping = payload if isinstance(payload, dict) else {}
        for key in keys:
            value = coerce_numeric(mapping.get(key))
            if value is not None:
                return value
        return None
