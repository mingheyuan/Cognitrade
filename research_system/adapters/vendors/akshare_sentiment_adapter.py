from __future__ import annotations

from typing import Any

import akshare as ak

from research_system.adapters.vendors.akshare_utils import call_with_retry, normalize_ashare_symbol, normalize_value


class AkShareSentimentAdapter:
    vendor_name = "akshare"

    def get_sentiment_snapshot(self, *, symbol: str) -> dict[str, Any]:
        market_symbol = normalize_ashare_symbol(symbol).upper()
        hot_detail_df = call_with_retry(lambda: ak.stock_hot_rank_detail_em(symbol=market_symbol), retries=2, delay_seconds=0.5)
        hot_latest_df = call_with_retry(lambda: ak.stock_hot_rank_latest_em(symbol=market_symbol), retries=2, delay_seconds=0.5)
        hot_keywords_df = call_with_retry(lambda: ak.stock_hot_keyword_em(symbol=market_symbol), retries=2, delay_seconds=0.5)
        try:
            news_df = call_with_retry(lambda: ak.stock_news_em(symbol=symbol), retries=2, delay_seconds=0.5)
        except Exception:
            news_df = None

        facts = {
            "symbol": symbol,
            "market_symbol": market_symbol,
            # The current rank and universe size are separate item/value rows;
            # truncating to five drops `rank` and makes the 0-100 percentile
            # impossible to calculate.
            "hot_rank_latest": [self._normalize_row(item) for item in hot_latest_df.to_dict("records")] if not hot_latest_df.empty else [],
            "hot_rank_detail": [self._normalize_row(item) for item in hot_detail_df.head(10).to_dict("records")] if not hot_detail_df.empty else [],
            "hot_keywords": [self._normalize_row(item) for item in hot_keywords_df.head(10).to_dict("records")] if not hot_keywords_df.empty else [],
            "news_samples": [self._normalize_row(item) for item in news_df.head(5).to_dict("records")] if news_df is not None and not news_df.empty else [],
        }
        if not facts["hot_rank_latest"] and not facts["hot_rank_detail"] and not facts["hot_keywords"]:
            raise ValueError(f"no sentiment data returned for symbol {symbol}")
        return facts

    def _normalize_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {str(key): normalize_value(value) for key, value in row.items()}
