from __future__ import annotations

from typing import Any, Callable
from urllib.parse import quote

import requests


class UziHotTrendAdapter:
    vendor_name = "hottrend"
    _UA_PC = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
    _UA_MOBILE = "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"

    def get_mentions(self, *, name: str, extra_keywords: list[str] | None = None) -> dict[str, Any]:
        keywords = self._keywords(name, extra_keywords or [])
        loaders: dict[str, Callable[[], list[dict[str, Any]]]] = {
            "weibo": self._weibo,
            "zhihu": self._zhihu,
            "baidu": self._baidu,
            "douyin": self._douyin,
            "toutiao": self._toutiao,
            "bilibili": self._bilibili,
        }
        mentions: dict[str, list[dict[str, Any]]] = {}
        trace = []
        for platform, loader in loaders.items():
            try:
                rows = loader()
                matches = [row for row in rows if any(keyword in str(row.get("title") or "") for keyword in keywords)]
                mentions[platform] = matches
                trace.append({"source_id": platform, "status": "available", "count": len(matches)})
            except Exception as exc:
                mentions[platform] = []
                trace.append({"source_id": platform, "status": "provider_unavailable", "error_type": exc.__class__.__name__})
        return {
            "keywords_used": keywords,
            "mentions": mentions,
            "total_hits": sum(len(rows) for rows in mentions.values()),
            "platforms_ok": sum(row["status"] == "available" for row in trace),
            "source_trace": trace,
        }

    def _weibo(self) -> list[dict[str, Any]]:
        data = self._json("https://weibo.com/ajax/side/hotSearch")
        return [self._item(index, row.get("word"), f"https://s.weibo.com/weibo?q={quote(str(row.get('word') or ''))}", row.get("num")) for index, row in enumerate(((data.get("data") or {}).get("realtime") or [])[:50], 1) if row.get("word")]

    def _zhihu(self) -> list[dict[str, Any]]:
        data = self._json("https://www.zhihu.com/api/v3/feed/topstory/hot-list-web?limit=50&desktop=true")
        rows = []
        for index, item in enumerate((data.get("data") or [])[:50], 1):
            target = item.get("target") or {}
            title = (target.get("title_area") or {}).get("text") or target.get("title")
            if title:
                rows.append(self._item(index, title, ((target.get("link") or {}).get("url") or ""), 0))
        return rows

    def _baidu(self) -> list[dict[str, Any]]:
        data = self._json("https://top.baidu.com/api/board?platform=wise&tab=realtime", mobile=True)
        cards = (data.get("data") or {}).get("cards") or []
        content = (cards[0] or {}).get("content") or [] if cards else []
        return [self._item(index, row.get("word") or row.get("query"), row.get("url") or "", row.get("hotScore")) for index, row in enumerate(content[:50], 1) if row.get("word") or row.get("query")]

    def _douyin(self) -> list[dict[str, Any]]:
        data = self._json("https://www.douyin.com/aweme/v1/web/hot/search/list/")
        return [self._item(index, row.get("word"), f"https://www.douyin.com/search/{quote(str(row.get('word') or ''))}", row.get("hot_value")) for index, row in enumerate(((data.get("data") or {}).get("word_list") or [])[:50], 1) if row.get("word")]

    def _toutiao(self) -> list[dict[str, Any]]:
        data = self._json("https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc")
        return [self._item(index, row.get("Title") or row.get("title"), "", row.get("HotValue")) for index, row in enumerate((data.get("data") or [])[:50], 1) if row.get("Title") or row.get("title")]

    def _bilibili(self) -> list[dict[str, Any]]:
        data = self._json("https://s.search.bilibili.com/main/hotword?limit=50")
        return [self._item(index, row.get("keyword") or row.get("show_name"), "", row.get("heat_score")) for index, row in enumerate((data.get("list") or [])[:50], 1) if row.get("keyword") or row.get("show_name")]

    def _json(self, url: str, *, mobile: bool = False) -> dict[str, Any]:
        response = requests.get(url, headers={"User-Agent": self._UA_MOBILE if mobile else self._UA_PC}, timeout=15)
        response.raise_for_status()
        return dict(response.json() or {})

    @staticmethod
    def _item(rank: int, title: Any, url: Any, score: Any) -> dict[str, Any]:
        return {"rank": rank, "title": str(title or ""), "url": str(url or ""), "hot_score": score or 0}

    @staticmethod
    def _keywords(name: str, extras: list[str]) -> list[str]:
        values = [name, name[:2] if len(name) >= 3 else "", name[-2:] if len(name) >= 4 else "", *extras]
        return list(dict.fromkeys(value.strip() for value in values if value and len(value.strip()) >= 2))
