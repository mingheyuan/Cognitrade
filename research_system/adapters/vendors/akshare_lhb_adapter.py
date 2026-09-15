from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

import akshare as ak

from research_system.adapters.vendors.akshare_utils import (
    call_with_retry,
    normalize_value,
)

_LHB_STATISTICS_PERIOD = "\u8fd1\u4e00\u6708"
_LHB_SYMBOL_COLUMNS = {
    "\u4ee3\u7801",
    "\u8bc1\u5238\u4ee3\u7801",
    "\u80a1\u7968\u4ee3\u7801",
    "security_code",
    "symbol",
}


class AkShareLHBAdapter:
    vendor_name = "akshare"

    def get_lhb_snapshot(
        self, *, symbol: str, as_of_date: date | None = None
    ) -> dict[str, Any]:
        provider_symbol = self._provider_symbol(symbol)
        target = as_of_date or date.today()
        window_start = target - timedelta(days=30)
        date_df = call_with_retry(
            lambda: ak.stock_lhb_stock_detail_date_em(symbol=provider_symbol),
            retries=2,
            delay_seconds=0.5,
        )
        historical_dates = (
            sorted(
                {
                    parsed
                    for row in date_df.to_dict("records")
                    for value in row.values()
                    if date.min < (parsed := self._date_value(value)) <= target
                }
            )
            if not date_df.empty
            else []
        )
        dates = [value for value in historical_dates if value >= window_start]
        latest_historical_date = (
            historical_dates[-1].strftime("%Y%m%d") if historical_dates else None
        )
        if not dates:
            # A successful empty date list is a verified zero-event result,
            # not a transport/data-source failure.  Preserve the observation
            # window so report completeness can distinguish it from the
            # native UZI fetcher's unverified empty fallback.
            return {
                "symbol": symbol,
                "latest_lhb_date": None,
                "latest_historical_lhb_date": latest_historical_date,
                "buy_detail": [],
                "sell_detail": [],
                "lhb_statistics": [],
                "lhb_count_30d": 0,
                "buy_top_total": None,
                "sell_top_total": None,
                "observation_status": "verified_empty",
                "observation_window_days": 30,
            }

        latest_date = dates[-1].strftime("%Y%m%d")
        buy_df = call_with_retry(
            lambda: ak.stock_lhb_stock_detail_em(
                symbol=provider_symbol, date=latest_date, flag="买入"
            ),
            retries=2,
            delay_seconds=0.5,
        )
        sell_df = call_with_retry(
            lambda: ak.stock_lhb_stock_detail_em(
                symbol=provider_symbol, date=latest_date, flag="卖出"
            ),
            retries=2,
            delay_seconds=0.5,
        )
        statistics: list[dict[str, Any]] = []
        if as_of_date is None:
            stats_df = call_with_retry(
                lambda: ak.stock_lhb_stock_statistic_em(symbol=_LHB_STATISTICS_PERIOD),
                retries=2,
                delay_seconds=0.5,
            )
            statistics = [
                self._normalize_row(row)
                for row in stats_df.to_dict("records")
                if self._matches_symbol(row, symbol)
            ]

        return {
            "symbol": symbol,
            "latest_lhb_date": latest_date,
            "latest_historical_lhb_date": latest_historical_date,
            "buy_detail": [
                self._normalize_row(item) for item in buy_df.head(10).to_dict("records")
            ]
            if not buy_df.empty
            else [],
            "sell_detail": [
                self._normalize_row(item)
                for item in sell_df.head(10).to_dict("records")
            ]
            if not sell_df.empty
            else [],
            "lhb_statistics": statistics[:5],
            "lhb_count_30d": (
                len(dates)
                if as_of_date is not None
                else self._event_count(statistics, default=1)
            ),
            "buy_top_total": self._sum_numeric_column(buy_df),
            "sell_top_total": self._sum_numeric_column(sell_df),
            "observation_status": "verified_non_empty",
            "observation_window_days": 30,
        }

    @staticmethod
    def _provider_symbol(symbol: str) -> str:
        """Convert CN symbol notation to the six-digit code expected by LHB APIs."""
        raw = str(symbol or "").strip().upper()
        match = re.fullmatch(
            r"(?:([0-9]{6})(?:\.(?:SH|SZ|BJ))?|(?:SH|SZ|BJ)\.?([0-9]{6}))",
            raw,
        )
        if match is None:
            raise ValueError(
                "AkShare LHB requires a six-digit CN stock code, optionally "
                "qualified by SH, SZ, or BJ"
            )
        return match.group(1) or match.group(2)

    @staticmethod
    def _date_value(value: str) -> date:
        normalized = str(value or "").replace("-", "").replace("/", "")
        try:
            return date(
                int(normalized[:4]),
                int(normalized[4:6]),
                int(normalized[6:8]),
            )
        except (TypeError, ValueError):
            return date.min

    @staticmethod
    def _event_count(rows: list[dict[str, Any]], *, default: int) -> int:
        values: list[int] = []
        for row in rows:
            for key in ("上榜次数", "count", "lhb_count_30d"):
                try:
                    value = int(float(row.get(key)))
                except (TypeError, ValueError):
                    continue
                if value >= 0:
                    values.append(value)
        return max(values) if values else default

    def _extract_latest_date(self, df) -> str:
        rows = df.to_dict("records")
        values: list[str] = []
        for row in rows:
            for value in row.values():
                normalized = str(normalize_value(value) or "").strip()
                digits = normalized.replace("-", "").replace("/", "")
                if len(digits) == 8 and digits.isdigit():
                    values.append(digits)
        if not values:
            latest = date.today() - timedelta(days=1)
            return latest.strftime("%Y%m%d")
        return max(values)

    def _normalize_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {str(key): normalize_value(value) for key, value in row.items()}

    def _matches_symbol(self, row: dict[str, Any], symbol: str) -> bool:
        target = self._symbol_text(symbol)
        if not target:
            return False
        for key, value in row.items():
            if (
                str(key).strip().lower() in _LHB_SYMBOL_COLUMNS
                and self._symbol_text(value) == target
            ):
                return True
        return False

    def _symbol_text(self, value: Any) -> str:
        normalized = str(normalize_value(value) or "").strip()
        normalized = normalized.removesuffix(".0")
        if normalized.isdigit():
            normalized = normalized.zfill(6)
        try:
            return self._provider_symbol(normalized)
        except ValueError:
            return ""

    def _sum_numeric_column(self, df) -> float | None:
        if df is None or getattr(df, "empty", True):
            return None
        for column in df.columns:
            normalized = str(column)
            if (
                "买入" in normalized
                or "卖出" in normalized
                or "金额" in normalized
                or "净额" in normalized
            ):
                try:
                    values = [
                        float(normalize_value(item))
                        for item in df[column].tolist()
                        if item not in (None, "")
                    ]
                    if values:
                        return round(sum(values), 2)
                except Exception:
                    continue
        return None
