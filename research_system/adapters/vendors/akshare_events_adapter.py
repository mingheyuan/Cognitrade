from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import akshare as ak

from research_system.adapters.vendors.akshare_utils import call_with_retry, normalize_value


class AkShareEventsAdapter:
    vendor_name = "akshare"

    def get_events_snapshot(self, *, symbol: str) -> dict[str, Any]:
        notices_df = call_with_retry(
            lambda: ak.stock_zh_a_disclosure_report_cninfo(
                symbol=symbol,
                start_date=(date.today() - timedelta(days=30)).strftime("%Y%m%d"),
                end_date=date.today().strftime("%Y%m%d"),
            ),
            retries=2,
            delay_seconds=0.5,
        )
        if notices_df.empty:
            raise ValueError(f"no event data returned for symbol {symbol}")
        rows = [self._normalize_row(item) for item in notices_df.head(8).to_dict("records")]
        return {
            "symbol": symbol,
            "event_notices": rows,
            "event_keywords": self._extract_event_keywords(rows),
        }

    def _normalize_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {str(key): normalize_value(value) for key, value in row.items()}

    def _extract_event_keywords(self, rows: list[dict[str, Any]]) -> list[str]:
        keywords = ("业绩", "合同", "中标", "回购", "激励", "临床", "注册", "合作", "减持", "增持")
        hits: list[str] = []
        for row in rows:
            text = " ".join(str(value) for value in row.values())
            for keyword in keywords:
                if keyword in text and keyword not in hits:
                    hits.append(keyword)
        return hits
