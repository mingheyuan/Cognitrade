from __future__ import annotations

import re
from typing import Any

from domain_packs.finance_ashare.tools.security_master import AShareSecurityMaster
from domain_packs.finance_ashare.tools.fund_master import FundSecurityMaster

_SECURITY_MASTER = AShareSecurityMaster()
_FUND_MASTER = FundSecurityMaster()
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
_MULTI_TARGET_PATTERN = re.compile(
    r"(?:\d+|[一二两三四五六七八九十]+)\s*(?:家|只|支|个)\s*(?:银行|公司|股票|标的|证券|个股)?"
)
_SCREEN_KEYWORDS = ("screen", "watchlist", "candidate", "\u9009\u80a1", "\u7b5b\u9009", "\u63a8\u8350")
_SECTOR_KEYWORDS = ("sector", "industry", "\u677f\u5757", "\u884c\u4e1a")
_REVIEW_KEYWORDS = ("review thesis", "thesis review", "\u590d\u6838", "\u4e0a\u4e00\u8f6e\u5224\u65ad")
_DISTILL_KEYWORDS = ("distill", "kol", "\u84b8\u998f", "\u63d0\u70bc")
_PREVIOUS_RUN_KEYWORDS = ("previous", "prior", "last run", "latest run", "上一轮", "上次", "最近一次")
_ANALYSIS_KEYWORDS = (
    "analyze",
    "analysis",
    "research",
    "company",
    "stock",
    "share",
    "equity",
    "分析",
    "研究",
    "公司",
    "股票",
    "个股",
)
_FUND_KEYWORDS = (
    "fund",
    "etf",
    "lof",
    "基金",
    "基民",
    "申购",
    "赎回",
    "净值",
    "重仓基金",
    "基金持仓",
)
_PORTFOLIO_KEYWORDS = (
    "portfolio",
    "returns attribution",
    "return attribution",
    "rebalance",
    "组合",
    "收益归因",
    "业绩归因",
    "再平衡",
    "调仓",
    "持仓权重",
)
_FUND_CATEGORY_ALIASES = {
    "股票型": "股票型",
    "混合型": "混合型",
    "债券型": "债券型",
    "指数型": "指数型",
    "qdii": "QDII",
    "fof": "FOF",
    "etf": "ETF",
    "lof": "LOF",
}
_RUN_ID_PATTERN = re.compile(r"(?:run|运行)(?:\s*id)?\s*[#：:=]?\s*([A-Za-z0-9][A-Za-z0-9_-]{5,127})", re.IGNORECASE)
_CODE_PATTERN = re.compile(r"(?<!\d)(\d{6})(?!\d)")


