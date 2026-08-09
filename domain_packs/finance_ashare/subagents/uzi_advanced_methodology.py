"""Finance Pack-owned Medium/Deep UZI methodology products.

These are deliberately pure computations over Pack snapshot facts.  They do
not import the temporary UZI checkout and do not create their own network or
threading runtime.  Missing inputs remain explicit ``data_gap`` values.
"""

from __future__ import annotations

from typing import Any

from domain_packs.finance_ashare.subagents.uzi_method_products import build_method_product
from domain_packs.finance_ashare.subagents.uzi_methodology import (
    UZI_METHOD_VERSION,
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
    ("fund_holders", "基金持仓", "fund_holders"),
    ("similar_stocks", "相似股票", "similar_stocks"),
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


def build_institutional_analysis(
    rows: list[dict[str, Any]],
    symbols: list[str],
    valuation: dict[str, Any] | None,
    profile: dict[str, Any],
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    requested_products = ["three_statement", "quick_lbo", "accretion_dilution"]
    if bool(profile.get("enable_segmental_model")):
        requested_products.append("segmental")
    products = {
        method: build_method_product(
            method,
            rows,
            symbols,
            inputs=inputs,
            valuation=valuation,
        )
        for method in requested_products
    }
    results: list[dict[str, Any]] = []
    valuation_models = list((valuation or {}).get("models") or [])
    for symbol in symbols:
        model = next((item for item in valuation_models if item.get("symbol") == symbol), {})
        methods: dict[str, Any] = {
            "dcf": model or {
                "symbol": symbol,
                "status": "data_gap",
                "data_gap": ["valuation_model_missing"],
            },
        }
        for method, product in products.items():
            methods[method] = next(
                (
                    item
                    for item in list(product.get("results") or [])
                    if str(item.get("symbol") or "") == symbol
                ),
                {
                    "symbol": symbol,
                    "quality_status": "data_gap",
                    "data_gap": [f"{method}_result_missing"],
                },
            )
        statuses = [
            str(value.get("quality_status") or value.get("status") or "data_gap")
            for value in methods.values()
            if isinstance(value, dict)
        ]
        results.append(
            {
                "symbol": symbol,
                "status": (
                    "data_gap"
                    if not statuses or all(status == "data_gap" for status in statuses)
                    else "degraded"
                    if any(status in {"data_gap", "degraded"} for status in statuses)
                    else "assumption_only"
                    if any(status == "assumption_only" for status in statuses)
                    else "available"
                ),
                "methods": methods,
            }
        )
    gaps = [
        f"{result['symbol']}:{method}:{gap}"
        for result in results
        for method, value in result["methods"].items()
        if isinstance(value, dict)
        for gap in list(value.get("data_gap") or [])
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
        "status": (
            "data_gap"
            if not results or all(result["status"] == "data_gap" for result in results)
            else "degraded"
            if gaps
            else "assumption_only"
            if assumption_flags
            else "available"
        ),
        "methods": [
            "dcf", "comps", "three_statement", "quick_lbo", "accretion_dilution",
            *(["segmental"] if bool(profile.get("enable_segmental_model")) else []),
        ],
        "results": results,
        "data_gap": gaps,
        "confidence": round(max(0.2, 0.85 - 0.05 * len(gaps)), 4),
        "risk_flags": ["only_observed_data_and_explicit_user_assumptions", *gaps, *assumption_flags],
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


def _method_result_for_symbol(
    product: dict[str, Any],
    method: str,
    symbol: str,
) -> dict[str, Any]:
    return next(
        (
            item
            for item in list(product.get("results") or [])
            if str(item.get("symbol") or "") == symbol
        ),
        {
            "symbol": symbol,
            "quality_status": "data_gap",
            "data_gap": [f"{method}_result_missing"],
        },
    )


def _method_status(methods: dict[str, Any]) -> str:
    statuses = [str(value.get("quality_status") or "data_gap") for value in methods.values()]
    if not statuses or all(status == "data_gap" for status in statuses):
        return "data_gap"
    if any(status in {"data_gap", "degraded"} for status in statuses):
        return "degraded"
    if any(status == "assumption_only" for status in statuses):
        return "assumption_only"
    return "available"


def build_research_workflow(
    rows: list[dict[str, Any]],
    symbols: list[str],
    valuation: dict[str, Any] | None,
    profile: dict[str, Any],
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    method_ids = [
        "initiation_report",
        "earnings_review",
        "catalyst_calendar",
        "thesis_tracker",
        "morning_note",
    ]
    products = {
        method: build_method_product(
            method,
            rows,
            symbols,
            inputs=inputs,
            valuation=valuation,
        )
        for method in method_ids
    }
    results = []
    gaps: list[str] = []
    for symbol in symbols:
        methods: dict[str, Any] = {}
        for method, product in products.items():
            result = _method_result_for_symbol(product, method, symbol)
            methods[method] = result
            gaps.extend(
                f"{symbol}:{method}:{gap}"
                for gap in list(result.get("data_gap") or [])
            )
        results.append({
            "symbol": symbol,
            "status": _method_status(methods),
            "methods": methods,
        })
    return {
        "methodology_version": UZI_METHOD_VERSION,
        "profile": profile["selected"],
        "results": results,
        "status": (
            "data_gap"
            if not results or all(result["status"] == "data_gap" for result in results)
            else "degraded"
            if gaps
            else "available"
        ),
        "data_gap": list(dict.fromkeys(gaps)),
        "summary": f"完成 {len(results)} 个标的的覆盖、业绩、催化剂、Thesis 和 Idea Screen 工作流。",
        "confidence": max(0.0, 1.0 - len(set(gaps)) / max(len(symbols) * len(method_ids), 1)),
        "risk_flags": ["workflow_is_source_bound", *list(dict.fromkeys(gaps))],
    }


def build_deep_analysis(
    rows: list[dict[str, Any]],
    symbols: list[str],
    institutional: dict[str, Any] | None,
    workflow: dict[str, Any] | None,
    profile: dict[str, Any],
    inputs: dict[str, Any] | None = None,
    risk_scan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    method_ids = ["ic_memo", "porter_bcg", "dd_checklist", "unit_economics", "value_creation_plan"]
    products = {
        method: build_method_product(
            method,
            rows,
            symbols,
            inputs=inputs,
            valuation=_valuation_from_institutional(institutional),
            risk_scan=risk_scan,
        )
        for method in method_ids
    }
    results = []
    gaps: list[str] = []
    assumptions: list[str] = []
    for symbol in symbols:
        methods: dict[str, Any] = {}
        for method, product in products.items():
            result = _method_result_for_symbol(product, method, symbol)
            methods[method] = result
            gaps.extend(f"{symbol}:{method}:{gap}" for gap in list(result.get("data_gap") or []))
            assumptions.extend(
                f"{symbol}:{method}:{flag}"
                for flag in list(result.get("assumption_flags") or [])
            )
        results.append({
            "symbol": symbol,
            "status": _method_status(methods),
            "methods": methods,
            "institutional_status": (institutional or {}).get("status"),
            "workflow_status": (workflow or {}).get("status"),
            "bull_bear_debate": {
                "status": "pending_agent_review",
                "bull": [],
                "bear": [],
                "data_gap": ["deep_agent_bull_bear_review_not_yet_applied"],
            },
        })
    return {
        "methodology_version": UZI_METHOD_VERSION,
        "profile": profile["selected"],
        "results": results,
        "status": (
            "data_gap"
            if not results or all(result["status"] == "data_gap" for result in results)
            else "degraded"
            if gaps
            else "assumption_only"
            if assumptions
            else "available"
        ),
        "data_gap": list(dict.fromkeys(gaps)),
        "assumption_flags": list(dict.fromkeys(assumptions)),
        "summary": f"完成 {len(results)} 个标的的 IC Memo、DD、竞争分析、价值创造和多空辩论视图。",
        "confidence": max(0.0, 1.0 - len(set(gaps)) / max(len(symbols) * len(method_ids), 1)),
        "risk_flags": ["deep_views_require_source_evidence_or_explicit_user_assumptions", *list(dict.fromkeys(gaps))],
    }


def _valuation_from_institutional(institutional: dict[str, Any] | None) -> dict[str, Any]:
    models = [
        dict(methods["dcf"])
        for result in list((institutional or {}).get("results") or [])
        if isinstance(result, dict)
        if isinstance((methods := result.get("methods")), dict)
        if isinstance(methods.get("dcf"), dict)
    ]
    return {"models": models}


__all__ = [
    "UZI_SOURCE_DIMENSIONS",
    "UZI_DERIVED_DIMENSIONS",
    "UZI_DIMENSIONS",
    "build_dimension_coverage",
    "build_deep_analysis",
    "build_institutional_analysis",
    "build_research_workflow",
]
