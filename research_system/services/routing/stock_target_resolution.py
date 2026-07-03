from __future__ import annotations

import re


STOCK_NAME_ALIASES: dict[str, str] = {
    "\u8d35\u5dde\u8305\u53f0": "600519",
    "\u8305\u53f0": "600519",
    "\u5b81\u5fb7\u65f6\u4ee3": "300750",
    "\u6bd4\u4e9a\u8fea": "002594",
    "\u4eac\u4e1c\u65b9a": "000725",
    "\u4eac\u4e1c\u65b9\uff21": "000725",
    "\u4eac\u4e1c\u65b9A": "000725",
    "\u5bd2\u6b66\u7eaa": "688256",
    "\u65b0\u6613\u76db": "300502",
    "\u5e73\u5b89\u94f6\u884c": "000001",
    "\u62db\u5546\u94f6\u884c": "600036",
    "\u5b81\u6ce2\u94f6\u884c": "002142",
    "\u5174\u4e1a\u94f6\u884c": "601166",
    "\u6c5f\u82cf\u94f6\u884c": "600919",
    "\u6210\u90fd\u94f6\u884c": "601838",
}

_COMPARE_KEYWORDS_CN = (
    "\u5bf9\u6bd4",
    "\u6bd4\u8f83",
    "\u54ea\u4e2a\u597d",
    "\u8c01\u66f4\u503c\u5f97",
    "\u8c01\u66f4\u5f3a",
    "\u54ea\u4e2a\u66f4\u597d",
    "\u600e\u4e48\u6392",
    "\u6392\u4e00\u4e0b",
)
_COMPARE_KEYWORDS_EN = (
    "compare",
    "comparison",
    "which is better",
    "which one is better",
    "better investment",
    "rank them",
)
_SCREEN_KEYWORDS_CN = (
    "\u63a8\u8350",
    "\u503c\u5f97",
    "\u5173\u6ce8",
    "\u9009\u80a1",
    "\u4ec0\u4e48\u80a1\u7968",
    "\u54ea\u4e9b\u80a1\u7968",
)
_SCREEN_KEYWORDS_EN = (
    "watchlist",
    "screen",
    "candidate",
    "worth",
    "follow",
    "what stock",
)
_EXPANSION_HINTS_CN = (
    "\u6269\u5230",
    "\u6269\u5c55\u5230",
    "\u6269\u4e00\u4e0b",
    "\u540c\u4e1a",
    "\u94f6\u884c\u80a1",
    "\u5238\u5546\u80a1",
    "\u767d\u9152\u80a1",
    "\u65b0\u80fd\u6e90",
)
_EXPANSION_HINTS_EN = (
    "expand to",
    "peer group",
    "peer basket",
    "sector peers",
)


def extract_stock_codes(query: str) -> list[str]:
    seen: list[str] = []
    for code in re.findall(r"\b(\d{6})\b", query):
        if code not in seen:
            seen.append(code)
    return seen


def extract_named_stock_symbols(query: str) -> list[str]:
    seen: list[str] = []
    lowered_query = query.lower()
    for alias, code in STOCK_NAME_ALIASES.items():
        if alias.lower() in lowered_query and code not in seen:
            seen.append(code)
    return seen


def extract_stock_symbols(query: str) -> list[str]:
    seen: list[str] = []
    for code in extract_stock_codes(query):
        if code not in seen:
            seen.append(code)
    for code in extract_named_stock_symbols(query):
        if code not in seen:
            seen.append(code)
    return seen


def primary_stock_symbol(query: str) -> str | None:
    symbols = extract_stock_symbols(query)
    return symbols[0] if symbols else None


def infer_query_intent_hint(query: str) -> str | None:
    symbols = extract_stock_symbols(query)
    codes = extract_stock_codes(query)
    lowered = query.lower()
    has_compare_language = any(keyword in query for keyword in _COMPARE_KEYWORDS_CN) or any(
        keyword in lowered for keyword in _COMPARE_KEYWORDS_EN
    )
    has_screen_language = any(keyword in query for keyword in _SCREEN_KEYWORDS_CN) or any(
        keyword in lowered for keyword in _SCREEN_KEYWORDS_EN
    )
    has_expansion_language = any(keyword in query for keyword in _EXPANSION_HINTS_CN) or any(
        keyword in lowered for keyword in _EXPANSION_HINTS_EN
    )

    if len(symbols) >= 2 and codes and has_expansion_language and (has_compare_language or has_screen_language):
        return "stock_compare"
    return None


def stock_name_for_symbol(symbol: str, *, query: str) -> str:
    lowered_query = query.lower()
    for alias, mapped_symbol in STOCK_NAME_ALIASES.items():
        if mapped_symbol == symbol and alias.lower() in lowered_query:
            return alias
    return symbol
