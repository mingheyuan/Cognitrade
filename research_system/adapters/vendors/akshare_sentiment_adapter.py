from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import akshare as ak

from research_system.adapters.vendors.akshare_utils import (
    call_with_retry,
    normalize_ashare_symbol,
    normalize_value,
)


class AkShareSentimentAdapter:
    vendor_name = "akshare"

    def get_sentiment_snapshot(
        self, *, symbol: str, as_of_date: date | None = None
    ) -> dict[str, Any]:
        market_symbol = normalize_ashare_symbol(symbol).upper()
        if as_of_date is None:
            hot_detail_df = call_with_retry(
                lambda: ak.stock_hot_rank_detail_em(symbol=market_symbol),
                retries=2,
                delay_seconds=0.5,
            )
            hot_latest_df = call_with_retry(
                lambda: ak.stock_hot_rank_latest_em(symbol=market_symbol),
                retries=2,
                delay_seconds=0.5,
            )
            hot_keywords_df = call_with_retry(
                lambda: ak.stock_hot_keyword_em(symbol=market_symbol),
                retries=2,
                delay_seconds=0.5,
            )
        else:
            hot_detail_df = hot_latest_df = hot_keywords_df = None
        try:
            news_df = call_with_retry(
                lambda: ak.stock_news_em(symbol=symbol), retries=2, delay_seconds=0.5
            )
        except Exception:
            news_df = None
        if as_of_date is not None and news_df is not None:
            news_df = self._filter_news(news_df, as_of_date)

        facts = {
            "symbol": symbol,
            "market_symbol": market_symbol,
            # The current rank and universe size are separate item/value rows;
            # truncating to five drops `rank` and makes the 0-100 percentile
            # impossible to calculate.
            "hot_rank_latest": [
                self._normalize_row(item) for item in hot_latest_df.to_dict("records")
            ]
            if hot_latest_df is not None and not hot_latest_df.empty
            else [],
            "hot_rank_detail": [
                self._normalize_row(item)
                for item in hot_detail_df.head(10).to_dict("records")
            ]
            if hot_detail_df is not None and not hot_detail_df.empty
            else [],
            "hot_keywords": [
                self._normalize_row(item)
                for item in hot_keywords_df.head(10).to_dict("records")
            ]
            if hot_keywords_df is not None and not hot_keywords_df.empty
            else [],
            "news_samples": [
                self._normalize_row(item) for item in news_df.head(5).to_dict("records")
            ]
            if news_df is not None and not news_df.empty
            else [],
            "current_only_fields_omitted": [
                "hot_rank_latest",
                "hot_rank_detail",
                "hot_keywords",
            ]
            if as_of_date is not None
            else [],
        }
        if not any(
            facts[field]
            for field in (
                "hot_rank_latest",
                "hot_rank_detail",
                "hot_keywords",
                "news_samples",
            )
        ):
            raise ValueError(f"no sentiment data returned for symbol {symbol}")
        return facts

    def _normalize_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {str(key): normalize_value(value) for key, value in row.items()}

    @staticmethod
    def _filter_news(frame, as_of_date: date):
        start = as_of_date - timedelta(days=30)
        for column in ("发布时间", "发布日期", "日期", "date", "publish_time"):
            if column not in frame.columns:
                continue
            parsed = frame[column].map(AkShareSentimentAdapter._date_value)
            return frame[
                parsed.map(
                    lambda value: value is not None and start <= value <= as_of_date
                )
            ]
        return frame.iloc[0:0]

    @staticmethod
    def _date_value(value: Any) -> date | None:
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
        return None
