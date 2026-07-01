from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import akshare as ak

from research_system.adapters.vendors.akshare_utils import call_with_retry, normalize_value


class AkShareLHBAdapter:
    vendor_name = "akshare"

    def get_lhb_snapshot(self, *, symbol: str) -> dict[str, Any]:
        date_df = call_with_retry(lambda: ak.stock_lhb_stock_detail_date_em(symbol=symbol), retries=2, delay_seconds=0.5)
        if date_df.empty:
            raise ValueError(f"no lhb dates returned for symbol {symbol}")

        latest_date = self._extract_latest_date(date_df)
        buy_df = call_with_retry(
            lambda: ak.stock_lhb_stock_detail_em(symbol=symbol, date=latest_date, flag="买入"),
            retries=2,
            delay_seconds=0.5,
        )
        sell_df = call_with_retry(
            lambda: ak.stock_lhb_stock_detail_em(symbol=symbol, date=latest_date, flag="卖出"),
            retries=2,
            delay_seconds=0.5,
        )
        stats_df = call_with_retry(lambda: ak.stock_lhb_stock_statistic_em(symbol=symbol), retries=2, delay_seconds=0.5)

        return {
            "symbol": symbol,
            "latest_lhb_date": latest_date,
            "buy_detail": [self._normalize_row(item) for item in buy_df.head(10).to_dict("records")] if not buy_df.empty else [],
            "sell_detail": [self._normalize_row(item) for item in sell_df.head(10).to_dict("records")] if not sell_df.empty else [],
            "lhb_statistics": [self._normalize_row(item) for item in stats_df.head(5).to_dict("records")] if not stats_df.empty else [],
            "buy_top_total": self._sum_numeric_column(buy_df),
            "sell_top_total": self._sum_numeric_column(sell_df),
        }

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

    def _sum_numeric_column(self, df) -> float | None:
        if df is None or getattr(df, "empty", True):
            return None
        for column in df.columns:
            normalized = str(column)
            if "买入" in normalized or "卖出" in normalized or "金额" in normalized or "净额" in normalized:
                try:
                    values = [float(normalize_value(item)) for item in df[column].tolist() if item not in (None, "")]
                    if values:
                        return round(sum(values), 2)
                except Exception:
                    continue
        return None
