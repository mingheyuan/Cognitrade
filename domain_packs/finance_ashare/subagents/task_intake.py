from __future__ import annotations

import re
from typing import Any


_STOCK_NAME_ALIASES = {
    "\u8d35\u5dde\u8305\u53f0": "600519",
    "\u8305\u53f0": "600519",
    "\u5b81\u5fb7\u65f6\u4ee3": "300750",
    "\u6bd4\u4e9a\u8fea": "002594",
    "\u4eac\u4e1c\u65b9a": "000725",
    "\u4eac\u4e1c\u65b9\uff21": "000725",
    "\u5bd2\u6b66\u7eaa": "688256",
    "\u65b0\u6613\u76db": "300502",
    "\u5e73\u5b89\u94f6\u884c": "000001",
    "\u62db\u5546\u94f6\u884c": "600036",
    "\u5b81\u6ce2\u94f6\u884c": "002142",
    "\u5174\u4e1a\u94f6\u884c": "601166",
    "\u6c5f\u82cf\u94f6\u884c": "600919",
    "\u6210\u90fd\u94f6\u884c": "601838",
}
_SECTOR_ALIASES = {
    "\u534a\u5bfc\u4f53": "\u534a\u5bfc\u4f53",
    "\u4eba\u5f62\u673a\u5668\u4eba": "\u4eba\u5f62\u673a\u5668\u4eba",
    "\u65b0\u80fd\u6e90": "\u65b0\u80fd\u6e90",
    "\u521b\u65b0\u836f": "\u521b\u65b0\u836f",
    "\u94f6\u884c": "\u94f6\u884c",
    "ai": "AI",
    "semiconductor": "semiconductor",
}
_COMPARE_KEYWORDS = ("compare", "comparison", "\u5bf9\u6bd4", "\u6bd4\u8f83", "\u54ea\u4e2a\u597d", "\u6392\u4e00\u4e0b")
_SCREEN_KEYWORDS = ("screen", "watchlist", "candidate", "\u9009\u80a1", "\u7b5b\u9009", "\u63a8\u8350")
_SECTOR_KEYWORDS = ("sector", "industry", "\u677f\u5757", "\u884c\u4e1a")
_REVIEW_KEYWORDS = ("review thesis", "thesis review", "\u590d\u6838", "\u4e0a\u4e00\u8f6e\u5224\u65ad")
_DISTILL_KEYWORDS = ("distill", "kol", "\u84b8\u998f", "\u63d0\u70bc")


def resolve_request(request: dict[str, Any]) -> dict[str, Any]:
    query = str(request.get("user_query") or "").strip()
    explicit_intent = str(request.get("intent") or "").strip() or None
    slots = dict(request.get("slots") or {})
    constraints = dict(request.get("constraints") or {})
    symbols = _symbols(query, slots=slots, constraints=constraints)
    sector = _sector(query, slots=slots, constraints=constraints)
    if symbols:
        slots["symbols"] = symbols
    if sector:
        slots["sector"] = sector

    intent = explicit_intent or _infer_intent(query=query, symbols=symbols, sector=sector)
    if intent is None:
        return _clarify(
            "Please provide an A-share code or company name, a sector, or state whether you want screening, comparison, or thesis review.",
            ["intent_or_target"],
            query=query,
        )
    if intent == "stock_compare" and len(symbols) < 2:
        return _clarify(
            "Please provide at least two A-share codes or company names to compare.",
            ["symbols"],
            query=query,
            intent=intent,
        )
    if intent == "stock_check" and not symbols and not slots.get("snapshots"):
        return _clarify(
            "Please provide an A-share code or supported company name for the analysis.",
            ["symbols"],
            query=query,
            intent=intent,
        )
    if intent == "stock_screen" and not symbols and not sector:
        return _clarify(
            "Please specify a sector or a candidate list for screening so the result has a defined investment universe.",
            ["sector_or_symbols"],
            query=query,
            intent=intent,
        )
    if intent == "sector_research" and not sector:
        return _clarify(
            "Please specify the sector or industry to research.",
            ["sector"],
            query=query,
            intent=intent,
        )
    if intent == "review_thesis" and not constraints.get("review_target_run_id"):
        return _clarify(
            "Please select the prior run whose thesis should be reviewed.",
            ["review_target_run_id"],
            query=query,
            intent=intent,
        )
    return {
        "status": "accepted",
        "intent": intent,
        "slots": slots,
        "constraints": constraints,
        "trace": {
            "source": "finance_ashare.task_intake",
            "symbols": symbols,
            "sector": sector,
            "explicit_intent": bool(explicit_intent),
        },
    }


def _symbols(query: str, *, slots: dict[str, Any], constraints: dict[str, Any]) -> list[str]:
    values: list[object] = [*(slots.get("symbols") or [])]
    if constraints.get("symbol"):
        values.append(constraints["symbol"])
    values.extend(constraints.get("symbols") or [])
    values.extend(re.findall(r"\b(\d{6})\b", query))
    normalized_query = query.casefold()
    values.extend(
        code for alias, code in _STOCK_NAME_ALIASES.items() if alias.casefold() in normalized_query
    )
    result: list[str] = []
    for value in values:
        symbol = str(value or "").strip()
        if symbol and symbol not in result:
            result.append(symbol)
    return result


def _sector(query: str, *, slots: dict[str, Any], constraints: dict[str, Any]) -> str | None:
    supplied = str(slots.get("sector") or constraints.get("sector_query") or "").strip()
    if supplied:
        return supplied
    lowered = query.casefold()
    for alias, sector in _SECTOR_ALIASES.items():
        if alias.casefold() in lowered:
            return sector
    match = re.search(r"([A-Za-z0-9\u4e00-\u9fff]{2,20})(?:\u677f\u5757|\u884c\u4e1a)", query)
    return match.group(1).strip() if match else None


def _infer_intent(*, query: str, symbols: list[str], sector: str | None) -> str | None:
    lowered = query.casefold()
    if _contains(query, lowered, _REVIEW_KEYWORDS):
        return "review_thesis"
    if _contains(query, lowered, _DISTILL_KEYWORDS):
        return "distill_kol_logic"
    if _contains(query, lowered, _COMPARE_KEYWORDS):
        return "stock_compare"
    if _contains(query, lowered, _SCREEN_KEYWORDS):
        return "stock_screen"
    if _contains(query, lowered, _SECTOR_KEYWORDS) or sector:
        return "sector_research"
    return "stock_check" if symbols else None


def _contains(query: str, lowered: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in query or keyword in lowered for keyword in keywords)


def _clarify(question: str, missing_fields: list[str], *, query: str, intent: str | None = None) -> dict[str, Any]:
    return {
        "status": "needs_clarification",
        "intent": intent,
        "clarification": {"question": question, "missing_fields": missing_fields},
        "trace": {"source": "finance_ashare.task_intake", "query": query},
    }
