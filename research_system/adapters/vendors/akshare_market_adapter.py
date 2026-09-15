from __future__ import annotations

from typing import Any
from datetime import UTC, date, datetime, timedelta

import akshare as ak

from research_system.adapters.vendors.akshare_utils import (
    call_with_retry,
    coerce_numeric,
    normalize_ashare_symbol,
    normalize_value,
)
from research_system.adapters.vendors.provider_execution import (
    classify_provider_exception,
    failure_facts,
)


class AkShareMarketAdapter:
    vendor_name = "akshare"
    _spot_cache_df = None
    _spot_cache_at: datetime | None = None
    # Keep the spot table warm for the duration of a typical multi-stock run.
    _spot_cache_ttl = timedelta(minutes=5)
    _identity_cache: dict[str, dict[str, Any]] = {}

    def get_market_snapshot(
        self, *, symbol: str, start_date: str, end_date: str, adjust: str = ""
    ) -> dict[str, Any]:
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

    def get_market_overview(
        self,
        *,
        symbol: str,
        latest_bar: dict[str, Any] | None = None,
        as_of_date: date | None = None,
    ) -> dict[str, Any]:
        normalized_symbol = normalize_ashare_symbol(symbol)
        spot = self._spot_from_latest_bar(
            normalized_symbol=normalized_symbol, latest_bar=latest_bar
        )
        if not spot:
            spot = self._get_basic_spot(normalized_symbol)
        identity = self._get_identity(symbol)
        if identity.get("name"):
            spot["名称"] = identity["name"]
        if as_of_date is None and identity.get("industry"):
            spot["行业"] = identity["industry"]
        valuation, valuation_history = self._get_valuation_data(
            symbol, as_of_date=as_of_date
        )
        normalized = self._normalize_market_overview(
            symbol=symbol,
            normalized_symbol=normalized_symbol,
            spot=spot,
            valuation=valuation,
        )
        normalized["valuation_history"] = valuation_history
        normalized["spot_found"] = bool(spot)
        normalized["valuation_found"] = (
            bool(valuation)
            and str(valuation.get("availability") or "available") == "available"
        )
        return normalized

    def _spot_from_latest_bar(
        self, *, normalized_symbol: str, latest_bar: dict[str, Any] | None
    ) -> dict[str, Any]:
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
            raw_symbol = (
                normalized_symbol[2:]
                if len(normalized_symbol) > 2
                else normalized_symbol
            )
            row = spot_df[symbol_series == raw_symbol]
        if row.empty:
            return {}
        latest = row.iloc[0].to_dict()
        return {str(k): normalize_value(v) for k, v in latest.items()}

    @classmethod
    def _spot_dataframe(cls):
        now = datetime.now(UTC)
        if (
            cls._spot_cache_df is not None
            and cls._spot_cache_at is not None
            and now - cls._spot_cache_at <= cls._spot_cache_ttl
        ):
            return cls._spot_cache_df
        spot_df = call_with_retry(ak.stock_zh_a_spot)
        cls._spot_cache_df = spot_df
        cls._spot_cache_at = now
        return spot_df

    def _get_valuation_data(
        self, symbol: str, *, as_of_date: date | None = None
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        try:
            valuation_df = call_with_retry(
                lambda: ak.stock_value_em(symbol=symbol),
                retries=2,
                delay_seconds=0.5,
            )
        except Exception as exc:
            return failure_facts(classify_provider_exception(exc)), []
        if valuation_df.empty:
            return {}, []
        normalized_rows: list[dict[str, Any]] = []
        for row in valuation_df.to_dict("records"):
            normalized = {str(k): normalize_value(v) for k, v in row.items()}
            try:
                row_date = datetime.fromisoformat(
                    str(normalized.get("数据日期"))
                ).date()
            except (TypeError, ValueError):
                continue
            if as_of_date is None or row_date <= as_of_date:
                normalized_rows.append(normalized)
        if not normalized_rows:
            return {}, []
        normalized_rows.sort(key=lambda row: str(row.get("数据日期") or ""))
        latest_payload = dict(normalized_rows[-1])
        try:
            latest_date = datetime.fromisoformat(str(latest_payload.get("数据日期")))
        except (TypeError, ValueError):
            latest_date = datetime.now(UTC).replace(tzinfo=None)
        cutoff = latest_date - timedelta(days=5 * 366)
        history = []
        for normalized in normalized_rows:
            try:
                row_date = datetime.fromisoformat(str(normalized.get("数据日期")))
            except (TypeError, ValueError):
                continue
            if row_date >= cutoff:
                history.append(
                    {
                        "date": normalized.get("数据日期"),
                        "close": normalized.get("当日收盘价"),
                        "market_cap": normalized.get("总市值"),
                        "pe_ttm": normalized.get("PE(TTM)"),
                        "pb": normalized.get("市净率"),
                        "ps": normalized.get("市销率"),
                        "peg": normalized.get("PEG值"),
                    }
                )
        return latest_payload, history

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
        return {
            "symbol": symbol,
            "normalized_symbol": normalized_symbol,
            "security_name": spot.get("名称") or spot.get("股票简称"),
            "industry": spot.get("行业") or spot.get("所属行业"),
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

    @classmethod
    def _get_identity(cls, symbol: str) -> dict[str, Any]:
        normalized_symbol = "".join(
            character for character in str(symbol or "") if character.isdigit()
        )[-6:]
        if normalized_symbol in cls._identity_cache:
            return dict(cls._identity_cache[normalized_symbol])
        try:
            frame = call_with_retry(
                lambda: ak.stock_individual_info_em(symbol=normalized_symbol),
                retries=1,
                delay_seconds=0.2,
            )
        except Exception:
            frame = None
        if frame is None or getattr(frame, "empty", True):
            try:
                names = call_with_retry(
                    ak.stock_info_a_code_name,
                    retries=1,
                    delay_seconds=0.2,
                )
            except Exception:
                return {}
            code_column = next(
                (
                    column
                    for column in ("code", "代码", "证券代码")
                    if column in names.columns
                ),
                None,
            )
            name_column = next(
                (
                    column
                    for column in ("name", "名称", "证券简称")
                    if column in names.columns
                ),
                None,
            )
            if code_column is None or name_column is None:
                return {}
            matched = names[names[code_column].astype(str).str.zfill(6) == normalized_symbol]
            if matched.empty:
                return {}
            result = {"name": normalize_value(matched.iloc[0][name_column])}
            cls._identity_cache[normalized_symbol] = result
            return dict(result)
        if frame is None or getattr(frame, "empty", True):
            return {}
        item_column = "item" if "item" in frame.columns else frame.columns[0]
        value_column = "value" if "value" in frame.columns else frame.columns[-1]
        values = {
            str(row[item_column]): normalize_value(row[value_column])
            for _, row in frame.iterrows()
        }
        result = {
            "name": values.get("股票简称") or values.get("简称") or values.get("名称"),
            "industry": values.get("行业") or values.get("所属行业"),
        }
        cls._identity_cache[normalized_symbol] = result
        return dict(result)

    def _pct_change_from_latest_bar(self, latest_bar: dict[str, Any]) -> float | None:
        close_price = self._first_numeric(latest_bar.get("close"))
        previous_close = self._previous_close_from_latest_bar(latest_bar)
        if close_price is None or previous_close in (None, 0):
            return None
        return ((close_price - previous_close) / previous_close) * 100.0

    def _previous_close_from_latest_bar(
        self, latest_bar: dict[str, Any]
    ) -> float | None:
        previous_close = self._first_numeric(
            latest_bar.get("pre_close"), latest_bar.get("昨收")
        )
        if previous_close is not None:
            return previous_close
        close_price = self._first_numeric(latest_bar.get("close"))
        pct_change = self._first_numeric(
            latest_bar.get("pct_change"), latest_bar.get("涨跌幅")
        )
        if close_price is not None and pct_change not in (None, -100):
            return close_price / (1.0 + pct_change / 100.0)
        return self._first_numeric(latest_bar.get("open"))

    def _first_numeric(self, *values: Any) -> float | None:
        for value in values:
            parsed = coerce_numeric(value)
            if parsed is not None:
                return parsed
        return None
