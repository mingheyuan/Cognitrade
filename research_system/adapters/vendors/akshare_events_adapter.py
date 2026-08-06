from __future__ import annotations

from datetime import datetime
from typing import Any

import requests

class AkShareEventsAdapter:
    vendor_name = "akshare"

    def get_events_snapshot(self, *, symbol: str) -> dict[str, Any]:
        rows = self._cninfo_direct(symbol=symbol, limit=30)
        if not rows:
            raise ValueError(f"no event data returned for symbol {symbol}")
        return {
            "symbol": symbol,
            "event_notices": rows[:10],
            "event_keywords": self._extract_event_keywords(rows),
            "source_id": "cninfo",
        }

    def _cninfo_direct(self, *, symbol: str, limit: int) -> list[dict[str, Any]]:
        code = str(symbol or "").strip().upper().removeprefix("SH").removeprefix("SZ").removeprefix("BJ")[-6:]
        if not code.isdigit():
            return []
        if code.startswith(("000", "001", "002", "3")):
            column = "szse"
        elif code.startswith(("600", "601", "603", "605", "688", "689")):
            column = "sse"
        else:
            column = "bse"
        response = requests.post(
            "http://www.cninfo.com.cn/new/hisAnnouncement/query",
            data={
                "pageNum": 1,
                "pageSize": limit,
                "column": column,
                "tabName": "fulltext",
                "plate": "",
                "stock": code,
                "searchkey": "",
                "secid": "",
                "category": "",
                "trade": "",
                "seDate": "",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            },
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; Cognitrade/1.0)",
                "Accept": "application/json",
                "Origin": "http://www.cninfo.com.cn",
                "Referer": "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
            },
            timeout=15,
        )
        response.raise_for_status()
        rows = []
        for item in (response.json().get("announcements") or [])[:limit]:
            timestamp = item.get("announcementTime") or 0
            try:
                published = datetime.fromtimestamp(timestamp / 1000).strftime("%Y-%m-%d") if timestamp else ""
            except (TypeError, ValueError, OSError):
                published = ""
            path = str(item.get("adjunctUrl") or "")
            rows.append(
                {
                    "公告时间": published,
                    "公告标题": str(item.get("announcementTitle") or ""),
                    "公告链接": f"http://static.cninfo.com.cn/{path.lstrip('/')}" if path else "",
                }
            )
        return rows

    def _extract_event_keywords(self, rows: list[dict[str, Any]]) -> list[str]:
        keywords = ("业绩", "合同", "中标", "回购", "激励", "临床", "注册", "合作", "减持", "增持")
        hits: list[str] = []
        for row in rows:
            text = " ".join(str(value) for value in row.values())
            for keyword in keywords:
                if keyword in text and keyword not in hits:
                    hits.append(keyword)
        return hits
