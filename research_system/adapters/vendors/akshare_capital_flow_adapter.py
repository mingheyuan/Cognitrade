from __future__ import annotations

from datetime import date, datetime
from typing import Any

import akshare as ak
from requests.exceptions import RequestException

from research_system.adapters.vendors.akshare_utils import (
    call_with_retry,
    normalize_ashare_symbol,
    normalize_value,
)
from research_system.adapters.vendors.provider_execution import (
    classify_provider_exception,
)


class AkShareCapitalFlowAdapter:
    vendor_name = "akshare"

    def get_capital_flow_snapshot(
        self, *, symbol: str, as_of_date: date | None = None
    ) -> dict[str, Any]:
        market = self._market_code(symbol)
        individual_df = call_with_retry(
            lambda: ak.stock_individual_fund_flow(stock=symbol, market=market),
            retries=2,
            delay_seconds=0.5,
        )
        if as_of_date is not None:
            individual_df = self._filter_frame_as_of(individual_df, as_of_date)
        if individual_df.empty:
            raise ValueError(f"no capital flow data returned for symbol {symbol}")

        main_rank_status = "not_queried_for_historical_point_in_time"
        main_rank_df = None
        if as_of_date is None:
            try:
                main_rank_df = call_with_retry(
                    lambda: ak.stock_main_fund_flow(symbol="全部股票"),
                    retries=2,
                    delay_seconds=0.5,
                )
            except (OSError, RequestException) as exc:
                # The individual flow is still useful when the optional ranking endpoint is unavailable.
                main_rank_status = classify_provider_exception(exc)
        normalized_symbol = normalize_ashare_symbol(symbol)
        rank_row = None
        if main_rank_df is not None and not main_rank_df.empty:
            for row in main_rank_df.to_dict("records"):
                code = (
                    str(
                        row.get("代码") or row.get("股票代码") or row.get("代码 ") or ""
                    )
                    .strip()
                    .lower()
                )
                code = code[-6:] if len(code) >= 6 else code
                if code == symbol or code == normalized_symbol[-6:]:
                    rank_row = self._normalize_row(row)
                    break
        if main_rank_df is not None:
            main_rank_status = (
                "no_data"
                if main_rank_df.empty
                else "available"
                if rank_row is not None
                else "no_match"
            )

        rows = [self._normalize_row(item) for item in individual_df.to_dict("records")]
        rows.sort(key=self._row_date, reverse=True)
        latest = rows[0]
        recent = rows[:20]
        return {
            "symbol": symbol,
            "market": market,
            "latest_flow": latest,
            "recent_flows": recent,
            "main_flow_rank": rank_row,
            "main_flow_rank_status": main_rank_status,
        }

    def _market_code(self, symbol: str) -> str:
        normalized = normalize_ashare_symbol(symbol)
        if normalized.startswith("sh"):
            return "sh"
        if normalized.startswith("bj"):
            return "bj"
        return "sz"

    def _normalize_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {str(key): normalize_value(value) for key, value in row.items()}

    @classmethod
    def _filter_frame_as_of(cls, frame, as_of_date: date):
        for column in ("日期", "交易日期", "trade_date", "date"):
            if column not in frame.columns:
                continue
            parsed = frame[column].map(cls._row_date)
            return frame[
                parsed.map(lambda value: value != date.min and value <= as_of_date)
            ]
        return frame.iloc[0:0]

    @staticmethod
    def _row_date(row_or_value: Any) -> date:
        value = row_or_value
        if isinstance(row_or_value, dict):
            value = next(
                (
                    row_or_value.get(key)
                    for key in ("日期", "交易日期", "trade_date", "date")
                    if row_or_value.get(key) not in (None, "")
                ),
                None,
            )
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value or "").strip().replace("/", "-")
        for fmt in ("%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(text[:10], fmt).date()
            except ValueError:
                continue
        return date.min
