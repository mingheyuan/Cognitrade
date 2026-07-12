"""Finance Pack-owned Medium/Deep UZI methodology products.

These are deliberately pure computations over Pack snapshot facts.  They do
not import the temporary UZI checkout and do not create their own network or
threading runtime.  Missing inputs remain explicit ``data_gap`` values.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from domain_packs.finance_ashare.subagents.uzi_methodology import (
    UZI_METHOD_VERSION,
    build_features,
)


UZI_SOURCE_DIMENSIONS: tuple[tuple[str, str, str], ...] = (
    ("0_basic", "基础信息", "market"),
    ("1_financials", "财务三表", "fundamental"),
    ("2_kline", "价格趋势", "kline"),
    ("3_macro", "宏观环境", "macro"),
    ("4_peers", "同行比较", "peers"),
    ("5_chain", "产业链", "chain"),
    ("6_research", "研报共识", "research"),
    ("7_industry", "行业景气", "industry"),
    ("8_materials", "原材料", "materials"),
    ("9_futures", "期货关联", "futures"),
    ("10_valuation", "估值", "valuation"),
    ("11_governance", "治理", "governance"),
    ("12_capital_flow", "资金流", "capital_flow"),
    ("13_policy", "政策", "policy"),
    ("14_moat", "护城河", "moat"),
    ("15_events", "事件", "events"),
    ("16_lhb", "龙虎榜", "lhb"),
    ("17_sentiment", "舆情", "sentiment"),
    ("18_trap", "风险信号", "trap"),
    ("19_contests", "组合与比赛", "contests"),
)

# These are derived Pack products, not additional UZI external dimensions.
UZI_DERIVED_DIMENSIONS: tuple[tuple[str, str, str], ...] = (
    ("20_valuation_models", "机构估值模型", "institutional_analysis"),
    ("21_research_workflow", "研究工作流", "research_workflow"),
    ("22_deep_methods", "Deep 方法", "deep_analysis"),
)

UZI_DIMENSIONS: tuple[tuple[str, str, str], ...] = (
    *UZI_SOURCE_DIMENSIONS,
    *UZI_DERIVED_DIMENSIONS,
)


def _num(value: Any, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return default


def _facts(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    value = snapshot.get("facts") if isinstance(snapshot, dict) else None
    return value if isinstance(value, dict) else {}


def _section(facts: dict[str, Any], *names: str) -> dict[str, Any]:
    for name in names:
        value = facts.get(name)
        if isinstance(value, dict):
            return value
    return {}


def _snapshots_for_symbol(rows: list[dict[str, Any]], symbol: str) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("snapshot_type") or "unknown"): row
        for row in rows
        if isinstance(row, dict) and str(row.get("symbol") or "") == symbol
    }


def _three_statement(features: dict[str, Any], profile: str) -> dict[str, Any]:
    revenue = _num(features.get("revenue_latest_yi"))
    if revenue <= 0:
        return {
            "method": "three_statement_projection",
            "status": "data_gap",
            "data_gap": ["revenue_missing"],
            "methodology_log": ["缺少基期营收，未生成三表预测。"],
        }
    growth = [0.12, 0.10, 0.08, 0.06, 0.05]
    margin_input = features.get("net_margin")
    margin = max(_num(margin_input, 12.0) / 100.0, 0.01)
    assumption_flags = [] if margin_input is not None else ["net_margin_assumed"]
    tax = 0.25
    years = [f"Y{index}" for index in range(1, 6)]
    revenues: list[float] = []
    net_income: list[float] = []
    operating_cash_flow: list[float] = []
    capex: list[float] = []
    previous = revenue
    for rate in growth:
        current = previous * (1 + rate)
        ni = current * margin
        depreciation = current * 0.04
        working_capital = (current - previous) * 0.10
        revenues.append(round(current, 3))
        net_income.append(round(ni, 3))
        operating_cash_flow.append(round(ni * (1 - tax) + depreciation - working_capital, 3))
        capex.append(round(current * 0.05, 3))
        previous = current
    fcf = [round(operating_cash_flow[i] - capex[i], 3) for i in range(5)]
    return {
        "method": "three_statement_projection",
        "status": "assumption_only" if assumption_flags else "available",
        "profile": profile,
        "years": years,
        "income_statement": {"revenue": revenues, "net_income": net_income},
        "cash_flow": {
            "operating_cash_flow": operating_cash_flow,
            "capex": capex,
            "free_cash_flow": fcf,
        },
        "balance_sheet": {
            "equity_roll_forward": [
                round(_num(features.get("market_cap_yi")) / max(_num(features.get("pb"), 2.0), 0.1)
                      + sum(net_income[:index + 1]), 3)
                for index in range(5)
            ]
        },
        "assumptions": {
            "revenue_growth": growth,
            "net_margin": margin,
            "tax_rate": tax,
            "capex_to_revenue": 0.05,
        },
        "assumption_flags": assumption_flags,
        "methodology_log": [
            f"基期营收 {revenue:.2f} 亿，采用五年增速路径 {growth}。",
            f"净利率基于已报告数据或保守代理值 {margin:.2%}。",
            f"五年累计自由现金流 {sum(fcf):.2f} 亿。",
        ],
    }


def _lbo(features: dict[str, Any], profile: str) -> dict[str, Any]:
    revenue = _num(features.get("revenue_latest_yi"))
    ebitda = _num(features.get("ebitda_yi"))
    assumption_flags: list[str] = []
    if ebitda <= 0:
        ebitda = revenue * max(_num(features.get("net_margin"), 12.0) / 100.0, 0.01) / 0.6
        if ebitda > 0:
            assumption_flags.append("ebitda_assumed_from_revenue_margin")
    if ebitda <= 0:
        return {
            "method": "quick_lbo",
            "status": "data_gap",
            "data_gap": ["ebitda_missing", "revenue_missing"],
            "methodology_log": ["缺少 EBITDA 或营收，未生成 LBO 交叉检验。"],
        }
    entry_multiple = 8.0
    debt_multiple = 4.0
    exit_multiple = 8.0
    entry_ev = ebitda * entry_multiple
    entry_debt = ebitda * debt_multiple
    entry_equity = max(entry_ev - entry_debt, 0.0)
    debt = entry_debt
    future_ebitda = ebitda
    path: list[float] = []
    debt_path: list[float] = [round(debt, 3)]
    for _ in range(5):
        future_ebitda *= 1.08
        cash_available = max(future_ebitda * 0.5 - debt * 0.06, 0.0)
        debt = max(debt - cash_available * 0.7, 0.0)
        path.append(round(future_ebitda, 3))
        debt_path.append(round(debt, 3))
    exit_equity = max(path[-1] * exit_multiple - debt, 0.0)
    moic = exit_equity / entry_equity if entry_equity else 0.0
    irr = moic ** (1 / 5) - 1 if moic > 0 else 0.0
    return {
        "method": "quick_lbo",
        "status": "assumption_only" if assumption_flags else "available",
        "profile": profile,
        "entry": {"enterprise_value": round(entry_ev, 3), "debt": round(entry_debt, 3), "equity": round(entry_equity, 3)},
        "exit": {"enterprise_value": round(path[-1] * exit_multiple, 3), "debt": round(debt, 3), "equity": round(exit_equity, 3)},
        "ebitda_path": path,
        "debt_path": debt_path,
        "returns": {"moic": round(moic, 3), "irr": round(irr, 4)},
        "verdict": "return_hurdle_met" if irr >= 0.20 else "return_hurdle_not_met",
        "assumptions": {"entry_multiple": entry_multiple, "debt_multiple": debt_multiple, "exit_multiple": exit_multiple, "hold_years": 5},
        "assumption_flags": assumption_flags,
        "methodology_log": [
            f"入口 EV/EBITDA {entry_multiple:.1f}x，债务为 EBITDA 的 {debt_multiple:.1f}x。",
            f"五年后预计 MOIC {moic:.2f}x，IRR {irr:.2%}。",
        ],
    }


def _merger(features: dict[str, Any], profile: str) -> dict[str, Any]:
    eps = _num(features.get("eps"))
    derived_eps = False
    if eps <= 0:
        net_profit = _num(features.get("net_profit_latest_yi"))
        shares = _num(features.get("shares_outstanding_yi"))
        eps = net_profit / shares if net_profit > 0 and shares > 0 else 0.0
        derived_eps = eps > 0
    if eps <= 0:
        return {"method": "accretion_dilution", "status": "data_gap", "data_gap": ["eps_missing"]}
    target_premium = 0.20
    pro_forma_eps = eps * (1 + 0.04 - target_premium * 0.02)
    return {
        "method": "accretion_dilution",
        "status": "derived" if derived_eps else "available",
        "profile": profile,
        "standalone_eps": round(eps, 4),
        "pro_forma_eps": round(pro_forma_eps, 4),
        "accretion_pct": round((pro_forma_eps / eps - 1) * 100, 2),
        "assumptions": {"target_premium": target_premium, "synergy_pct": 0.04},
        "derivation": "net_profit_latest_yi / shares_outstanding_yi" if derived_eps else None,
        "methodology_log": ["以目标溢价和协同效应构造并购后 EPS 增厚/摊薄交叉检验。"],
    }


def _segmental(rows: list[dict[str, Any]], symbol: str, profile: str) -> dict[str, Any]:
    grouped = _snapshots_for_symbol(rows, symbol)
    financial = _section(_facts(grouped.get("fundamental")), "financial_abstract", "financials")
    segments = financial.get("segments") or financial.get("segment_revenue")
    if not isinstance(segments, list) or not segments:
        return {
            "method": "segmental_revenue_build_up",
            "status": "data_gap",
            "profile": profile,
            "data_gap": ["segment_revenue_missing"],
            "segments": [],
            "methodology_log": ["财报快照没有分业务收入，未补造 Segmental 结果。"],
        }
    normalized = [
        {"name": str(item.get("name") or item.get("segment") or "unknown"),
         "revenue": _num(item.get("revenue")),
         "growth": _num(item.get("growth") or item.get("revenue_growth"))}
        for item in segments if isinstance(item, dict)
    ]
    return {
        "method": "segmental_revenue_build_up",
        "status": "available" if normalized else "data_gap",
        "profile": profile,
        "segments": normalized,
        "methodology_log": [f"读取 {len(normalized)} 个已披露业务分部。"],
    }


def build_institutional_analysis(
    rows: list[dict[str, Any]],
    symbols: list[str],
    valuation: dict[str, Any] | None,
    profile: dict[str, Any],
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    valuation_models = list((valuation or {}).get("models") or [])
    for symbol in symbols:
        features = build_features(rows, symbol)
        model = next((item for item in valuation_models if item.get("symbol") == symbol), {})
        results.append(
            {
                "symbol": symbol,
                "status": "available",
                "methods": {
                    "dcf": model,
                    "three_statement": _three_statement(features, profile["selected"]),
                    "quick_lbo": _lbo(features, profile["selected"]),
                    "accretion_dilution": _merger(features, profile["selected"]),
                    "segmental": _segmental(rows, symbol, profile["selected"]),
                },
            }
        )
    gaps = [
        f"{result['symbol']}:{method}"
        for result in results
        for method, value in result["methods"].items()
        if isinstance(value, dict) and value.get("status") == "data_gap"
    ]
    assumption_flags = [
        f"{result['symbol']}:{method}:{flag}"
        for result in results
        for method, value in result["methods"].items()
        if isinstance(value, dict)
        for flag in list(value.get("assumption_flags") or [])
    ]
    return {
        "methodology_version": UZI_METHOD_VERSION,
        "profile": profile["selected"],
        "status": "data_gap" if gaps else ("assumption_only" if assumption_flags else "available"),
        "methods": ["dcf", "comps", "three_statement", "quick_lbo", "accretion_dilution", "segmental"],
        "results": results,
        "data_gap": gaps,
        "confidence": round(max(0.2, 0.85 - 0.05 * len(gaps)), 4),
        "risk_flags": ["advanced_method_assumption_driven", *gaps, *assumption_flags],
        "summary": f"完成 {len(results)} 个标的的机构模型交叉检验，{len(gaps)} 项方法存在数据缺口。",
    }


def build_dimension_coverage(
    rows: list[dict[str, Any]],
    symbols: list[str],
    profile: dict[str, Any],
    *,
    institutional_available: bool = False,
    workflow_available: bool = False,
    deep_available: bool = False,
) -> dict[str, Any]:
    """Expose raw UZI dimensions separately from derived Pack products."""

    coverage: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbols:
        grouped = _snapshots_for_symbol(rows, symbol)
        entries: list[dict[str, Any]] = []
        for dimension_id, label, source_type in UZI_SOURCE_DIMENSIONS:
            source_row = grouped.get(source_type)
            available = bool(
                source_row
                and not bool(source_row.get("degraded"))
            )
            entries.append(
                {
                    "dimension_id": dimension_id,
                    "label": label,
                    "source_type": source_type,
                    "layer": "source",
                    "status": "available" if available else "data_gap",
                    "source_refs": (
                        [dict(source_row.get("source") or {})]
                        if source_row is not None and available
                        else []
                    ),
                    "data_gap": (
                        []
                        if available
                        else [
                            f"{source_type}_snapshot_missing"
                            if source_row is None
                            else str(source_row.get("degrade_reason") or f"{source_type}_snapshot_degraded")
                        ]
                    ),
                }
            )
        for dimension_id, label, source_type in UZI_DERIVED_DIMENSIONS:
            derived_available = {
                "institutional_analysis": institutional_available,
                "research_workflow": workflow_available,
                "deep_analysis": deep_available,
            }[source_type]
            entries.append(
                {
                    "dimension_id": dimension_id,
                    "label": label,
                    "source_type": source_type,
                    "layer": "derived",
                    "status": "available" if derived_available else "data_gap",
                    "source_refs": [],
                    "data_gap": [] if derived_available else [f"{source_type}_artifact_missing"],
                }
            )
        coverage[symbol] = entries
    source_missing = [
        f"{symbol}:{entry['dimension_id']}"
        for symbol, entries in coverage.items()
        for entry in entries
        if entry.get("layer") == "source" and entry["status"] != "available"
    ]
    derived_missing = [
        f"{symbol}:{entry['dimension_id']}"
        for symbol, entries in coverage.items()
        for entry in entries
        if entry.get("layer") == "derived" and entry["status"] != "available"
    ]
    source_total = len(UZI_SOURCE_DIMENSIONS) * len(symbols)
    derived_total = len(UZI_DERIVED_DIMENSIONS) * len(symbols)
    source_available = source_total - len(source_missing)
    derived_available = derived_total - len(derived_missing)
    return {
        "methodology_version": UZI_METHOD_VERSION,
        "profile": profile["selected"],
        "dimension_count": len(UZI_SOURCE_DIMENSIONS),
        "raw_dimension_count": len(UZI_SOURCE_DIMENSIONS),
        "derived_dimension_count": len(UZI_DERIVED_DIMENSIONS),
        "total_dimension_count": len(UZI_DIMENSIONS),
        "coverage": coverage,
        "available_count": source_available,
        "missing_count": len(source_missing),
        "derived_available_count": derived_available,
        "derived_missing_count": len(derived_missing),
        "data_gap": [*source_missing, *derived_missing],
        "confidence": round(
            source_available / max(source_total, 1),
            4,
        ),
        "risk_flags": ["dimension_coverage_is_source_bound", *source_missing[:12]],
    }


def _workflow_for_symbol(rows: list[dict[str, Any]], symbol: str, valuation: dict[str, Any] | None) -> dict[str, Any]:
    grouped = _snapshots_for_symbol(rows, symbol)
    features = build_features(rows, symbol)
    events = _facts(grouped.get("events"))
    research = _facts(grouped.get("research"))
    financial = _section(_facts(grouped.get("fundamental")), "financial_abstract", "financials")
    current_price = _num(features.get("price"))
    fair_value = _num((next((m for m in (valuation or {}).get("models", []) if m.get("symbol") == symbol), {}) or {}).get("fair_value"))
    upside = (fair_value - current_price) / current_price if fair_value and current_price else None
    revenue_growth = _num(features.get("revenue_growth"))
    profit_growth = _num(features.get("profit_growth"))
    thesis = [
        {"pillar": "估值空间", "status": "support" if upside is not None and upside > 0.15 else "watch", "evidence": f"代理价值相对当前价格变化 {upside:.1%}" if upside is not None else "缺少可比估值输入"},
        {"pillar": "经营增长", "status": "support" if max(revenue_growth, profit_growth) >= 10 else "watch", "evidence": f"营收增速 {revenue_growth:.1f}%，利润增速 {profit_growth:.1f}%"},
        {"pillar": "现金流质量", "status": "support" if features.get("fcf_latest_yi") is not None and features.get("fcf_latest_yi") > 0 else "data_gap", "evidence": f"FCF {features.get('fcf_latest_yi') or '—'} 亿"},
    ]
    event_rows = []
    for item in list(events.get("event_timeline") or events.get("recent_news") or [])[:10]:
        if isinstance(item, dict):
            event_rows.append({"date": item.get("date") or item.get("pub_time"), "title": item.get("title") or item.get("headline"), "impact": "medium"})
        elif isinstance(item, str):
            event_rows.append({"date": None, "title": item, "impact": "medium"})
    screens = {
        "value": bool(features.get("pe") is not None and features.get("pe") < 20),
        "growth": bool(max(revenue_growth, profit_growth) >= 15),
        "quality": bool(features.get("roe") is not None and features.get("roe") >= 12),
        "gulp": bool(max(revenue_growth, profit_growth) >= 15 and features.get("pe") is not None and features.get("pe") < 35),
    }
    return {
        "symbol": symbol,
        "status": "available",
        "initiating_coverage": {"rating": "关注" if upside is None else ("增持" if upside > 0.15 else "中性"), "target_price": fair_value or None, "upside": upside},
        "earnings_analysis": {"revenue": _num(financial.get("revenue")), "net_profit": _num(financial.get("net_profit")), "revenue_growth": revenue_growth, "profit_growth": profit_growth},
        "catalyst_calendar": {"events": event_rows, "next_30d": event_rows[:3]},
        "thesis_tracker": {"pillars": thesis, "support_count": sum(item["status"] == "support" for item in thesis)},
        "idea_screens": screens,
        "sector_overview": {"sector": features.get("sector") or "未识别", "status": "data_gap" if not features.get("sector") else "available"},
    }


def build_research_workflow(
    rows: list[dict[str, Any]],
    symbols: list[str],
    valuation: dict[str, Any] | None,
    profile: dict[str, Any],
) -> dict[str, Any]:
    results = [_workflow_for_symbol(rows, symbol, valuation) for symbol in symbols]
    return {
        "methodology_version": UZI_METHOD_VERSION,
        "profile": profile["selected"],
        "results": results,
        "summary": f"完成 {len(results)} 个标的的覆盖、业绩、催化剂、Thesis 和 Idea Screen 工作流。",
        "confidence": 0.65 if results else 0.0,
        "risk_flags": ["forward_calendar_is_source_limited"],
    }


def _competitive(features: dict[str, Any], workflow: dict[str, Any]) -> dict[str, Any]:
    growth = _num(features.get("revenue_growth"))
    margin = _num(features.get("net_margin"))
    if growth >= 15 and margin >= 10:
        category = "growth_leader"
    elif growth < 5 and margin < 8:
        category = "challenged"
    else:
        category = "cash_generator"
    return {"category": category, "growth": growth, "margin": margin, "industry_attractiveness": "待补充行业比较"}


def _deep_for_symbol(
    rows: list[dict[str, Any]],
    symbol: str,
    institutional: dict[str, Any] | None,
    workflow: dict[str, Any] | None,
) -> dict[str, Any]:
    features = build_features(rows, symbol)
    result = next((item for item in (institutional or {}).get("results", []) if item.get("symbol") == symbol), {})
    methods = result.get("methods") if isinstance(result, dict) else {}
    three_statement = methods.get("three_statement") if isinstance(methods, dict) else {}
    fcf_path = list((three_statement or {}).get("cash_flow", {}).get("free_cash_flow") or [])
    risks = []
    if features.get("debt_ratio") is None:
        risks.append({"item": "杠杆数据", "status": "data_gap", "action": "补充资产负债表和债务期限"})
    if features.get("fcf_latest_yi") is None:
        risks.append({"item": "自由现金流", "status": "data_gap", "action": "补充现金流量表"})
    if features.get("pct_change") is not None and abs(float(features["pct_change"])) > 8:
        risks.append({"item": "价格波动", "status": "attention", "action": "核对事件和公告来源"})
    if not risks:
        risks.append({"item": "外部事实核验", "status": "review_required", "action": "复核公告、产业链和监管信息"})
    workflow_result = next((item for item in (workflow or {}).get("results", []) if item.get("symbol") == symbol), {})
    bull_points = [item.get("evidence") for item in workflow_result.get("thesis_tracker", {}).get("pillars", []) if item.get("status") == "support"]
    bear_points = [item.get("action") for item in risks]
    return {
        "symbol": symbol,
        "ic_memo": {"recommendation": workflow_result.get("initiating_coverage", {}).get("rating", "待研究"), "thesis": bull_points[:3], "top_risks": risks[:3]},
        "unit_economics": {"revenue": features.get("revenue_latest_yi"), "net_margin": features.get("net_margin"), "cashflow_conversion": round((_num(features.get("fcf_latest_yi")) / max(_num(features.get("net_profit_latest_yi")), 0.001)), 3) if features.get("net_profit_latest_yi") else None},
        "value_creation_plan": {"growth_levers": ["收入增长", "利润率改善"], "cashflow_path": fcf_path, "status": "assumption_sensitive"},
        "dd_checklist": {"items": risks, "completion_pct": round(max(0, 100 - len(risks) * 20), 1)},
        "competitive_analysis": _competitive(features, workflow or {}),
        "portfolio_rebalance": {"status": "not_requested", "reason": "未提供用户持仓，不生成仓位建议"},
        "bull_bear_debate": {"bull": bull_points[:3], "bear": bear_points[:3], "punchline": "证据与假设需要在同一来源链上复核"},
    }


def build_deep_analysis(
    rows: list[dict[str, Any]],
    symbols: list[str],
    institutional: dict[str, Any] | None,
    workflow: dict[str, Any] | None,
    profile: dict[str, Any],
) -> dict[str, Any]:
    results = [_deep_for_symbol(rows, symbol, institutional, workflow) for symbol in symbols]
    return {
        "methodology_version": UZI_METHOD_VERSION,
        "profile": profile["selected"],
        "results": results,
        "summary": f"完成 {len(results)} 个标的的 IC Memo、DD、竞争分析、价值创造和多空辩论视图。",
        "confidence": 0.55 if results else 0.0,
        "risk_flags": ["deep_views_are_assumption_sensitive", "portfolio_rebalance_not_requested"],
    }


__all__ = [
    "UZI_SOURCE_DIMENSIONS",
    "UZI_DERIVED_DIMENSIONS",
    "UZI_DIMENSIONS",
    "build_dimension_coverage",
    "build_deep_analysis",
    "build_institutional_analysis",
    "build_research_workflow",
]
