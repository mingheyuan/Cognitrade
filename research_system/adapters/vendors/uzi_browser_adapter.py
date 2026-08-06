from __future__ import annotations

from typing import Any
from urllib.parse import quote

from research_system.runtime.platform.settings import Settings


class UziBrowserAdapter:
    """Explicitly gated Playwright evidence collector for UZI tier-2 sources."""

    vendor_name = "playwright"
    _URLS = {
        "iwencai": lambda symbol, query: f"https://www.iwencai.com/unifiedwap/result?w={quote(query or symbol)}",
        "ths_f10": lambda symbol, _query: f"https://stockpage.10jqka.com.cn/{_digits(symbol)}/",
        "xueqiu_f10": lambda symbol, _query: f"https://xueqiu.com/S/{_xueqiu_symbol(symbol)}",
        "legulegu": lambda symbol, query: f"https://legulegu.com/search?q={quote(query or symbol)}",
        "stockstar": lambda symbol, _query: f"https://stock.quote.stockstar.com/{_digits(symbol)}.shtml",
        "futu": lambda symbol, _query: f"https://www.futunn.com/stock/{symbol}",
        "yuncaijing": lambda symbol, _query: f"https://www.yuncaijing.com/stock/details/id/{_digits(symbol)}.html",
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def collect_many(self, *, source_ids: tuple[str, ...], symbol: str, query: str) -> dict[str, Any]:
        evidence: dict[str, dict[str, Any]] = {}
        trace: list[dict[str, Any]] = []
        for source_id in source_ids:
            reason = self._policy_gap(source_id)
            if reason:
                trace.append({"source_id": source_id, "status": "disabled", "reason": reason})
                continue
            try:
                item = self._collect(source_id=source_id, symbol=symbol, query=query)
                excerpt = str(item.get("text_excerpt") or "").strip()
                blocked = any(
                    marker in excerpt.lower()
                    for marker in ("just a moment", "access denied", "captcha", "验证后继续")
                )
                if excerpt and not blocked:
                    evidence[source_id] = item
                trace.append(
                    {
                        "source_id": source_id,
                        "status": "available" if excerpt and not blocked else "no_data",
                    }
                )
            except Exception as exc:
                trace.append({"source_id": source_id, "status": "provider_unavailable", "error_type": exc.__class__.__name__})
        return {"evidence": evidence, "source_trace": trace}

    def _policy_gap(self, source_id: str) -> str | None:
        if not self.settings.browser_sources_enabled:
            return "browser_sources_disabled"
        if not self.settings.sensitive_sources_enabled:
            return "sensitive_sources_disabled"
        aliases = {source_id}
        if source_id == "xueqiu_f10":
            aliases.add("xueqiu")
        if not aliases.intersection(self.settings.sensitive_source_allowlist):
            return "source_not_allowlisted"
        if source_id == "xueqiu_f10" and not self.settings.xueqiu_cookie:
            return "credentials_missing"
        return None

    def _collect(self, *, source_id: str, symbol: str, query: str) -> dict[str, Any]:
        from playwright.sync_api import sync_playwright

        url_builder = self._URLS.get(source_id)
        if url_builder is None:
            raise ValueError(f"unknown browser source {source_id}")
        url = url_builder(symbol, query)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
            )
            if source_id == "xueqiu_f10" and self.settings.xueqiu_cookie:
                context.add_cookies(_cookies(self.settings.xueqiu_cookie, ".xueqiu.com"))
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            title = page.title()
            text = " ".join(page.locator("body").inner_text(timeout=5_000).split())[:8_000]
            links = page.locator("a[href]").evaluate_all(
                "els => els.slice(0, 40).map(a => ({text: (a.innerText || '').trim().slice(0, 120), url: a.href}))"
            )
            browser.close()
        return {"source_id": source_id, "url": url, "title": title, "text_excerpt": text, "links": links}


def _digits(symbol: str) -> str:
    return "".join(char for char in str(symbol) if char.isdigit())[-6:]


def _xueqiu_symbol(symbol: str) -> str:
    raw = str(symbol).strip().upper()
    if raw.endswith(".HK"):
        return f"HK{raw.removesuffix('.HK').zfill(5)}"
    if any(char.isalpha() for char in raw) and not raw.startswith(("SH", "SZ", "BJ")):
        return raw
    code = _digits(raw)
    return f"BJ{code}" if code.startswith(("4", "8")) else f"SH{code}" if code.startswith(("5", "6", "9")) else f"SZ{code}"


def _cookies(header: str, domain: str) -> list[dict[str, Any]]:
    rows = []
    for part in header.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        if name.strip():
            rows.append({"name": name.strip(), "value": value.strip(), "domain": domain, "path": "/"})
    return rows
