from __future__ import annotations

import os
from queue import Queue
from threading import Thread
from typing import Any
from urllib.parse import urlparse

TRUSTED_DOMAINS_BY_DIM: dict[str, tuple[str, ...]] = {
    "macro": (
        "stats.gov.cn", "pbc.gov.cn", "safe.gov.cn", "gov.cn",
        "chinamoney.com.cn", "chinabond.com.cn", "cs.com.cn", "cnstock.com", "stcn.com", "nbd.com.cn",
    ),
    "policy": (
        "gov.cn", "csrc.gov.cn", "miit.gov.cn", "ndrc.gov.cn", "samr.gov.cn",
        "pbc.gov.cn", "safe.gov.cn", "cfachina.org", "cs.com.cn", "cnstock.com", "stcn.com",
    ),
    "events": (
        "cs.com.cn", "cnstock.com", "stcn.com", "nbd.com.cn", "sse.com.cn",
        "szse.cn", "hkexnews.hk", "yicai.com", "cls.cn", "wallstreetcn.com",
    ),
    "sentiment": (
        "xueqiu.com", "guba.eastmoney.com", "tgb.cn", "jisilu.cn", "zhihu.com", "nbd.com.cn", "stcn.com",
    ),
    "trap": (
        "zhihu.com", "weibo.com", "xiaohongshu.com", "douyin.com", "tgb.cn", "guba.eastmoney.com", "cs.com.cn", "nbd.com.cn",
    ),
    "industry": ("stats.gov.cn", "miit.gov.cn", "ndrc.gov.cn", "cs.com.cn", "cnstock.com", "stcn.com", "nbd.com.cn"),
    "chain": ("cninfo.com.cn", "10jqka.com.cn", "yicai.com", "stcn.com", "nbd.com.cn"),
    "peers": ("data.eastmoney.com", "10jqka.com.cn", "cninfo.com.cn", "cnstock.com", "stcn.com"),
    "research": ("cnstock.com", "stockstar.com", "hexun.com", "cs.com.cn", "stcn.com", "nbd.com.cn"),
    "governance": ("cninfo.com.cn", "sse.com.cn", "szse.cn", "csrc.gov.cn", "cnstock.com", "stcn.com"),
    "moat": ("nbd.com.cn", "yicai.com", "cs.com.cn", "stcn.com", "wallstreetcn.com", "cls.cn"),
    "materials": ("shfe.com.cn", "dce.com.cn", "czce.com.cn", "ine.cn", "100ppi.com", "99qh.com", "fx678.com"),
    "futures": ("shfe.com.cn", "dce.com.cn", "czce.com.cn", "ine.cn", "99qh.com", "cfachina.org", "fx678.com"),
}

_GARBAGE = ("拼音", "汉语", "通用规范汉字", "部首", "笔画", "Unicode", "字形演变")

_DOMAIN_SOURCE_IDS = {
    "stats.gov.cn": "stats_gov", "pbc.gov.cn": "pbc", "safe.gov.cn": "safe", "gov.cn": "gov_cn",
    "chinamoney.com.cn": "chinamoney", "chinabond.com.cn": "chinabond", "cs.com.cn": "cs_cn",
    "cnstock.com": "cnstock", "stcn.com": "stcn", "nbd.com.cn": "nbd", "csrc.gov.cn": "csrc",
    "miit.gov.cn": "miit", "ndrc.gov.cn": "ndrc", "samr.gov.cn": "samr", "sse.com.cn": "sse",
    "szse.cn": "szse", "hkexnews.hk": "hkexnews", "yicai.com": "yicai", "cls.cn": "cls",
    "wallstreetcn.com": "wallstreetcn", "xueqiu.com": "xueqiu_f10", "guba.eastmoney.com": "guba_em_list",
    "data.eastmoney.com": "em_data", "jisilu.cn": "jisilu", "10jqka.com.cn": "ths_f10",
    "shfe.com.cn": "shfe", "dce.com.cn": "dce", "czce.com.cn": "czce", "ine.cn": "ine",
    "100ppi.com": "100ppi", "fx678.com": "fx678", "cfachina.org": "cfachina",
    "99qh.com": "qh99_inventory",
    "stockstar.com": "stockstar", "hexun.com": "hexun",
}


class UziWebSearchAdapter:
    vendor_name = "ddgs"

    def search(
        self,
        *,
        query: str,
        dimension: str,
        max_results: int = 8,
        trusted: bool = True,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        from ddgs import DDGS

        domains = TRUSTED_DOMAINS_BY_DIM.get(dimension, ()) if trusted else ()
        scoped_query = query
        if domains:
            scoped_query = f"({' OR '.join(f'site:{domain}' for domain in domains[:10])}) {query}"
        configured_timeout = max(int(os.environ.get("UZI_DDG_TIMEOUT", "10")), 1)
        timeout_seconds = configured_timeout if timeout_seconds is None else min(configured_timeout, timeout_seconds)
        if timeout_seconds <= 0:
            raise TimeoutError("DDGS request deadline exceeded")

        def load():
            with DDGS(timeout=timeout_seconds) as client:
                return list(client.text(scoped_query, region="cn-zh", safesearch="off", max_results=max_results))

        result_queue: Queue[tuple[str, Any]] = Queue(maxsize=1)

        def guarded_load() -> None:
            try:
                result_queue.put(("ok", load()))
            except Exception as exc:
                result_queue.put(("error", exc))

        worker = Thread(target=guarded_load, daemon=True, name="uzi-ddgs-search")
        worker.start()
        worker.join(timeout_seconds)
        if worker.is_alive():
            raise ConnectionError("ddgs search timeout")
        status, value = result_queue.get_nowait()
        if status == "error":
            raise value
        raw = list(value)
        rows = []
        for row in raw:
            normalized = {
                "title": str(row.get("title") or "").strip(),
                "body": str(row.get("body") or row.get("snippet") or "").strip(),
                "url": str(row.get("href") or row.get("url") or "").strip(),
                "source": "ddgs",
            }
            normalized["source_id"] = self._source_id(normalized["url"])
            text = f"{normalized['title']} {normalized['body']}"
            if sum(token in text for token in _GARBAGE) >= 2:
                continue
            if normalized["title"] or normalized["body"]:
                rows.append(normalized)
        if not rows:
            raise ValueError("no web search data returned")
        return {
            "query": query,
            "dimension": dimension,
            "trusted_domains": list(domains),
            "results": rows,
            "result_count": len(rows),
            "source_trace": self._trace(rows),
        }

    @staticmethod
    def _source_id(url: str) -> str:
        host = (urlparse(url).hostname or "").lower()
        return next((source_id for domain, source_id in _DOMAIN_SOURCE_IDS.items() if host == domain or host.endswith(f".{domain}")), "ddgs")

    @staticmethod
    def _trace(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for row in rows:
            source_id = str(row.get("source_id") or "ddgs")
            counts[source_id] = counts.get(source_id, 0) + 1
        return [
            {"source_id": source_id, "status": "available", "count": count, "transport": "ddgs"}
            for source_id, count in counts.items()
        ]
