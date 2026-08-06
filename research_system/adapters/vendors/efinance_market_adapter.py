from __future__ import annotations

from datetime import datetime
from importlib import import_module
from typing import Any

from research_system.adapters.vendors.akshare_utils import coerce_numeric, normalize_value


class EFinanceMarketAdapter:
    """Zero-key A/H/US market fallback backed by eFinance."""

    vendor_name = "efinance"

    def get_market_snapshot(
        self,
        *,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "",
    ) -> dict[str, Any]:
        if str(symbol).upper().endswith(".HK") or str(symbol).upper().startswith("HK") or any(
            char.isalpha() for char in str(symbol).upper().replace("SH", "").replace("SZ", "").replace("BJ", "")
        ):
            raise ValueError("efinance market adapter only supports A-share symbols")
        ef = import_module("efinance")
        history = ef.stock.get_quote_history(
            self._provider_symbol(symbol),
            beg=start_date,
            end=end_date,
            klt=101,
            fqt={"qfq": 1, "hfq": 2}.get(adjust, 0),
        )
        if history is None or getattr(history, "empty", True):
            raise ValueError(f"no market data returned for symbol {symbol}")
        bars = [self._normalize_bar(row) for row in history.to_dict("records")]
        return {
            "symbol": symbol,
            "normalized_symbol": self._provider_symbol(symbol),
            "start_date": start_date,
            "end_date": end_date,
            "adjust": adjust,
            "bars": bars,
            "latest_bar": dict(bars[-1]),
            "bar_count": len(bars),
        }

    def get_market_overview(self, *, symbol: str, latest_bar: dict[str, Any] | None = None) -> dict[str, Any]:
        bar = dict(latest_bar or {})
        latest_price = coerce_numeric(bar.get("close"))
        pct_change = coerce_numeric(bar.get("pct_change"))
        return {
            "symbol": symbol,
            "normalized_symbol": self._provider_symbol(symbol),
            "latest_price": latest_price,
            "pct_change": pct_change,
            "amount": coerce_numeric(bar.get("amount")),
            "spot": bar,
            "valuation": {},
            "spot_found": bool(bar),
            "valuation_found": False,
        }

    @staticmethod
    def _provider_symbol(symbol: str) -> str:
        raw = str(symbol or "").strip().upper()
        return raw.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")

    @staticmethod
    def _normalize_bar(row: dict[str, Any]) -> dict[str, Any]:
        aliases = {
            "日期": "date",
            "股票名称": "name",
            "股票代码": "symbol",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "涨跌幅": "pct_change",
            "换手率": "turnover",
        }
        normalized: dict[str, Any] = {}
        for key, value in row.items():
            target = aliases.get(str(key), str(key))
            if target == "date" and isinstance(value, (datetime,)):
                normalized[target] = value.date().isoformat()
            else:
                normalized[target] = normalize_value(value)
        return normalized