def resolve_request(request: dict[str, Any]) -> dict[str, Any]:
    query = str(request.get("user_query") or "").strip()
    explicit_intent = str(request.get("intent") or "").strip() or None
    slots = dict(request.get("slots") or {})
    constraints = dict(request.get("constraints") or {})
    runtime_context = dict(request.get("_runtime_context") or {})
    portfolio_holdings = [
        dict(item)
        for item in (
            constraints.get("portfolio_holdings")
            or slots.get("portfolio_holdings")
            or []
        )
        if isinstance(item, dict)
    ]
    if portfolio_holdings:
        slots["portfolio_holdings"] = portfolio_holdings
    for field_name in ("benchmark_return", "rebalance_targets", "drift_threshold", "fee_model"):
        if field_name in constraints:
            slots[field_name] = constraints[field_name]
    fund_category = _fund_category(query, slots=slots, constraints=constraints)
    supplied_fund_codes = [
        *_as_list(slots.get("fund_codes")),
        *_as_list(constraints.get("fund_codes")),
    ]
    should_resolve_funds = (
        explicit_intent == "fund_research"
        or _contains(query, query.casefold(), _FUND_KEYWORDS)
        or fund_category is not None
        or any(not _looks_like_equity_code(code) for code in _CODE_PATTERN.findall(query))
        or bool(supplied_fund_codes)
    )
    fund_resolution = _FUND_MASTER.resolve(
        query,
        supplied_codes=supplied_fund_codes,
        allow_network=should_resolve_funds,
    )
    fund_codes = list(fund_resolution.fund_codes)
    if explicit_intent == "fund_research":
        for value in supplied_fund_codes:
            normalized = FundSecurityMaster.normalize_code(value)
            if normalized and normalized not in fund_codes:
                fund_codes.append(normalized)
    if fund_codes:
        slots["fund_codes"] = fund_codes
    if fund_category:
        slots["fund_category"] = fund_category
    sector = _sector(query, slots=slots, constraints=constraints)
    resolution = _symbols(
        query,
        slots=slots,
        constraints=constraints,
        allow_network=not sector and not _looks_like_multi_target_request(query),
    )
    symbols = [symbol for symbol in resolution.symbols if symbol not in fund_codes]
    if symbols:
        slots["symbols"] = symbols
    if sector:
        slots["sector"] = sector

    intent = explicit_intent or _infer_intent(
        query=query,
        symbols=symbols,
        sector=sector,
        fund_codes=fund_codes,
        fund_category=fund_category,
        portfolio_holdings=portfolio_holdings,
    )
    if intent is None:
        return _clarify(
            ["intent_or_target"],
            query=query,
            slots=slots,
            constraints=constraints,
            context={"reason": "intent_or_target_missing"},
        )
    if intent == "stock_compare" and len(symbols) < 2:
        constraints.setdefault("requested_target_count", 2)
        return _clarify(
            ["symbols"],
            query=query,
            intent=intent,
            slots=slots,
            constraints=constraints,
            context={
                "reason": "comparison_targets_missing",
                "target_scope": sector,
            },
        )
    if intent == "fund_research" and not fund_codes and not fund_category:
        return _clarify(
            ["fund_category_or_codes"],
            query=query,
            intent=intent,
            slots=slots,
            constraints=constraints,
            context={
                "reason": "fund_scope_missing",
                "ambiguous_fund_terms": fund_resolution.ambiguous_terms,
                "provider_error": fund_resolution.provider_error,
            },
        )
    if intent == "portfolio_research" and not portfolio_holdings:
        return _clarify(
            ["portfolio_holdings"],
            query=query,
            intent=intent,
            slots=slots,
            constraints=constraints,
            context={"reason": "portfolio_holdings_missing"},
        )
    if intent == "stock_check" and not symbols and not slots.get("snapshots"):
        if _is_contextual_continuation(query, request, runtime_context):
            constraints["continuation"] = True
        else:
            return _clarify(
                ["symbols"],
                query=query,
                intent=intent,
                slots=slots,
                constraints=constraints,
                context={"reason": "analysis_target_missing"},
            )
    if intent == "stock_screen" and not symbols and not sector:
        constraints.setdefault("candidate_pool_size", 8)
        slots["universe_scope"] = "ashare_liquidity_ranked"
    if intent == "sector_research" and not sector:
        return _clarify(
            ["sector"],
            query=query,
            intent=intent,
            slots=slots,
            constraints=constraints,
            context={"reason": "sector_missing"},
        )
    if intent == "review_thesis":
        review_target_run_id = _review_target_run_id(
            query=query,
            slots=slots,
            constraints=constraints,
            runtime_context=runtime_context,
        )
        if not review_target_run_id:
            return _clarify(
                ["review_target_run_id"],
                query=query,
                intent=intent,
                slots=slots,
                constraints=constraints,
                context={"reason": "review_target_missing"},
            )
        constraints["review_target_run_id"] = review_target_run_id
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
            "security_resolution_source": resolution.source,
            "security_matches": resolution.matches,
            "ambiguous_security_terms": resolution.ambiguous_terms,
            "fund_resolution_source": fund_resolution.source,
            "fund_matches": fund_resolution.matches,
            "ambiguous_fund_terms": fund_resolution.ambiguous_terms,
            "fund_provider_error": fund_resolution.provider_error,
            "portfolio_holding_count": len(portfolio_holdings),
        },
    }


def _symbols(
    query: str,
    *,
    slots: dict[str, Any],
    constraints: dict[str, Any],
    allow_network: bool = True,
):
    values: list[object] = [*(slots.get("symbols") or [])]
    if constraints.get("symbol"):
        values.append(constraints["symbol"])
    values.extend(constraints.get("symbols") or [])
    supplied_text = " ".join(str(value or "") for value in values)
    return _SECURITY_MASTER.resolve(
        f"{query} {supplied_text}".strip(),
        supplied_symbols=values,
        allow_network=allow_network,
    )


def _sector(query: str, *, slots: dict[str, Any], constraints: dict[str, Any]) -> str | None:
    supplied = str(slots.get("sector") or constraints.get("sector_query") or "").strip()
    if supplied:
        return supplied
    lowered = query.casefold()
    for alias, sector in _SECTOR_ALIASES.items():
        if _contains_sector_alias(query, lowered, alias):
            return sector
    match = re.search(r"([A-Za-z0-9\u4e00-\u9fff]{2,20})(?:\u677f\u5757|\u884c\u4e1a)", query)
    if not match:
        return None
    candidate = match.group(1).strip()
    candidate = re.sub(r"^(?:请|帮我|分析|研究|梳理|看看|复盘|关于|筛选)", "", candidate)
    return candidate or None


