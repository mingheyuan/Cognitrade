from __future__ import annotations

from datetime import date, datetime
from importlib import import_module
from typing import Any

from research_system.adapters.vendors.akshare_utils import (
    coerce_numeric,
    normalize_value,
)


class EFinanceMarketAdapter:
    """Zero-key A/H/US market fallback backed by eFinance."""

    vendor_name = "efinance"

    def __init__(self) -> None:
        self._quote_cache: dict[str, dict[str, Any]] = {}
        self._base_cache: dict[str, dict[str, Any]] = {}

    def get_market_snapshot(
        self,
        *,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "",
    ) -> dict[str, Any]:
        if (
            str(symbol).upper().endswith(".HK")
            or str(symbol).upper().startswith("HK")
            or any(
                char.isalpha()
                for char in str(symbol)
                .upper()
                .replace("SH", "")
                .replace("SZ", "")
                .replace("BJ", "")
            )
        ):
            raise ValueError("efinance market adapter only supports A-share symbols")
        ef = import_module("efinance")
        provider_symbol = self._provider_symbol(symbol)
        history_mode = "quote_history"
        try:
            history = ef.stock.get_quote_history(
                provider_symbol,
                beg=start_date,
                end=end_date,
                klt=101,
                fqt={"qfq": 1, "hfq": 2}.get(adjust, 0),
            )
            if history is None or getattr(history, "empty", True):
                raise ValueError(f"no market data returned for symbol {symbol}")
            bars = [self._normalize_bar(row) for row in history.to_dict("records")]
        except Exception:
            if end_date != date.today().strftime("%Y%m%d"):
                raise
            # eFinance history and quote snapshots use different EastMoney
            # endpoints.  push2his can be blocked while the current quote is
            # healthy; retain the independent current-market observation and
            # let the dedicated kline dimension own long history completeness.
            quote = self._quote_record(ef, provider_symbol)
            latest = coerce_numeric(quote.get("最新价"))
            if latest is None:
                raise ValueError(
                    f"no efinance quote fallback returned for symbol {symbol}"
                )
            bars = [
                {
                    "date": str(quote.get("日期") or date.today().isoformat()),
                    "name": quote.get("名称"),
                    "symbol": provider_symbol,
                    "open": coerce_numeric(quote.get("今开")),
                    "close": latest,
                    "high": coerce_numeric(quote.get("最高")),
                    "low": coerce_numeric(quote.get("最低")),
                    "pre_close": coerce_numeric(quote.get("昨收")),
                    "volume": coerce_numeric(quote.get("成交量")),
                    "amount": coerce_numeric(quote.get("成交额")),
                    "pct_change": coerce_numeric(quote.get("涨跌幅")),
                }
            ]
            history_mode = "quote_snapshot_fallback"
        return {
            "symbol": symbol,
            "normalized_symbol": provider_symbol,
            "start_date": start_date,
            "end_date": end_date,
            "adjust": adjust,
            "bars": bars,
            "latest_bar": dict(bars[-1]),
            "bar_count": len(bars),
            "history_mode": history_mode,
        }

    def get_market_overview(
        self,
        *,
        symbol: str,
        latest_bar: dict[str, Any] | None = None,
        as_of_date: date | None = None,
    ) -> dict[str, Any]:
        bar = dict(latest_bar or {})
        ef = import_module("efinance")
        provider_symbol = self._provider_symbol(symbol)
        quote: dict[str, Any] = {}
        base: dict[str, Any] = {}
        if as_of_date is None:
            try:
                quote = self._quote_record(ef, provider_symbol)
            except Exception:
                quote = {}
            try:
                base = self._base_record(ef, provider_symbol)
            except Exception:
                base = {}

        latest_price = coerce_numeric(quote.get("最新价"))
        if latest_price is None:
            latest_price = coerce_numeric(bar.get("close"))
        pct_change = coerce_numeric(quote.get("涨跌幅"))
        if pct_change is None:
            pct_change = coerce_numeric(bar.get("pct_change"))
        market_cap = coerce_numeric(base.get("总市值"))
        float_market_cap = coerce_numeric(base.get("流通市值"))
        pb = coerce_numeric(base.get("市净率"))
        pe_dynamic = coerce_numeric(base.get("市盈率(动)"))
        return {
            "symbol": symbol,
            "normalized_symbol": provider_symbol,
            "security_name": base.get("股票名称")
            or quote.get("名称")
            or bar.get("name"),
            "industry": base.get("所处行业"),
            "latest_price": latest_price,
            "pct_change": pct_change,
            "amount": coerce_numeric(quote.get("成交额"))
            or coerce_numeric(bar.get("amount")),
            "pb": pb,
            # eFinance exposes dynamic PE here, not TTM PE.  Keep the basis
            # explicit so downstream recovery never silently relabels it.
            "pe_dynamic": pe_dynamic,
            "market_cap": market_cap,
            "float_market_cap": float_market_cap,
            "spot": quote or bar,
            "valuation": base,
            "spot_found": bool(quote or bar),
            "valuation_found": any(
                value is not None
                for value in (market_cap, float_market_cap, pb, pe_dynamic)
            ),
        }

    def _quote_record(self, ef: Any, provider_symbol: str) -> dict[str, Any]:
        cached = self._quote_cache.get(provider_symbol)
        if cached is not None:
            return dict(cached)
        record = self._as_record(ef.stock.get_quote_snapshot(provider_symbol))
        if record:
            self._quote_cache[provider_symbol] = record
        return dict(record)

    def _base_record(self, ef: Any, provider_symbol: str) -> dict[str, Any]:
        cached = self._base_cache.get(provider_symbol)
        if cached is not None:
            return dict(cached)
        record = self._as_record(ef.stock.get_base_info(provider_symbol))
        if record:
            self._base_cache[provider_symbol] = record
        return dict(record)

    @staticmethod
    def _as_record(value: Any) -> dict[str, Any]:
        if value is None or getattr(value, "empty", True):
            return {}
        # eFinance normally returns Series here; tolerate a one-row DataFrame
        # because releases have changed this shape before.
        if hasattr(value, "iloc") and not hasattr(value, "name"):
            value = value.iloc[0]
        payload = value.to_dict()
        if payload and all(isinstance(item, dict) for item in payload.values()):
            payload = {
                key: next(iter(item.values()), None) for key, item in payload.items()
            }
        return {str(key): normalize_value(item) for key, item in payload.items()}

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
