from __future__ import annotations

import re
from typing import Any

import requests

from research_system.adapters.vendors.akshare_utils import normalize_value
from research_system.runtime.platform.settings import Settings


class UziContestAdapter:
    """UZI contest sources with explicit policy for authenticated Xueqiu data."""

    vendor_name = "uzi_contests"
    _UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def get_snapshot(self, *, symbol: str) -> dict[str, Any]:
        trace: list[dict[str, Any]] = []
        xueqiu: list[dict[str, Any]] = []
        xueqiu_gap: str | None = None
        if not self.settings.sensitive_sources_enabled:
            xueqiu_gap = "sensitive_source_disabled:xueqiu"
        elif "xueqiu" not in self.settings.sensitive_source_allowlist:
            xueqiu_gap = "sensitive_source_not_allowlisted:xueqiu"
        elif not self.settings.xueqiu_cookie:
            xueqiu_gap = "sensitive_source_credentials_missing:xueqiu"
        else:
            try:
                xueqiu = self._xueqiu_http(symbol)
                if not xueqiu and self.settings.browser_sources_enabled:
                    xueqiu = self._xueqiu_playwright(symbol)
                trace.append({"source_id": "xueqiu_f10", "status": "available" if xueqiu else "no_data", "count": len(xueqiu)})
                if not xueqiu:
                    xueqiu_gap = "xueqiu_contests_empty"
            except Exception as exc:
                trace.append({"source_id": "xueqiu_f10", "status": "provider_unavailable", "error_type": exc.__class__.__name__})
                xueqiu_gap = "sensitive_source_request_failed:xueqiu"

        public_loaders = {
            "taoguba": lambda: self._taoguba(symbol),
            "ths_simulator": lambda: self._ths(symbol),
            "dpswang": self._dpswang,
        }
        public: dict[str, list[dict[str, Any]]] = {}
        for source_id, loader in public_loaders.items():
            try:
                rows = loader()
                public[source_id] = rows
                trace.append({"source_id": source_id, "status": "available" if rows else "no_data", "count": len(rows)})
            except Exception as exc:
                public[source_id] = []
                trace.append({"source_id": source_id, "status": "provider_unavailable", "error_type": exc.__class__.__name__})

        available = bool(xueqiu or any(public.values()))
        gaps = [] if available else [xueqiu_gap or "contest_sources_empty"]
        return {
            "xueqiu_cubes": xueqiu,
            "taoguba_mentions": public["taoguba"],
            "ths_simulator": public["ths_simulator"],
            "dpswang": public["dpswang"],
            "summary": {
                "cube_count": len(xueqiu),
                "high_return_cube_count": sum(1 for row in xueqiu if (self._number(row.get("total_gain")) or float("-inf")) > 50),
                "taoguba_mentions_count": len(public["taoguba"]),
                "ths_evidence_count": len(public["ths_simulator"]),
            },
            "availability": "available" if available else "data_gap",
            "data_gap": gaps,
            "degrade_reason": gaps[0] if gaps else None,
            "source_trace": trace,
            "source_policy": "explicit_sensitive_source_for_xueqiu_public_fallbacks_enabled",
        }

    def _xueqiu_http(self, symbol: str) -> list[dict[str, Any]]:
        response = requests.get(
            "https://xueqiu.com/query/v1/search/cube/stock.json",
            params={"q": self._xueqiu_symbol(symbol), "count": 50, "page": 1},
            headers={"User-Agent": self._UA, "Referer": "https://xueqiu.com/", "Cookie": self.settings.xueqiu_cookie or ""},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        return self._normalize_cubes(payload.get("list") or payload.get("cubes") or [])

    def _xueqiu_playwright(self, symbol: str) -> list[dict[str, Any]]:
        from playwright.sync_api import sync_playwright

        url = (
            "https://xueqiu.com/query/v1/search/cube/stock.json"
            f"?q={self._xueqiu_symbol(symbol)}&count=50&page=1"
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(user_agent=self._UA)
            context.add_cookies(self._playwright_cookies(self.settings.xueqiu_cookie or ""))
            response = context.request.get(url, headers={"Referer": "https://xueqiu.com/"})
            payload = response.json() if response.ok else {}
            browser.close()
        return self._normalize_cubes(payload.get("list") or payload.get("cubes") or [])

    def _taoguba(self, symbol: str) -> list[dict[str, Any]]:
        response = requests.get(
            "https://www.taoguba.com.cn/Article/list/all",
            params={"keyword": symbol[-6:]},
            headers={"User-Agent": self._UA},
            timeout=15,
        )
        response.raise_for_status()
        matches = re.findall(r'<a[^>]+href="(/Article/\d+/\d+)"[^>]*>([^<]{4,80})</a>', response.text)
        return [{"title": title.strip(), "url": f"https://www.taoguba.com.cn{href}"} for href, title in matches[:30]]

    def _ths(self, symbol: str) -> list[dict[str, Any]]:
        response = requests.get(
            "https://moni.10jqka.com.cn/holder/",
            params={"stock": symbol[-6:]},
            headers={"User-Agent": self._UA, "Referer": "https://moni.10jqka.com.cn/"},
            timeout=12,
        )
        response.raise_for_status()
        if "Just a moment" in response.text:
            return []
        matches = re.findall(r'<a[^>]+class="user[^"]*"[^>]*>([^<]+)</a>.*?(\d+\.\d+)%', response.text)
        return [{"nickname": name.strip(), "return_pct": float(value)} for name, value in matches[:20]]

    def _dpswang(self) -> list[dict[str, Any]]:
        response = requests.get("https://www.dpswang.com/match/list", headers={"User-Agent": self._UA}, timeout=12)
        response.raise_for_status()
        return [{"reachable": True, "url": response.url}] if response.text else []

    @staticmethod
    def _normalize_cubes(rows: list[Any]) -> list[dict[str, Any]]:
        normalized = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            owner = row.get("owner") if isinstance(row.get("owner"), dict) else {}
            cube_symbol = str(row.get("symbol") or "").strip()
            normalized.append(
                {
                    "name": normalize_value(row.get("name")),
                    "owner": normalize_value(owner.get("screen_name")),
                    "symbol": cube_symbol or None,
                    "daily_gain": normalize_value(row.get("daily_gain")),
                    "monthly_gain": normalize_value(row.get("monthly_gain")),
                    "total_gain": normalize_value(row.get("total_gain")),
                    "annualized_gain_rate": normalize_value(row.get("annualized_gain_rate")),
                    "stocks_count": normalize_value(row.get("stocks_count")),
                    "url": f"https://xueqiu.com/P/{cube_symbol}" if cube_symbol else None,
                }
            )
        return normalized

    @staticmethod
    def _playwright_cookies(cookie_header: str) -> list[dict[str, Any]]:
        cookies = []
        for part in cookie_header.split(";"):
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            if name.strip():
                cookies.append({"name": name.strip(), "value": value.strip(), "domain": ".xueqiu.com", "path": "/"})
        return cookies

    @staticmethod
    def _xueqiu_symbol(symbol: str) -> str:
        normalized = str(symbol or "").strip().upper()
        if normalized.endswith(".HK"):
            return f"HK{normalized.removesuffix('.HK').zfill(5)}"
        if any(char.isalpha() for char in normalized) and not normalized.startswith(("SH", "SZ", "BJ")):
            return normalized
        if normalized.startswith(("SH", "SZ", "BJ")):
            return normalized
        return f"BJ{normalized}" if normalized.startswith(("4", "8")) else f"SH{normalized}" if normalized.startswith(("5", "6", "9")) else f"SZ{normalized}"

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