def _contains_sector_alias(query: str, lowered: str, alias: str) -> bool:
    """Match English sector aliases as words, not arbitrary substrings."""

    normalized_alias = alias.casefold()
    if normalized_alias.isascii():
        return re.search(
            rf"(?<![a-z0-9]){re.escape(normalized_alias)}(?![a-z0-9])",
            lowered,
        ) is not None
    return normalized_alias in query.casefold()


def _review_target_run_id(
    *,
    query: str,
    slots: dict[str, Any],
    constraints: dict[str, Any],
    runtime_context: dict[str, Any],
) -> str | None:
    supplied = str(
        constraints.get("review_target_run_id")
        or slots.get("review_target_run_id")
        or ""
    ).strip()
    if not supplied:
        match = _RUN_ID_PATTERN.search(query)
        supplied = match.group(1) if match else ""
    if supplied:
        return supplied
    lowered = query.casefold()
    if not any(keyword in query or keyword in lowered for keyword in _PREVIOUS_RUN_KEYWORDS):
        return None
    recent_runs = [row for row in runtime_context.get("recent_runs") or [] if isinstance(row, dict)]
    eligible = [
        row
        for row in recent_runs
        if str(row.get("run_id") or "").strip()
        and str(row.get("status") or "") in {"completed", "completed_partial"}
        and str(row.get("intent") or "") != "review_thesis"
    ]
    return str(eligible[0].get("run_id")) if eligible else None


def _infer_intent(
    *,
    query: str,
    symbols: list[str],
    sector: str | None,
    fund_codes: list[str],
    fund_category: str | None,
    portfolio_holdings: list[dict[str, Any]],
) -> str | None:
    lowered = query.casefold()
    if portfolio_holdings or _contains(query, lowered, _PORTFOLIO_KEYWORDS):
        return "portfolio_research"
    if fund_codes or fund_category or _contains(query, lowered, _FUND_KEYWORDS):
        return "fund_research"
    if _contains(query, lowered, _REVIEW_KEYWORDS):
        return "review_thesis"
    if _contains(query, lowered, _DISTILL_KEYWORDS):
        return "distill_kol_logic"
    if _contains(query, lowered, _COMPARE_KEYWORDS):
        return "stock_compare"
    if _contains(query, lowered, _SCREEN_KEYWORDS):
        return "stock_screen"
    if _looks_like_multi_target_request(query):
        return "stock_compare"
    if _contains(query, lowered, _SECTOR_KEYWORDS) or sector:
        return "sector_research"
    if _contains(query, lowered, _ANALYSIS_KEYWORDS):
        return "stock_check"
    return "stock_check" if symbols else None


def _fund_category(
    query: str,
    *,
    slots: dict[str, Any],
    constraints: dict[str, Any],
) -> str | None:
    supplied = str(slots.get("fund_category") or constraints.get("fund_category") or "").strip()
    if supplied:
        lowered = supplied.casefold()
        return next(
            (
                category
                for alias, category in _FUND_CATEGORY_ALIASES.items()
                if alias.casefold() in lowered
            ),
            supplied,
        )
    lowered = query.casefold()
    return next(
        (
            category
            for alias, category in _FUND_CATEGORY_ALIASES.items()
            if alias.casefold() in lowered
        ),
        None,
    )


def _looks_like_equity_code(value: object) -> bool:
    code = str(value or "").strip()
    return len(code) == 6 and code.startswith(
        ("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "606", "688", "689", "4", "8", "92")
    )


def _as_list(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _looks_like_multi_target_request(query: str) -> bool:
    """Treat an explicit quantity of targets as comparison intent, even without 'compare'."""

    return _MULTI_TARGET_PATTERN.search(query) is not None


def _is_contextual_continuation(
    query: str,
    request: dict[str, Any],
    runtime_context: dict[str, Any],
) -> bool:
    context = list(request.get("conversation_context") or [])
    if not context:
        context = list(runtime_context.get("conversation_context") or [])
    if not context:
        return False
    lowered = query.casefold()
    markers = (
        "continue",
        "previous",
        "prior",
        "last conclusion",
        "继续",
        "上一轮",
        "上次",
        "前面的结论",
    )
    return any(marker in lowered or marker in query for marker in markers)


def _contains(query: str, lowered: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in query or keyword in lowered for keyword in keywords)


def _clarify(
    missing_fields: list[str],
    *,
    query: str,
    intent: str | None = None,
    slots: dict[str, Any] | None = None,
    constraints: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "needs_clarification",
        "intent": intent,
        "slots": dict(slots or {}),
        "constraints": dict(constraints or {}),
        "clarification": {
            "missing_fields": list(missing_fields),
            "context": {
                "query": query,
                "intent": intent,
                **dict(context or {}),
            },
        },
        "trace": {"source": "finance_ashare.task_intake", "query": query},
    }
