from __future__ import annotations

from typing import Any

from research_system.adapters.vendors.mx_api_adapter import MxApiAdapter
from research_system.adapters.vendors.uzi_web_search_adapter import UziWebSearchAdapter
from research_system.runtime.platform.settings import Settings


JUNK_PATTERNS = (
    "类型；类型",
    "XXX",
    "TODO",
    "null",
    "undefined",
    "None",
    "抱歉，",
    "无法回答",
    "我不知道",
    "不清楚",
    "暂无数据",
    "（示例）",
    "（待补）",
)


def is_junk_fallback_text(text: str) -> bool:
    value = str(text or "").strip()
    if len(value) < 5 or any(pattern in value for pattern in JUNK_PATTERNS):
        return True
    parts = [part.strip() for part in value.split("；") if part.strip()]
    return len(parts) >= 2 and len(set(parts)) == 1


class UziQualitativeFallback:
    """Pack-native UZI 3.9.2 MX → DDGS qualitative fallback stage.

    Every stage returns only real provider output.  Playwright is deliberately
    a separate final pipeline stage so it cannot run before the integrity
    report and MX/DDGS recovery have completed.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def collect(
        self,
        *,
        query: str,
        dimension: str,
        symbol: str,
    ) -> dict[str, Any]:
        trace: list[dict[str, Any]] = []

        if self.settings.mx_api_key:
            try:
                payload = MxApiAdapter(self.settings.mx_api_key).query_text(query=query)
                text = str(payload.get("text") or "").strip()
                if not is_junk_fallback_text(text):
                    trace.append({"source_id": "mx_api", "status": "available", "transport": "api"})
                    return {
                        "status": "available",
                        "source_id": "mx_api",
                        "text": text,
                        "query": query,
                        "source_trace": trace,
                    }
                trace.append({"source_id": "mx_api", "status": "parse_failed", "reason": "junk_filtered"})
            except Exception as exc:
                trace.append({"source_id": "mx_api", "status": "provider_unavailable", "error_type": exc.__class__.__name__})
        else:
            trace.append({"source_id": "mx_api", "status": "disabled", "reason": "credentials_missing"})

        try:
            payload = UziWebSearchAdapter().search(
                query=query,
                dimension=dimension,
                max_results=3,
            )
            snippets = []
            for row in list(payload.get("results") or [])[:3]:
                if not isinstance(row, dict):
                    continue
                title = str(row.get("title") or "").strip()
                body = str(row.get("body") or "").strip()
                if title or body:
                    snippets.append(f"{title} — {body[:80]}".strip(" —"))
            text = "；".join(snippets)[:300]
            trace.extend(list(payload.get("source_trace") or []))
            if not is_junk_fallback_text(text):
                return {
                    "status": "available",
                    "source_id": "ddgs",
                    "text": text,
                    "query": query,
                    "results": list(payload.get("results") or []),
                    "source_trace": trace,
                }
            trace.append({"source_id": "ddgs", "status": "parse_failed", "reason": "junk_filtered"})
        except Exception as exc:
            trace.append({"source_id": "ddgs", "status": "provider_unavailable", "error_type": exc.__class__.__name__})

        return {
            "status": "data_gap",
            "source_id": None,
            "text": "",
            "query": query,
            "source_trace": trace,
            "data_gap": ["mx_ddgs_exhausted"],
        }


__all__ = ["JUNK_PATTERNS", "UziQualitativeFallback", "is_junk_fallback_text"]
