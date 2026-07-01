from __future__ import annotations

import re


STOCK_NAME_ALIASES: dict[str, str] = {
    "贵州茅台": "600519",
    "茅台": "600519",
    "宁德时代": "300750",
    "比亚迪": "002594",
    "京东方a": "000725",
    "京东方Ａ": "000725",
    "京东方A": "000725",
    "寒武纪": "688256",
    "新易盛": "300502",
    "平安银行": "000001",
    "招商银行": "600036",
    "宁波银行": "002142",
    "兴业银行": "601166",
    "江苏银行": "600919",
    "成都银行": "601838",
}

_COMPARE_KEYWORDS_CN = (
    "对比",
    "比较",
    "哪个好",
    "谁更值得",
    "谁更强",
    "哪个更好",
    "怎么排",
    "排一下",
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
    "推荐",
    "值得",
    "关注",
    "选股",
    "什么股票",
    "哪些股票",
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
    "扩到",
    "扩展到",
    "扩一下",
    "同业",
    "银行股",
    "券商股",
    "白酒股",
    "新能源",
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
