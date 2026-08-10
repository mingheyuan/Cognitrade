from __future__ import annotations

import json
import re
from typing import Any, Callable

import requests


class UziNewsAdapter:
    """UZI 3.9.4 direct news sources: Jin10, EastMoney and THS."""

    vendor_name = "uzi_news_http"
    _UA = "Mozilla/5.0 (compatible; Cognitrade/1.0)"

    def get_news(self, *, symbol: str = "", name: str = "", limit: int = 20) -> dict[str, Any]:
        loaders: dict[str, Callable[[], list[dict[str, Any]]]] = {
            "jin10_flash": lambda: self._jin10(limit),
            "em_kuaixun": lambda: self._em_kuaixun(limit),
            "em_stock_ann": lambda: self._em_ann(symbol, limit),
            "ths_news_today": lambda: self._ths(limit),
        }
        sources: dict[str, list[dict[str, Any]]] = {}
        trace: list[dict[str, Any]] = []
        keywords = [token for token in (name, name[-2:] if len(name) >= 3 else "", symbol) if token]
        for source_id, loader in loaders.items():
            try:
                rows = loader()
                if keywords:
                    filtered = [row for row in rows if any(token in self._text(row) for token in keywords)]
                    rows = filtered or rows if source_id == "em_stock_ann" else filtered
                sources[source_id] = rows
                trace.append({"source_id": source_id, "status": "available" if rows else "no_data", "count": len(rows)})
            except Exception as exc:
                sources[source_id] = []
                trace.append({"source_id": source_id, "status": "provider_unavailable", "error_type": exc.__class__.__name__})
        if not any(sources.values()):
            raise ValueError("no direct news data returned")
        return {
            "sources": sources,
            "source_trace": trace,
            "total_hits": sum(len(rows) for rows in sources.values()),
        }

    def _jin10(self, limit: int) -> list[dict[str, Any]]:
        text = self._get("https://www.jin10.com/flash_newest.js")
        match = re.search(r"var newest\s*=\s*(\[.*?\]);", text, re.S)
        if not match:
            return []
        data = json.loads(match.group(1))
        rows = []
        for item in data[:limit]:
            body = (item.get("data") or {}).get("content") or ""
            title = (item.get("data") or {}).get("title") or re.sub(r"<[^>]+>", "", body)[:100]
            if title:
                rows.append({"source": "jin10_flash", "title": title, "body": re.sub(r"<[^>]+>", "", body)[:500], "time": item.get("time"), "url": "https://www.jin10.com/"})
        return rows

    def _em_kuaixun(self, limit: int) -> list[dict[str, Any]]:
        text = self._get("https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_50_1_.html")
        match = re.search(r"var ajaxResult\s*=\s*(\{.*\})\s*;?\s*$", text, re.S)
        if not match:
            return []
        data = json.loads(match.group(1))
        return [
            {
                "source": "em_kuaixun",
                "title": row.get("title") or str(row.get("digest") or "")[:100],
                "body": row.get("digest") or "",
                "time": row.get("showtime"),
                "url": row.get("url_mobile") or row.get("url_w") or "",
            }
            for row in (data.get("LivesList") or [])[:limit]
            if row.get("title") or row.get("digest")
        ]

    def _em_ann(self, symbol: str, limit: int) -> list[dict[str, Any]]:
        response = requests.get(
            "https://np-anotice-stock.eastmoney.com/api/security/ann",
            params={"sr": -1, "page_size": limit, "page_index": 1, "ann_type": "A", "client_source": "web"},
            headers={"User-Agent": self._UA},
            timeout=12,
        )
        response.raise_for_status()
        rows = []
        for item in ((response.json().get("data") or {}).get("list") or []):
            codes = [str(code.get("stock_code") or "") for code in item.get("codes") or []]
            if symbol and symbol[-6:] not in codes:
                continue
            rows.append({"source": "em_stock_ann", "title": item.get("title"), "body": item.get("art_code"), "time": item.get("notice_date") or item.get("display_time"), "url": "https://np-anotice-stock.eastmoney.com/"})
        return rows

    def _ths(self, limit: int) -> list[dict[str, Any]]:
        text = self._get("http://news.10jqka.com.cn/today_list/")
        titles = re.findall(r'<a[^>]*class="[^"]*title[^"]*"[^>]*>([^<]{5,120})</a>', text)
        if not titles:
            titles = re.findall(r"<li[^>]*>\s*<a[^>]*>([^<]{10,100})</a>", text)
        return [{"source": "ths_news_today", "title": title.strip(), "url": "http://news.10jqka.com.cn/today_list/"} for title in titles[:limit]]

    def _get(self, url: str) -> str:
        response = requests.get(url, headers={"User-Agent": self._UA}, timeout=12)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        return response.text

    @staticmethod
    def _text(row: dict[str, Any]) -> str:
        return f"{row.get('title') or ''} {row.get('body') or ''}"
