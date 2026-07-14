from __future__ import annotations

from typing import Any
from datetime import UTC, datetime, timedelta

import akshare as ak

from research_system.adapters.vendors.akshare_utils import (
    call_with_retry,
    coerce_numeric,
    normalize_ashare_symbol,
    normalize_value,
)


class AkShareMarketAdapter:
    vendor_name = "akshare"
    _spot_cache_df = None
    _spot_cache_at: datetime | None = None
    # Keep the spot table warm for the duration of a typical multi-stock run.
    _spot_cache_ttl = timedelta(minutes=5)

    def get_market_snapshot(self, *, symbol: str, start_date: str, end_date: str, adjust: str = "") -> dict[str, Any]:
        normalized_symbol = normalize_ashare_symbol(symbol)
        hist_df = call_with_retry(
            lambda: ak.stock_zh_a_daily(
                symbol=normalized_symbol,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
        )
        if hist_df.empty:
            raise ValueError(f"no market data returned for symbol {symbol}")
        bars = [
            {str(key): normalize_value(value) for key, value in row.items()}
            for row in hist_df.to_dict("records")
        ]
        return {
            "symbol": symbol,
            "normalized_symbol": normalized_symbol,
            "start_date": start_date,
            "end_date": end_date,
            "adjust": adjust,
            "bars": bars,
            "latest_bar": dict(bars[-1]),
            "bar_count": len(bars),
        }

    def get_market_overview(self, *, symbol: str, latest_bar: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized_symbol = normalize_ashare_symbol(symbol)
        spot = self._spot_from_latest_bar(normalized_symbol=normalized_symbol, latest_bar=latest_bar)
        if not spot:
            spot = self._get_basic_spot(normalized_symbol)
        valuation = self._get_latest_valuation(symbol)
        normalized = self._normalize_market_overview(
            symbol=symbol,
            normalized_symbol=normalized_symbol,
            spot=spot,
            valuation=valuation,
        )
        normalized["spot_found"] = bool(spot)
        normalized["valuation_found"] = bool(valuation)
        return normalized

    def _spot_from_latest_bar(self, *, normalized_symbol: str, latest_bar: dict[str, Any] | None) -> dict[str, Any]:
        if not latest_bar:
            return {}
        latest_price = self._first_numeric(latest_bar.get("close"))
        pct_change = self._pct_change_from_latest_bar(latest_bar)
        amount = self._first_numeric(latest_bar.get("amount"))
        open_price = self._first_numeric(latest_bar.get("open"))
        high_price = self._first_numeric(latest_bar.get("high"))
        low_price = self._first_numeric(latest_bar.get("low"))
        previous_close = self._previous_close_from_latest_bar(latest_bar)
        volume = self._first_numeric(latest_bar.get("volume"))
        snapshot = {
            "代码": normalized_symbol,
            "最新价": latest_price,
            "涨跌幅": pct_change,
            "成交额": amount,
            "今开": open_price,
            "最高": high_price,
            "最低": low_price,
            "昨收": previous_close,
            "成交量": volume,
        }
        return {key: value for key, value in snapshot.items() if value is not None}

    def _get_basic_spot(self, normalized_symbol: str) -> dict[str, Any]:
        spot_df = self._spot_dataframe()
        if spot_df.empty:
            raise ValueError("no spot data returned")
        symbol_column = "代码" if "代码" in spot_df.columns else spot_df.columns[0]
        symbol_series = spot_df[symbol_column].astype(str).str.lower()
        row = spot_df[symbol_series == normalized_symbol]
        if row.empty:
            raw_symbol = normalized_symbol[2:] if len(normalized_symbol) > 2 else normalized_symbol
            row = spot_df[symbol_series == raw_symbol]
        if row.empty:
            return {}
        latest = row.iloc[0].to_dict()
        return {str(k): normalize_value(v) for k, v in latest.items()}

    @classmethod
    def _spot_dataframe(cls):
        now = datetime.now(UTC)
        if cls._spot_cache_df is not None and cls._spot_cache_at is not None:
            if now - cls._spot_cache_at <= cls._spot_cache_ttl:
                return cls._spot_cache_df
        spot_df = call_with_retry(ak.stock_zh_a_spot)
        cls._spot_cache_df = spot_df
        cls._spot_cache_at = now
        return spot_df

    def _get_latest_valuation(self, symbol: str) -> dict[str, Any]:
        try:
            valuation_df = call_with_retry(
                lambda: ak.stock_value_em(symbol=symbol),
                retries=2,
                delay_seconds=0.5,
            )
        except Exception as exc:
            return {"availability": "degraded", "note": f"stock_value_unavailable: {exc}"}
        if valuation_df.empty:
            return {}
        latest = valuation_df.iloc[-1].to_dict()
        return {str(k): normalize_value(v) for k, v in latest.items()}

    def _normalize_market_overview(
        self,
        *,
        symbol: str,
        normalized_symbol: str,
        spot: dict[str, Any],
        valuation: dict[str, Any],
    ) -> dict[str, Any]:
        latest_price = self._first_numeric(
            spot.get("最新价"),
            spot.get("close"),
            valuation.get("当日收盘价"),
        )
        pct_change = self._first_numeric(
            spot.get("涨跌幅"),
            valuation.get("当日涨跌幅"),
        )
        amount = self._first_numeric(spot.get("成交额"))
        overview = {
            "symbol": symbol,
            "normalized_symbol": normalized_symbol,
            "latest_price": latest_price,
            "pct_change": pct_change,
            "amount": amount,
            "pb": self._first_numeric(valuation.get("市净率")),
            "pe_ttm": self._first_numeric(valuation.get("PE(TTM)")),
            "pe_static": self._first_numeric(valuation.get("PE(静)")),
            "peg": self._first_numeric(valuation.get("PEG值")),
            "pcf": self._first_numeric(valuation.get("市现率")),
            "ps": self._first_numeric(valuation.get("市销率")),
            "market_cap": self._first_numeric(valuation.get("总市值")),
            "float_market_cap": self._first_numeric(valuation.get("流通市值")),
            "total_shares": self._first_numeric(valuation.get("总股本")),
            "float_shares": self._first_numeric(valuation.get("流通股本")),
            "valuation_date": normalize_value(valuation.get("数据日期")),
            "spot": spot,
            "valuation": valuation,
        }
        return overview

    def _pct_change_from_latest_bar(self, latest_bar: dict[str, Any]) -> float | None:
        close_price = self._first_numeric(latest_bar.get("close"))
        previous_close = self._previous_close_from_latest_bar(latest_bar)
        if close_price is None or previous_close in (None, 0):
            return None
        return ((close_price - previous_close) / previous_close) * 100.0

    def _previous_close_from_latest_bar(self, latest_bar: dict[str, Any]) -> float | None:
        previous_close = self._first_numeric(latest_bar.get("pre_close"), latest_bar.get("昨收"))
        if previous_close is not None:
            return previous_close
        close_price = self._first_numeric(latest_bar.get("close"))
        pct_change = self._first_numeric(latest_bar.get("pct_change"), latest_bar.get("涨跌幅"))
        if close_price is not None and pct_change not in (None, -100):
            return close_price / (1.0 + pct_change / 100.0)
        return self._first_numeric(latest_bar.get("open"))

    def _first_numeric(self, *values: Any) -> float | None:
        for value in values:
            parsed = coerce_numeric(value)
            if parsed is not None:
                return parsed
        return None
