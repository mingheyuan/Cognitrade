"""Finance Pack-owned implementations of the UZI research semantics.

The source project is used as a methodology reference only.  This module has
no import or runtime dependency on the temporary UZI checkout or its CLI.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from domain_packs.finance_ashare.subagents.uzi_panel_registry import (
    UZI_PANEL_SOURCE_COMMIT,
    UZI_PANEL_VERSION,
    panel_for_profile,
)
from domain_packs.finance_ashare.subagents.uzi_panel_rules import evaluate_investor


UZI_METHOD_VERSION = "3.9.1-adapted"
UZI_SOURCE_COMMIT = "775c76101e8890492d21c2e2827692456fcddb58"

_PROFILES: dict[str, dict[str, Any]] = {
    "quick": {
        "depth": "quick",
        "max_duration_seconds": 90,
        # Cognitrade-only quick policy; it is intentionally not UZI Lite.
        "investors_count": 5,
        "methods": ["dcf"],
        "snapshot_types": ["market", "fundamental", "notice"],
        "allowed_capability_tags": ["profile:quick"],
    },
    "lite": {
        "depth": "lite",
        "max_duration_seconds": 300,
        "investors_count": 10,
        "methods": ["dcf", "panel", "risk"],
        "snapshot_types": [
            "market",
            "fundamental",
            "notice",
            "capital_flow",
            "sentiment",
            "events",
            "lhb",
        ],
        "allowed_capability_tags": ["profile:lite", "profile:quick"],
    },
    "medium": {
        "depth": "medium",
        "max_duration_seconds": 480,
        "investors_count": 51,
        "methods": ["dcf", "comps", "three_statement", "quick_lbo", "accretion_dilution", "panel", "risk", "workflow"],
        "snapshot_types": [
            "market",
            "fundamental",
            "notice",
            "capital_flow",
            "sentiment",
            "events",
            "lhb",
            "sector",
            "policy",
            "kline",
            "macro",
            "peers",
            "chain",
            "research",
            "industry",
            "materials",
            "futures",
            "valuation",
            "governance",
            "moat",
            "trap",
            "contests",
        ],
        "allowed_capability_tags": [
            "profile:quick",
            "profile:lite",
            "profile:medium",
        ],
    },
    "deep": {
        "depth": "deep",
        "max_duration_seconds": 1200,
        "investors_count": 51,
        "methods": [
            "dcf",
            "comps",
            "three_statement",
            "quick_lbo",
            "accretion_dilution",
            "panel",
            "risk",
            "segmental",
            "workflow",
            "deep_analysis",
        ],
        "snapshot_types": [
            "market",
            "fundamental",
            "notice",
            "capital_flow",
            "sentiment",
            "events",
            "lhb",
            "sector",
            "policy",
            "kline",
            "macro",
            "peers",
            "chain",
            "research",
            "industry",
            "materials",
            "futures",
            "valuation",
            "governance",
            "moat",
            "trap",
            "contests",
        ],
        "allowed_capability_tags": [
            "profile:quick",
            "profile:lite",
            "profile:medium",
            "profile:deep",
        ],
    },
}

_PANEL_PERSPECTIVES = (
    ("value", "价值视角", "关注价格、安全边际和可验证现金流", ("pe", "pb", "fcf")),
    ("quality", "质量视角", "关注盈利质量、现金流和负债约束", ("roe", "fcf", "debt_ratio")),
    ("growth", "成长视角", "关注收入增长和利润扩张证据", ("revenue_growth", "profit_growth", "net_margin")),
    ("trend", "趋势视角", "关注价格动量和市场确认", ("pct_change", "amount", "price")),
    ("macro", "宏观视角", "关注周期暴露和估值承受力", ("pe", "revenue_growth", "debt_ratio")),
    ("risk", "风险视角", "优先识别数据缺口和下行约束", ("debt_ratio", "fcf", "notice_count")),
    ("china_value", "中国价投视角", "结合本土行业和治理证据审视长期质量", ("roe", "net_margin", "governance")),
    ("quant", "量化视角", "关注可重复的指标组合而非单点叙事", ("pe", "pct_change", "amount")),
    ("industry", "产业链视角", "关注行业位置和经营数据的相互验证", ("revenue_growth", "margin", "sector")),
    ("contrarian", "逆向视角", "寻找市场价格与基本面之间的可解释偏差", ("pe", "pb", "risk")),
)


def select_profile(inputs: dict[str, Any]) -> dict[str, Any]:
    requested = str(
        inputs.get("research_profile")
        or inputs.get("depth")
        or inputs.get("profile")
        or "lite"
    ).strip().lower()
    selected = requested if requested in _PROFILES else "lite"
    profile = dict(_PROFILES[selected])
    profile["requested"] = requested
    profile["selected"] = selected
    profile["selection_reason"] = (
        "用户显式指定研究深度"
        if requested in _PROFILES
        else "未指定或档位无效，使用 Finance Pack 默认 Lite"
    )
    profile["source_commit"] = UZI_SOURCE_COMMIT
    return profile


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _first_number(mapping: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _number(mapping.get(key))
        if value is not None:
            return value
    return None


def _metric_number(mapping: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    """Read both flat financial fields and the nested metric payloads."""

    for key in keys:
        value = mapping.get(key)
        if isinstance(value, dict):
            for nested_key in ("value", "single", "latest", "current"):
                number = _number(value.get(nested_key))
                if number is not None:
                    return number
        number = _number(value)
        if number is not None:
            return number
    return None


def _first_metric_number(
    sections: list[dict[str, Any]],
    keys: tuple[str, ...],
) -> tuple[float | None, str | None]:
    for index, section in enumerate(sections):
        value = _metric_number(section, keys)
        if value is not None:
            return value, str(index)
    return None, None


def _amount_yi(value: Any) -> float | None:
    """Normalize financial amounts to the Pack's 亿元 unit when needed."""

    number = _number(value)
    if number is None:
        return None
    return number / 100_000_000 if abs(number) >= 1_000_000 else number


