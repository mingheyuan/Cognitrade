from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
import math
from typing import Any


FUND_METHODOLOGY_VERSION = "uzi-3.9.2-fund-selection-v3"

_COMPONENTS: tuple[tuple[str, float, bool], ...] = (
    ("return_1y", 0.20, True),
    ("return_3y_annualized", 0.15, True),
    ("benchmark_excess_1y", 0.15, True),
    ("max_drawdown", 0.20, True),
    ("volatility", 0.15, False),
    ("fee", 0.05, False),
    ("return_consistency", 0.10, True),
)


def build_fund_analysis(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Rank comparable funds only from observed performance and NAV evidence."""

    funds = [dict(row) for row in snapshot.get("funds") or [] if isinstance(row, dict)]
    candidates = [_candidate_metrics(row) for row in funds]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        groups[_comparison_group(candidate)].append(candidate)

    evaluated: list[dict[str, Any]] = []
    analysis_gaps: list[str] = list(snapshot.get("data_gap") or [])
    for group_name, rows in groups.items():
        component_values = {
            component: [
                float(row["metrics"][component])
                for row in rows
                if row["metrics"].get(component) is not None
            ]
            for component, _weight, _higher_is_better in _COMPONENTS
        }
        for row in rows:
            score_parts: list[dict[str, Any]] = []
            observed_weight = 0.0
            weighted_score = 0.0
            for component, weight, higher_is_better in _COMPONENTS:
                value = row["metrics"].get(component)
                peers = component_values[component]
                percentile = _percentile(value, peers, higher_is_better=higher_is_better)
                if percentile is None:
                    continue
                observed_weight += weight
                weighted_score += percentile * weight
                score_parts.append(
                    {
                        "component": component,
                        "value": value,
                        "weight": weight,
                        "percentile": round(percentile, 4),
                    }
                )
            score = round(weighted_score / observed_weight, 2) if observed_weight > 0 else None
            coverage = round(observed_weight / sum(item[1] for item in _COMPONENTS), 4)
            selection_eligible = score is not None and coverage >= 0.6 and len(rows) >= 2
            gaps = list(row.get("data_gap") or [])
            if len(rows) < 2:
                gaps.append("comparable_fund_sample_too_small")
            if coverage < 0.6:
                gaps.append("fund_score_evidence_coverage_below_60pct")
            evaluated.append(
                {
                    **row,
                    "comparison_group": group_name,
                    "score": score,
                    "score_coverage": coverage,
                    "score_components": score_parts,
                    "selection_eligible": selection_eligible,
                    "data_gap": list(dict.fromkeys(gaps)),
                    "quality_status": (
                        "available"
                        if selection_eligible and not gaps
                        else "degraded"
                        if row.get("metrics")
                        else "data_gap"
                    ),
                }
            )

    for group_name in groups:
        group_rows = [row for row in evaluated if row["comparison_group"] == group_name]
        ranked = sorted(
            [row for row in group_rows if row["selection_eligible"]],
            key=lambda row: float(row["score"]),
            reverse=True,
        )
        for rank, row in enumerate(ranked, start=1):
            row["rank"] = rank
        for row in group_rows:
            row.setdefault("rank", None)

    evaluated.sort(
        key=lambda row: (
            row.get("rank") is None,
            int(row.get("rank") or 9999),
            str(row.get("fund_code") or ""),
        )
    )
    recommendations = [
        {
            "fund_code": row["fund_code"],
            "fund_name": row["fund_name"],
            "fund_type": row.get("fund_type"),
            "comparison_group": row["comparison_group"],
            "rank": row["rank"],
            "score": row["score"],
            "score_coverage": row["score_coverage"],
            "reason": _selection_reason(row),
            "risk": _risk_summary(row),
        }
        for row in evaluated
        if row.get("selection_eligible") and int(row.get("rank") or 9999) <= 3
    ]
    if not recommendations:
        analysis_gaps.append("no_fund_met_selection_evidence_gate")

    candidate_gaps = any(row.get("data_gap") for row in evaluated)
    quality_status = (
        "available"
        if recommendations and not analysis_gaps and not candidate_gaps
        else "degraded"
        if evaluated
        else "data_gap"
    )
    return {
        "methodology_version": FUND_METHODOLOGY_VERSION,
        "fund_category": snapshot.get("fund_category"),
        "investment_cycle": snapshot.get("investment_cycle"),
        "candidate_count": len(evaluated),
        "candidates": evaluated,
        "recommendations": recommendations,
        "selection_policy": {
            "description": "同类基金横向比较；长期年化收益优先使用近三年真实收益，基金历史不足三年时使用成立以来真实收益按实际观测期年化。",
            "minimum_score_coverage": 0.6,
            "minimum_comparable_sample": 2,
            "components": [
                {
                    "id": component,
                    "weight": weight,
                    "direction": "higher" if higher else "lower",
                }
                for component, weight, higher in _COMPONENTS
            ],
            "non_imputation_rule": "缺失字段不补 0、不补中性值；成立以来年化仅使用完整真实净值区间及真实累计收益，不构造历史。",
        },
        "quality_status": quality_status,
        "data_gap": list(dict.fromkeys(analysis_gaps)),
        "provider_errors": list(snapshot.get("provider_errors") or []),
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _candidate_metrics(row: dict[str, Any]) -> dict[str, Any]:
    returns = dict(row.get("returns_pct") or {})
    risk = dict(row.get("risk") or {})
    annualized_return = _long_term_annualized_return(returns, risk)
    observed_returns = [
        _number(returns.get(key))
        for key in ("month_3", "month_6", "year_1", "year_2", "year_3")
    ]
    observed_returns = [value for value in observed_returns if value is not None]
    consistency = (
        sum(1 for value in observed_returns if value > 0) / len(observed_returns) * 100.0
        if observed_returns
        else None
    )
    benchmark_1y = next(
        (
            row
            for row in list(row.get("benchmark_performance") or [])
            if isinstance(row, dict) and str(row.get("period") or "").replace(" ", "") in {"近1年", "1年"}
        ),
        {},
    )
    metrics = {
        "return_1y": _number(returns.get("year_1")),
        # Keep the established artifact field for contract continuity.  The UI
        # distinguishes the normal three-year basis from the since-inception
        # fallback by checking whether returns_pct.year_3 is present.
        "return_3y_annualized": annualized_return,
        "benchmark_excess_1y": _number(benchmark_1y.get("excess_return_pct")),
        # A less-negative drawdown is better, so the raw value can use the normal higher-is-better rank.
        "max_drawdown": _number(risk.get("max_drawdown_pct")),
        "volatility": _number(risk.get("annualized_volatility_pct")),
        "fee": _number(row.get("fee_pct")),
        "return_consistency": round(consistency, 4) if consistency is not None else None,
    }
    data_gap = list(row.get("data_gap") or [])
    data_gap.extend(key for key, value in metrics.items() if value is None)
    return {
        "fund_code": str(row.get("fund_code") or ""),
        "fund_name": str(row.get("fund_name") or row.get("fund_code") or ""),
        "fund_type": row.get("fund_type"),
        "as_of": row.get("as_of"),
        "unit_nav": row.get("unit_nav"),
        "returns_pct": returns,
        "risk": risk,
        "fee_pct": row.get("fee_pct"),
        "metrics": {key: value for key, value in metrics.items() if value is not None},
        "top_holdings": list(row.get("top_holdings") or []),
        "holdings_period": row.get("holdings_period"),
        "top10_concentration_pct": row.get("top10_concentration_pct"),
        "benchmark_definition": row.get("benchmark_definition"),
        "benchmark_performance": list(row.get("benchmark_performance") or []),
        "manager_tenures": list(row.get("manager_tenures") or []),
        "scale_history": list(row.get("scale_history") or []),
        "scale_change": dict(row.get("scale_change") or {}),
        "data_gap": list(dict.fromkeys(data_gap)),
    }


def _long_term_annualized_return(
    returns: dict[str, Any],
    risk: dict[str, Any],
) -> float | None:
    trailing_three_year = _number(returns.get("year_3"))
    if trailing_three_year is not None and trailing_three_year > -100:
        value = ((1.0 + trailing_three_year / 100.0) ** (1.0 / 3.0) - 1.0) * 100.0
        return round(value, 4)

    period_start = _date_value(risk.get("period_start"))
    period_end = _date_value(risk.get("period_end"))
    if period_start is None or period_end is None or period_end <= period_start:
        return None
    observation_years = (period_end - period_start).days / 365.2425
    if observation_years <= 0:
        return None

    # Prefer the provider's true since-inception total return because it can
    # include distributions that are not represented by unit NAV alone.  The
    # complete NAV interval supplies the real observation length.  If the
    # ranking source omits that field too, fall back to the observed NAV return.
    period_return = _number(returns.get("since_inception"))
    if period_return is None:
        period_return = _number(risk.get("period_return_pct"))
    if period_return is None or period_return <= -100:
        return None
    value = ((1.0 + period_return / 100.0) ** (1.0 / observation_years) - 1.0) * 100.0
    return round(value, 4)


def _date_value(value: Any):
    try:
        return datetime.fromisoformat(str(value)).date()
    except (TypeError, ValueError):
        return None


def _comparison_group(row: dict[str, Any]) -> str:
    fund_type = str(row.get("fund_type") or "未分类").strip()
    for separator in ("-", "—", "/"):
        if separator in fund_type:
            fund_type = fund_type.split(separator, 1)[0].strip()
    return fund_type or "未分类"


def _percentile(value: Any, peers: list[float], *, higher_is_better: bool) -> float | None:
    number = _number(value)
    if number is None or len(peers) < 2:
        return None
    ordered = sorted(peers, reverse=not higher_is_better)
    better = sum(1 for peer in ordered if (peer < number if higher_is_better else peer > number))
    equal = sum(1 for peer in ordered if peer == number)
    # Mid-rank percentile; it is only defined for an actual comparable sample.
    return (better + max(equal - 1, 0) / 2) / (len(ordered) - 1) * 100.0


def _selection_reason(row: dict[str, Any]) -> str:
    metrics = dict(row.get("metrics") or {})
    parts: list[str] = []
    if metrics.get("return_1y") is not None:
        parts.append(f"近1年收益 {metrics['return_1y']:.2f}%")
    if metrics.get("max_drawdown") is not None:
        parts.append(f"样本期最大回撤 {metrics['max_drawdown']:.2f}%")
    if metrics.get("benchmark_excess_1y") is not None:
        parts.append(f"近1年相对基准 {metrics['benchmark_excess_1y']:+.2f}%")
    parts.append(f"证据覆盖 {float(row.get('score_coverage') or 0) * 100:.0f}%")
    return "；".join(parts)


def _risk_summary(row: dict[str, Any]) -> str:
    risk = dict(row.get("risk") or {})
    concentration = _number(row.get("top10_concentration_pct"))
    parts: list[str] = []
    if risk.get("annualized_volatility_pct") is not None:
        parts.append(f"年化波动 {float(risk['annualized_volatility_pct']):.2f}%")
    if concentration is not None:
        parts.append(f"前十大持仓 {concentration:.2f}%")
    scale_change = dict(row.get("scale_change") or {})
    if _number(scale_change.get("total_share_change_pct")) is not None:
        parts.append(f"最新份额变化 {float(scale_change['total_share_change_pct']):+.2f}%")
    if row.get("data_gap"):
        parts.append(f"仍有 {len(row['data_gap'])} 项数据缺口")
    return "；".join(parts) or "暂无足够风险字段"


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


__all__ = ["FUND_METHODOLOGY_VERSION", "build_fund_analysis"]