def _first_amount_yi(
    sections: list[dict[str, Any]],
    keys: tuple[str, ...],
) -> tuple[float | None, str | None]:
    for index, section in enumerate(sections):
        value = _metric_number(section, keys)
        if value is not None:
            return _amount_yi(value), str(index)
    return None, None


def _facts(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    value = snapshot.get("facts")
    return value if isinstance(value, dict) else {}


def _section(facts: dict[str, Any], *names: str) -> dict[str, Any]:
    for name in names:
        value = facts.get(name)
        if isinstance(value, dict):
            return value
    return {}


def _snapshot_for_symbol(snapshots: list[dict[str, Any]], symbol: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in snapshots:
        if not isinstance(row, dict) or str(row.get("symbol") or "") != symbol:
            continue
        snapshot_type = str(row.get("snapshot_type") or "unknown")
        result[snapshot_type] = row
    return result


def build_features(snapshot_rows: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
    grouped = _snapshot_for_symbol(snapshot_rows, symbol)
    market = _section(_facts(grouped.get("market")), "market_overview", "spot")
    fundamental_facts = _facts(grouped.get("fundamental"))
    financial = _section(fundamental_facts, "financial_abstract", "financials")
    analysis = _section(fundamental_facts, "financial_analysis", "financial_indicator")
    info = _section(fundamental_facts, "info", "company_info")
    banking = _section(fundamental_facts, "banking_metrics")
    valuation_facts = _facts(grouped.get("valuation"))
    metric_sections = [
        ("financial_abstract", financial),
        ("financial_analysis", analysis),
        ("info", info),
        ("banking_metrics", banking),
        ("financial_abstract_new", fundamental_facts.get("financial_abstract_new") or {}),
        ("financial_benefit_new", fundamental_facts.get("financial_benefit_new") or {}),
        ("financial_debt_new", fundamental_facts.get("financial_debt_new") or {}),
        ("financial_cash_new", fundamental_facts.get("financial_cash_new") or {}),
        ("valuation", valuation_facts),
    ]

    def first_number_with_source(keys: tuple[str, ...]) -> tuple[float | None, str | None]:
        for section_name, section in metric_sections:
            if not isinstance(section, dict):
                continue
            value = _metric_number(section, keys)
            if value is not None:
                return value, section_name
        return None, None

    def first_amount_with_source(keys: tuple[str, ...]) -> tuple[float | None, str | None]:
        value, source = first_number_with_source(keys)
        return _amount_yi(value), source
    notices = _facts(grouped.get("notice"))
    capital = _facts(grouped.get("capital_flow"))
    sentiment = _facts(grouped.get("sentiment"))
    price = _first_number(market, ("latest_price", "price", "spot", "收盘", "close"))
    market_cap = _amount_yi(_first_number(market, ("market_cap", "market_cap_yi", "总市值")))
    shares = _first_number(market, ("shares_outstanding_yi", "shares", "total_shares", "总股本"))
    if shares is not None and abs(shares) >= 1_000_000:
        shares /= 100_000_000
    revenue, revenue_source = first_amount_with_source(
        ("revenue", "revenue_latest_yi", "营业收入", "营业总收入", "主营业务收入", "operating_revenue")
    )
    net_profit, net_profit_source = first_amount_with_source(
        ("net_profit", "net_profit_latest_yi", "归母净利润", "净利润", "net_income")
    )
    fcf, fcf_source = first_amount_with_source(
        (
            "fcf_latest_yi",
            "free_cash_flow",
            "operating_cash_flow",
            "经营活动现金流净额",
            "经营活动产生的现金流量净额",
        )
    )
    total_assets, total_assets_source = first_amount_with_source(
        ("total_assets_yi", "total_assets", "资产总计", "资产总额")
    )
    total_liabilities, total_liabilities_source = first_amount_with_source(
        ("total_liabilities_yi", "total_liabilities", "负债合计", "负债总额")
    )
    cash, cash_source = first_amount_with_source(
        ("cash_yi", "cash", "货币资金", "现金及现金等价物", "cash_and_equivalents")
    )
    direct_debt, debt_source = first_amount_with_source(
        ("total_debt_yi", "total_debt", "有息负债", "债务合计")
    )
    short_debt, short_debt_source = first_amount_with_source(
        ("short_term_debt", "short_term_borrowings", "短期借款")
    )
    long_debt, long_debt_source = first_amount_with_source(
        ("long_term_debt", "long_term_borrowings", "长期借款")
    )
    debt_derivation = None
    if direct_debt is not None:
        total_debt = direct_debt
    elif short_debt is not None or long_debt is not None:
        total_debt = (short_debt or 0.0) + (long_debt or 0.0)
        debt_source = "+".join(item for item in (short_debt_source, long_debt_source) if item)
        debt_derivation = "short_term_debt + long_term_debt"
    else:
        total_debt = None
    ebitda, ebitda_source = first_amount_with_source(
        ("ebitda_yi", "ebitda", "EBITDA", "息税折旧摊销前利润")
    )
    eps, eps_source = first_number_with_source(("eps", "EPS", "每股收益", "基本每股收益"))
    if shares is None and market_cap is not None and price and price > 0:
        shares = market_cap / price
    net_margin = _first_number(financial, ("net_margin", "净利率"))
    if net_margin is None:
        net_margin, _ = first_number_with_source(("net_margin", "净利率", "销售净利率", "销售净利率(%)"))
    if net_margin is None and revenue and revenue != 0 and net_profit is not None:
        net_margin = net_profit / revenue * 100
    elif net_margin is not None and abs(net_margin) <= 1:
        net_margin *= 100
    pe = _first_number(market, ("pe_ttm", "pe", "市盈率"))
    pb = _first_number(market, ("pb", "市净率"))
    roe = _first_number(banking, ("roe",)) or _first_number(financial, ("roe", "ROE"))
    if roe is None:
        roe, _ = first_number_with_source(
            ("roe", "ROE", "净资产收益率", "净资产收益率(%)", "加权净资产收益率(%)")
        )
    debt_ratio = _first_number(
        financial,
        ("debt_ratio", "asset_liability_ratio", "debt_to_assets", "资产负债率"),
    )
    if debt_ratio is None:
        debt_ratio, _ = first_number_with_source(
            ("debt_ratio", "asset_liability_ratio", "资产负债率", "资产负债率(%)")
        )
    if roe is not None and abs(roe) <= 1:
        roe *= 100
    if debt_ratio is not None and abs(debt_ratio) <= 1:
        debt_ratio *= 100
    pct_change = _first_number(market, ("pct_change", "change_pct", "涨跌幅"))
    revenue_growth = _first_number(financial, ("revenue_growth", "revenue_yoy", "营收同比"))
    if revenue_growth is None:
        revenue_growth, _ = first_number_with_source(
            ("revenue_growth", "revenue_yoy", "营收同比", "营业总收入同比增长率", "主营业务收入增长率(%)")
        )
    profit_growth = _first_number(financial, ("profit_growth", "net_profit_yoy", "净利润同比"))
    if profit_growth is None:
        profit_growth, _ = first_number_with_source(
            ("profit_growth", "net_profit_yoy", "净利润同比", "净利润同比增长率", "净利润增长率(%)")
        )
    amount = _first_number(market, ("amount", "成交额"))
    if revenue_growth is not None and abs(revenue_growth) <= 1:
        revenue_growth *= 100
    if profit_growth is not None and abs(profit_growth) <= 1:
        profit_growth *= 100
    segments = financial.get("segments") or financial.get("segment_revenue")
    if not isinstance(segments, list):
        segments = None
    values = {
        "price": price,
        "market_cap_yi": market_cap,
        "shares_outstanding_yi": shares,
        "revenue_latest_yi": revenue,
        "net_profit_latest_yi": net_profit,
        "fcf_latest_yi": fcf,
        "total_assets_yi": total_assets,
        "total_liabilities_yi": total_liabilities,
        "cash_yi": cash,
        "total_debt_yi": total_debt,
        "ebitda_yi": ebitda,
        "eps": eps,
        "net_margin": net_margin,
        "pe": pe,
        "pb": pb,
        "roe": roe,
        "debt_ratio": debt_ratio,
        "revenue_growth": revenue_growth,
        "profit_growth": profit_growth,
    }
    field_sources = {
        key: source
        for key, source in {
            "revenue_latest_yi": revenue_source,
            "net_profit_latest_yi": net_profit_source,
            "fcf_latest_yi": fcf_source,
            "total_assets_yi": total_assets_source,
            "total_liabilities_yi": total_liabilities_source,
            "cash_yi": cash_source,
            "total_debt_yi": debt_source,
            "ebitda_yi": ebitda_source,
            "eps": eps_source,
        }.items()
        if source
    }
    assumptions = [debt_derivation] if debt_derivation else []
    return {
        "symbol": symbol,
        **values,
        "pct_change": pct_change,
        "amount": amount,
        "notice_count": _first_number(notices, ("notice_count", "disclosures_count")),
        "capital_flow": capital,
        "sentiment": sentiment,
        "segments": segments,
        "field_sources": field_sources,
        "assumptions": assumptions,
        "data_gap": [key for key, value in values.items() if value is None],
        "sector": str(
            _facts(grouped.get("sector")).get("sector")
            or _facts(grouped.get("sector")).get("name")
            or ""
        ),
        "available_fields": [key for key, value in values.items() if value is not None],
    }


def _dcf_value(features: dict[str, Any]) -> dict[str, Any]:
    price = float(features.get("price") or 0.0)
    revenue = float(features.get("revenue_latest_yi") or 0.0)
    margin = float(features.get("net_margin") or 0.0) / 100.0
    fcf = float(features.get("fcf_latest_yi") or 0.0)
    warnings: list[str] = []
    data_gap: list[str] = []
    assumption_flags: list[str] = []
    if fcf <= 0 and revenue > 0 and margin > 0:
        fcf = revenue * margin * 0.8
        warnings.append("fcf_approximated_from_revenue_margin")
        assumption_flags.append("fcf_approximated_from_revenue_margin")
    if fcf <= 0:
        market_cap = float(features.get("market_cap_yi") or 0.0)
        fcf = market_cap * 0.05
        warnings.append("fcf_proxy_from_market_cap")
        assumption_flags.append("fcf_proxy_from_market_cap")
    if fcf <= 0:
        warnings.append("fcf_missing")
        data_gap.append("fcf_missing")
    # UZI A-share default: CAPM cost of equity plus after-tax debt cost.
    risk_free_rate = 0.025
    equity_risk_premium = 0.06
    beta = 1.0
    pretax_cost_of_debt = 0.045
    tax_rate = 0.25
    target_debt_ratio = 0.30
    cost_of_equity = risk_free_rate + beta * equity_risk_premium
    after_tax_cost_of_debt = pretax_cost_of_debt * (1 - tax_rate)
    wacc = (1 - target_debt_ratio) * cost_of_equity + target_debt_ratio * after_tax_cost_of_debt
    terminal_growth = 0.025
    stage_one_growth = 0.10
    stage_two_growth = 0.05
    projected: list[float] = []
    current = fcf
    for _ in range(5):
        current *= 1 + stage_one_growth
        projected.append(current)
    for _ in range(5):
        current *= 1 + stage_two_growth
        projected.append(current)
    pv_explicit = sum(value / (1 + wacc) ** (index + 1) for index, value in enumerate(projected))
    terminal = projected[-1] * (1 + terminal_growth) / (wacc - terminal_growth) if projected else 0.0
    enterprise_value = pv_explicit + terminal / (1 + wacc) ** len(projected) if projected else 0.0
    total_debt_value = features.get("total_debt_yi")
    cash_value = features.get("cash_yi")
    total_debt = float(total_debt_value or 0.0)
    cash = float(cash_value or 0.0)
    equity_value = enterprise_value - total_debt + cash
    shares = float(features.get("shares_outstanding_yi") or 0.0)
    if shares <= 0 and price > 0 and features.get("market_cap_yi"):
        shares = float(features["market_cap_yi"]) / price
    fair_value = equity_value / shares if shares > 0 else 0.0
    margin_of_safety = (fair_value - price) / price if price > 0 and fair_value > 0 else None
    if features.get("debt_ratio") is None:
        warnings.append("leverage_assumed")
        assumption_flags.append("leverage_assumed")
    if total_debt_value is None:
        warnings.append("total_debt_missing")
        assumption_flags.append("total_debt_assumed_zero")
    if cash_value is None:
        warnings.append("cash_missing")
        assumption_flags.append("cash_assumed_zero")
    if features.get("total_assets_yi") is None:
        warnings.append("total_assets_missing")
    if features.get("total_liabilities_yi") is None:
        warnings.append("total_liabilities_missing")
    if features.get("revenue_latest_yi") is None:
        warnings.append("revenue_missing")
        data_gap.append("revenue_missing")
    if price <= 0:
        data_gap.append("market_price_missing")
    if shares <= 0:
        data_gap.append("shares_missing")
    status = "data_gap" if data_gap else ("assumption_only" if assumption_flags else "available")
    confidence = max(0.15, min(0.9, 0.8 - 0.06 * len(warnings) - 0.04 * len(assumption_flags)))
    sensitivity: list[dict[str, Any]] = []
    for sensitivity_wacc in (wacc - 0.02, wacc - 0.01, wacc, wacc + 0.01, wacc + 0.02):
        for sensitivity_growth in (
            terminal_growth - 0.01,
            terminal_growth - 0.005,
            terminal_growth,
            terminal_growth + 0.005,
            terminal_growth + 0.01,
        ):
            projected_sensitivity: list[float] = []
            current_sensitivity = fcf
            for _ in range(5):
                current_sensitivity *= 1 + stage_one_growth
                projected_sensitivity.append(current_sensitivity)
            for _ in range(5):
                current_sensitivity *= 1 + stage_two_growth
                projected_sensitivity.append(current_sensitivity)
            explicit_sensitivity = sum(
                value / (1 + sensitivity_wacc) ** (index + 1)
                for index, value in enumerate(projected_sensitivity)
            )
            terminal_sensitivity = (
                projected_sensitivity[-1] * (1 + sensitivity_growth)
                / (sensitivity_wacc - sensitivity_growth)
                if sensitivity_wacc > sensitivity_growth
                else 0.0
            )
            equity_sensitivity = (
                explicit_sensitivity
                + terminal_sensitivity / (1 + sensitivity_wacc) ** len(projected_sensitivity)
                - total_debt
                + cash
            )
            sensitivity.append(
                {
                    "wacc": round(sensitivity_wacc, 4),
                    "terminal_growth": round(sensitivity_growth, 4),
                    "fair_value": round(equity_sensitivity / shares, 2) if shares > 0 else None,
                }
            )
    if margin_of_safety is None or data_gap:
        verdict = "data_gap"
    elif margin_of_safety >= 0.15:
        verdict = "below_proxy_value"
    elif margin_of_safety <= -0.15:
        verdict = "above_proxy_value"
    else:
        verdict = "near_proxy_value"
    return {
        "model": "dcf",
        "methodology_version": UZI_METHOD_VERSION,
        "symbol": features["symbol"],
        "status": status,
        "fair_value": round(fair_value, 2) if fair_value else None,
        "market_price": price or None,
        "margin_of_safety": round(margin_of_safety, 4) if margin_of_safety is not None else None,
        "assumptions": {
            "wacc": wacc,
            "terminal_growth": terminal_growth,
            "stage_one_growth": stage_one_growth,
            "stage_two_growth": stage_two_growth,
            "forecast_years": 10,
            "risk_free_rate": risk_free_rate,
            "equity_risk_premium": equity_risk_premium,
            "beta": beta,
            "tax_rate": tax_rate,
            "target_debt_ratio": target_debt_ratio,
            "cost_of_equity": cost_of_equity,
            "after_tax_cost_of_debt": after_tax_cost_of_debt,
            "total_debt_yi": total_debt_value,
            "cash_yi": cash_value,
        },
        "sensitivity": sensitivity,
        "verdict": verdict,
        "confidence": round(confidence, 4),
        "warnings": list(dict.fromkeys(warnings)),
        "data_gap": list(dict.fromkeys(data_gap)),
        "assumption_flags": list(dict.fromkeys(assumption_flags)),
        "field_sources": dict(features.get("field_sources") or {}),
        "methodology_log": [
            f"基期 FCF={round(fcf, 3)} 亿",
            f"CAPM 股权成本={cost_of_equity:.2%}，税后债务成本={after_tax_cost_of_debt:.2%}，WACC={wacc:.2%}",
            f"显式期 PV={round(pv_explicit, 3)} 亿，终值占比按两阶段现金流折现",
            f"每股代理价值={round(fair_value, 2) if fair_value else '—'}，当前价={price or '—'}",
        ],
    }


def _dimension(
    dimension_id: str,
    label: str,
    score: float | None,
    weight: int,
    evidence: list[str],
    data_gap: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "dimension_id": dimension_id,
        "label": label,
        "score": round(score, 2) if score is not None else None,
        "weight": weight,
        "status": "available" if score is not None else "data_gap",
        "evidence": evidence,
        "data_gap": list(data_gap or []),
    }


def build_dimension_scores(
    snapshot_rows: list[dict[str, Any]],
    symbol: str,
    dcf_model: dict[str, Any],
    peer_count: int,
) -> list[dict[str, Any]]:
    features = build_features(snapshot_rows, symbol)
    financial_values = [
        value for value in (
            features.get("roe"),
            features.get("net_margin"),
            features.get("fcf_latest_yi"),
            features.get("revenue_growth"),
        ) if value is not None
    ]
    financial_score = None
    if financial_values:
        financial_score = 5.0
        if (features.get("roe") or 0) >= 10:
            financial_score += 1.5
        if (features.get("net_margin") or 0) >= 10:
            financial_score += 1.0
        if (features.get("fcf_latest_yi") or 0) > 0:
            financial_score += 1.0
        if (features.get("debt_ratio") or 0) > 70:
            financial_score -= 1.5
        financial_score = max(1.0, min(10.0, financial_score))
    pct_change = features.get("pct_change")
    kline_score = None if pct_change is None else max(1.0, min(10.0, 5.0 + float(pct_change) / 5.0))
    margin = dcf_model.get("margin_of_safety")
    valuation_score = None if margin is None else max(1.0, min(10.0, 5.0 + float(margin) * 10.0))
    notice_count = features.get("notice_count")
    event_score = None if notice_count is None else max(1.0, min(10.0, 5.0 + float(notice_count) / 2.0))
    grouped = _snapshot_for_symbol(snapshot_rows, symbol)
    governance_available = bool(grouped.get("governance"))
    lhb_available = bool(grouped.get("lhb"))
    return [
        _dimension("0_basic", "标的与行情", 7.0 if features.get("price") is not None else None, 5,
                   ["当前价格已进入研究输入"] if features.get("price") is not None else [],
                   [] if features.get("price") is not None else ["market_price_missing"]),
        _dimension("1_financials", "财报质量", financial_score, 5,
                   [label for label, present in (
                       ("ROE", features.get("roe") is not None),
                       ("净利率", features.get("net_margin") is not None),
                       ("现金流", features.get("fcf_latest_yi") is not None),
                       ("营收增速", features.get("revenue_growth") is not None),
                   ) if present],
                   [] if financial_score is not None else ["financial_metrics_missing"]),
        _dimension("2_kline", "价格趋势", kline_score, 4,
                   ["当前涨跌幅"] if kline_score is not None else [],
                   [] if kline_score is not None else ["price_series_missing"]),
        _dimension("4_peers", "同行对比", 6.0 if peer_count > 0 else None, 4,
                   [f"可比较同行 {peer_count} 家"] if peer_count > 0 else [],
                   [] if peer_count > 0 else ["peer_group_missing"]),
        _dimension("10_valuation", "估值", valuation_score, 5,
                   ["DCF 代理价值与当前价格可比"] if valuation_score is not None else [],
                   [] if valuation_score is not None else ["valuation_inputs_missing"]),
        _dimension("11_governance", "治理与披露", 7.0 if governance_available else None, 4,
                   ["治理快照已提供"] if governance_available else [],
                   [] if governance_available else ["governance_snapshot_missing"]),
        _dimension("15_events", "事件与公告", event_score, 4,
                   [f"已读取 {int(notice_count)} 条披露计数"] if notice_count is not None else [],
                   [] if event_score is not None else ["events_snapshot_missing"]),
        _dimension("16_lhb", "龙虎榜与资金", 6.0 if lhb_available else None, 4,
                   ["龙虎榜快照已提供"] if lhb_available else [],
                   [] if lhb_available else ["lhb_snapshot_missing"]),
    ]


def _build_comps(features: dict[str, Any], peers: list[dict[str, Any]]) -> dict[str, Any]:
    valid_peers = [peer for peer in peers if peer.get("pe") is not None or peer.get("pb") is not None]
    if not valid_peers:
        return {
            "method": "comps",
            "status": "data_gap",
            "relative_view": "insufficient_peer_signal",
            "peer_count": 0,
            "warnings": ["peer_group_missing"],
            "methodology_log": ["没有足够的同行估值输入，未生成相对估值结论。"],
        }
    pe_values = sorted(float(peer["pe"]) for peer in valid_peers if peer.get("pe") is not None and float(peer["pe"]) > 0)
    pb_values = sorted(float(peer["pb"]) for peer in valid_peers if peer.get("pb") is not None and float(peer["pb"]) > 0)
    median_pe = pe_values[len(pe_values) // 2] if pe_values else None
    median_pb = pb_values[len(pb_values) // 2] if pb_values else None
    gaps = [
        (float(features[key]) - median) / median
        for key, median in (("pe", median_pe), ("pb", median_pb))
        if median and features.get(key) is not None
    ]
    relative_view = (
        "below_peer_median" if gaps and sum(gaps) / len(gaps) < -0.1
        else "above_peer_median" if gaps and sum(gaps) / len(gaps) > 0.1
        else "near_peer_median"
    )
    return {
        "method": "comps",
        "status": "available",
        "relative_view": relative_view,
        "peer_count": len(valid_peers),
        "peer_medians": {"pe": median_pe, "pb": median_pb},
        "target": {"pe": features.get("pe"), "pb": features.get("pb")},
        "score_gap_vs_peer_median": round(sum(gaps) / len(gaps), 4) if gaps else None,
        "confidence": round(min(0.85, 0.4 + len(valid_peers) * 0.1), 4),
        "warnings": [] if gaps else ["target_multiple_missing"],
        "methodology_log": [f"同行池 {len(valid_peers)} 家", f"PE 中位数 {median_pe or '—'}，PB 中位数 {median_pb or '—'}"],
    }


def build_valuation_model(snapshot_rows: list[dict[str, Any]], symbols: list[str], profile: dict[str, Any]) -> dict[str, Any]:
    features_by_symbol = {symbol: build_features(snapshot_rows, symbol) for symbol in symbols}
    models: list[dict[str, Any]] = []
    dimensions: dict[str, list[dict[str, Any]]] = {}
    for symbol, features in features_by_symbol.items():
        model = _dcf_value(features)
        peers = [features_by_symbol[peer] for peer in symbols if peer != symbol]
        model["comps"] = _build_comps(features, peers) if "comps" in profile.get("methods", []) else None
        models.append(model)
        dimensions[symbol] = build_dimension_scores(snapshot_rows, symbol, model, len(peers))
    return {
        "methodology_version": UZI_METHOD_VERSION,
        "profile": profile["selected"],
        "models": models,
        "dimensions": dimensions,
        "primary_symbol": symbols[0] if symbols else None,
        "methodologies": list(profile.get("methods") or ["dcf"]),
    }


def _signal(score: float) -> str:
    if score >= 65:
        return "bullish"
    if score < 35:
        return "bearish"
    return "neutral"


def _panel_vote(perspective: tuple[str, str, str, tuple[str, ...]], features: dict[str, Any]) -> dict[str, Any]:
    perspective_id, label, purpose, _ = perspective
    checks: list[tuple[str, bool | None]] = [
        ("估值或盈利证据", features.get("pe") is not None or features.get("pb") is not None),
        ("现金流证据", features.get("fcf_latest_yi") is not None),
        ("经营质量证据", features.get("roe") is not None or features.get("net_margin") is not None),
        ("价格与流动性证据", features.get("price") is not None and features.get("amount") is not None),
    ]
    if perspective_id == "growth":
        checks[0] = ("增长证据", features.get("revenue_growth") is not None or features.get("profit_growth") is not None)
    if perspective_id == "trend":
        checks[0] = ("趋势证据", features.get("pct_change") is not None)
    if perspective_id == "risk":
        checks[0] = ("负债约束证据", features.get("debt_ratio") is not None)
    available = [item for item in checks if item[1] is not None]
    passed = sum(1 for _, value in available if value)
    score = round(50 + (passed - max(len(available) - passed, 0)) * 12.5, 1) if available else 0.0
    confidence = round(min(0.9, 0.25 + len(available) * 0.15), 4)
    signal = _signal(score) if available else "insufficient_evidence"
    reason = (
        f"{label}基于{purpose}，可验证指标 {passed}/{len(available)} 项通过。"
        if available
        else f"{label}缺少可验证的专业输入，暂不形成方向判断。"
    )
    return {
        "perspective_id": perspective_id,
        "perspective_label": label,
        "methodology_purpose": purpose,
        "signal": signal,
        "score": score,
        "confidence": confidence,
        "reasoning": reason,
        "checks": [{"name": name, "status": "pass" if value else "fail"} for name, value in available],
        "is_methodology_perspective": True,
    }


def build_panel_review(snapshot_rows: list[dict[str, Any]], symbols: list[str], profile: dict[str, Any]) -> dict[str, Any]:
    votes: list[dict[str, Any]] = []
    panel_seats = panel_for_profile(str(profile.get("selected") or "lite"))
    for symbol in symbols[:1]:
        features = build_features(snapshot_rows, symbol)
        for investor in panel_seats:
            vote = _registered_panel_vote(investor, features)
            votes.append({"symbol": symbol, **vote})
    distribution = Counter(vote["signal"] for vote in votes)
    scores = [float(vote["score"]) for vote in votes if vote["score"] > 0]
    return {
        "methodology_version": UZI_METHOD_VERSION,
        "profile": profile["selected"],
        "scope": "methodology_perspectives",
        "panel_version": UZI_PANEL_VERSION,
        "source_commit": UZI_PANEL_SOURCE_COMMIT,
        "registered_seat_count": len(panel_seats),
        "rule_mode": "source_dimension_rule_adapter",
        "votes": votes,
        "distribution": dict(distribution),
        "average_score": round(sum(scores) / len(scores), 2) if scores else None,
        "disagreement": {
            "exists": len({vote["signal"] for vote in votes}) > 1,
            "summary": "不同方法论视角对证据解释存在分歧。" if votes else "没有足够证据形成 Panel 视角。",
        },
        "confidence": round(sum(vote["confidence"] for vote in votes) / len(votes), 4) if votes else 0.0,
        "risk_flags": ["perspectives_are_not_real_person_advice"],
    }


_PANEL_FIELD_FEATURES: dict[str, tuple[str, ...]] = {
    "1_financials": ("revenue_latest_yi", "net_profit_latest_yi", "fcf_latest_yi", "roe", "net_margin"),
    "2_kline": ("price", "pct_change", "amount"),
    "3_macro": ("pe", "revenue_growth", "debt_ratio"),
    "4_peers": ("pe", "pb"),
    "5_chain": ("revenue_growth", "net_margin"),
    "7_industry": ("revenue_growth", "net_margin"),
    "9_futures": ("amount", "pct_change"),
    "10_valuation": ("pe", "pb", "market_cap_yi"),
    "11_governance": ("roe", "debt_ratio"),
    "12_capital_flow": ("amount", "pct_change"),
    "13_policy": ("revenue_growth",),
    "14_moat": ("net_margin", "roe", "revenue_growth"),
    "15_events": ("pct_change",),
    "16_lhb": ("amount", "pct_change"),
    "17_sentiment": ("pct_change", "amount"),
    "18_trap": ("debt_ratio", "pct_change"),
}


def _registered_panel_vote(investor: dict[str, object], features: dict[str, Any]) -> dict[str, Any]:
    fields = [str(item) for item in list(investor.get("fields") or [])]
    checks: list[dict[str, Any]] = []
    for field in fields:
        feature_names = _PANEL_FIELD_FEATURES.get(field, ())
        present = any(features.get(name) is not None for name in feature_names)
        checks.append(
            {
                "dimension": field,
                "status": "available" if present else "data_gap",
                "feature_names": list(feature_names),
            }
        )
    rule_result = evaluate_investor(str(investor.get("id") or ""), features)
    available = [item for item in checks if item["status"] == "available"]
    passed = len(available)
    total = len(checks)
    score = float(rule_result.get("score") or 0.0)
    confidence = float(rule_result.get("confidence") or 0.0)
    signal = str(rule_result.get("signal") or "insufficient_evidence")
    label = str(investor.get("name") or investor.get("id") or "方法论席位")
    return {
        "investor_id": str(investor.get("id") or ""),
        "perspective_id": str(investor.get("id") or ""),
        "perspective_label": label,
        "school": str(investor.get("group") or ""),
        "required_dimensions": fields,
        "signal": signal,
        "score": score,
        "confidence": confidence,
        "reasoning": str(rule_result.get("rationale") or f"{label}规则覆盖 {passed}/{total} 个声明维度。"),
        "headline": str(rule_result.get("headline") or "数据不足，暂无判断。"),
        "pass_rules": list(rule_result.get("pass_rules") or []),
        "fail_rules": list(rule_result.get("fail_rules") or []),
        "weight_pass": int(rule_result.get("weight_pass") or 0),
        "weight_total": int(rule_result.get("weight_total") or 0),
        "rule_engine_version": str(rule_result.get("rule_engine_version") or ""),
        "checks": checks,
        "is_methodology_perspective": True,
        "is_real_person_advice": False,
        "rule_mode": "migrated_uzi_rule_engine",
    }


def build_risk_scan(
    query: str,
    snapshot_rows: list[dict[str, Any]],
    symbols: list[str],
    profile: dict[str, Any],
    source_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    query_text = str(query or "")
    lowered = query_text.lower()
    keyword_groups = {
        "promotion_language": ("必涨", "翻倍", "稳赚", "暴涨"),
        "paid_group": ("群", "直播", "vip", "社群"),
        "guru_persona": ("老师", "股神", "内幕"),
        "rumor": ("谣言", "辟谣", "虚假消息"),
    }
    source_refs = list(source_refs or [])
    query_refs = [{"type": "request", "id": query_text or "risk_scan_query"}]
    snapshot_refs: dict[str, list[dict[str, Any]]] = {}
    for row in snapshot_rows:
        if not isinstance(row, dict):
            continue
        snapshot_type = str(row.get("snapshot_type") or "").strip()
        if not snapshot_type:
            continue
        source = row.get("source") if isinstance(row.get("source"), dict) else {}
        provider = str(source.get("provider_id") or source.get("source_id") or "").strip()
        symbol = str(row.get("symbol") or "").strip()
        snapshot_refs.setdefault(snapshot_type, []).append(
            {
                "type": "snapshot",
                "id": f"{symbol}:{snapshot_type}" if symbol else snapshot_type,
                "provider": provider or None,
            }
        )

    def refs_for(*snapshot_types: str) -> list[dict[str, Any]]:
        refs = [
            ref
            for snapshot_type in snapshot_types
            for ref in snapshot_refs.get(snapshot_type, [])
        ]
        if refs:
            return list({str(ref.get("id")): ref for ref in refs}.values())
        return source_refs

    signals: list[dict[str, Any]] = []
    features = build_features(snapshot_rows, symbols[0]) if symbols else {}
    for signal_id, keywords in keyword_groups.items():
        hit = any(keyword in lowered for keyword in keywords)
        signals.append(
            {
                "signal_id": signal_id,
                "status": "matched" if hit else "not_observed",
                "severity": "high" if hit else "low",
                "finding": "请求包含需要核验的推广或传言词。" if hit else "请求文本未出现该类词语。",
                "evidence_refs": query_refs,
            }
        )
    missing = []
    if features.get("price") is None:
        missing.append("market_price_missing")
    if features.get("revenue_latest_yi") is None:
        missing.append("financial_revenue_missing")
    for signal_id, finding, snapshot_types in (
        ("fundamental_heat_mismatch", "基本面与热度缺少可比输入。", ("fundamental", "sentiment")),
        ("abnormal_kline", "缺少足够的价格序列，无法判断异常走势。", ("kline", "market")),
        ("cross_platform", "没有接入跨平台搜索证据。", ("research",)),
        ("disclosure_gap", "公告或披露证据不完整。", ("notice", "events")),
    ):
        signals.append(
            {
                "signal_id": signal_id,
                "status": "data_gap",
                "severity": "medium",
                "finding": finding,
                "evidence_refs": refs_for(*snapshot_types),
            }
        )
    hit_count = sum(1 for signal in signals if signal["status"] == "matched")
    if hit_count >= 4:
        risk_level = "high"
        recommendation = "发现多个推广或传言信号，强烈建议先核验信息源。"
    elif hit_count >= 2:
        risk_level = "attention"
        recommendation = "存在推广或传言信号，建议核验信息源。"
    elif missing:
        risk_level = "data_gap"
        recommendation = "风险扫描证据不完整，不能据此判断安全或危险。"
    else:
        risk_level = "low_observed"
        recommendation = "当前请求未观察到明显推广词，但这不等于完成外部风险核验。"
    return {
        "methodology_version": UZI_METHOD_VERSION,
        "profile": profile["selected"],
        "symbol": symbols[0] if symbols else None,
        "risk_level": risk_level,
        "signals": signals,
        "signals_hit_count": hit_count,
        "recommendation": recommendation,
        "confidence": round(0.75 if not missing else 0.45, 4),
        "risk_flags": [*missing, "external_search_not_claimed"],
    }


def build_presentation(
    report: dict[str, Any],
    valuation: dict[str, Any],
    panel: dict[str, Any],
    risk: dict[str, Any],
    profile: dict[str, Any],
    advanced: dict[str, Any] | None = None,
) -> dict[str, Any]:
    models = list(valuation.get("models") or [])
    primary = models[0] if models else {}
    symbol = str(primary.get("symbol") or report.get("symbols", [None])[0] or "")
    dimensions = list((valuation.get("dimensions") or {}).get(symbol) or [])
    return {
        "theme": "uzi_terminal_light",
        "profile": profile["selected"],
        "hero": {
            "symbol": symbol,
            "price": primary.get("market_price"),
            "score": panel.get("average_score"),
            "verdict": primary.get("verdict") or "data_gap",
            "safety": risk.get("risk_level") or "data_gap",
        },
        "core_conclusion": str(report.get("conclusion") or ""),
        "dashboard": {
            "price": {"current": primary.get("market_price")},
            "valuation": {"fair_value": primary.get("fair_value"), "margin_of_safety": primary.get("margin_of_safety")},
            "risks": list(report.get("risk") or []),
            "catalysts": [],
        },
        "great_divide": {
            "bull": {"signal": "bullish", "count": panel.get("distribution", {}).get("bullish", 0)},
            "bear": {"signal": "bearish", "count": panel.get("distribution", {}).get("bearish", 0)},
            "punchline": panel.get("disagreement", {}).get("summary"),
        },
        "panel": {
            "tabs": [vote.get("perspective_label") for vote in panel.get("votes") or []],
            "votes": list(panel.get("votes") or []),
        },
        "radar": dimensions,
        "risk_list": list(risk.get("signals") or []),
        "buy_zones": [],
        "advanced_methods": dict(advanced or {}),
        "source_artifact_ids": [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_gap": list(risk.get("risk_flags") or []),
    }


def build_delivery_conclusion(
    valuation: dict[str, Any],
    panel: dict[str, Any],
    risk: dict[str, Any],
) -> str:
    clauses: list[str] = []
    for model in list(valuation.get("models") or [])[:3]:
        symbol = str(model.get("symbol") or "目标标的")
        fair_value = model.get("fair_value")
        market_price = model.get("market_price")
        verdict = str(model.get("verdict") or "data_gap")
        if fair_value is None or market_price is None:
            clauses.append(f"{symbol} 的 DCF 代理估值缺少完整输入，暂不能形成价格判断")
        elif verdict == "below_proxy_value":
            clauses.append(f"{symbol} 的 DCF 代理价值约为 {fair_value}，高于当前价格 {market_price}")
        elif verdict == "above_proxy_value":
            clauses.append(f"{symbol} 的 DCF 代理价值约为 {fair_value}，低于当前价格 {market_price}")
        else:
            clauses.append(f"{symbol} 的 DCF 代理价值约为 {fair_value}，与当前价格 {market_price} 接近")
    average_score = panel.get("average_score")
    if average_score is not None:
        clauses.append(f"方法论视角平均分为 {average_score}")
    risk_level = str(risk.get("risk_level") or "data_gap")
    risk_text = {
        "low_observed": "文本层面未观察到明显推广词，但外部风险核验仍不完整",
        "attention": "观察到需要核验的推广或传言信号",
        "high": "观察到多个高风险推广或传言信号",
        "data_gap": "风险扫描存在数据缺口",
    }.get(risk_level, f"风险扫描状态为 {risk_level}")
    clauses.append(risk_text)
    if not clauses:
        return "当前没有足够的结构化证据形成研究结论。"
    return "；".join(clauses) + "。这是基于当前来源和模型假设的研究辅助判断，不构成投资建议。"


__all__ = [
    "UZI_METHOD_VERSION",
    "UZI_SOURCE_COMMIT",
    "build_features",
    "build_panel_review",
    "build_presentation",
    "build_risk_scan",
    "build_delivery_conclusion",
    "build_valuation_model",
    "select_profile",
]
