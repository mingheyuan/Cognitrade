from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from research_system.strategies.contracts import StrategyDocumentV1


@dataclass(frozen=True)
class ScoreBounds:
    lower: float
    upper: float
    coverage: float
    confidence: float
    values: dict[str, float | None]
    missing: tuple[str, ...]

    def payload(self) -> dict[str, Any]:
        return {
            "score_lower_bound": round(self.lower, 6),
            "score_upper_bound": round(self.upper, 6),
            "coverage": round(self.coverage, 6),
            "confidence": round(self.confidence, 6),
            "values": self.values,
            "missing": list(self.missing),
        }


@dataclass(frozen=True)
class CompositeScore:
    lower: float
    upper: float
    coverage: float
    confidence: float
    modules: dict[str, ScoreBounds]

    def payload(self) -> dict[str, Any]:
        return {
            "score_lower_bound": round(self.lower, 6),
            "score_upper_bound": round(self.upper, 6),
            "coverage": round(self.coverage, 6),
            "confidence": round(self.confidence, 6),
            "modules": {key: value.payload() for key, value in self.modules.items()},
        }


_BREAKPOINTS: dict[str, tuple[tuple[float, float], ...]] = {
    "year_1_return": ((-30, 0), (-10, 20), (0, 45), (10, 60), (25, 80), (45, 100)),
    "since_inception_annualized": ((-20, 0), (0, 40), (5, 55), (12, 75), (20, 90), (30, 100)),
    "benchmark_excess_year_1": ((-25, 0), (-10, 20), (0, 50), (5, 70), (15, 90), (25, 100)),
    "max_drawdown": ((0, 100), (8, 90), (15, 75), (25, 55), (40, 25), (60, 0)),
    "volatility": ((0, 100), (10, 90), (18, 75), (28, 50), (40, 25), (60, 0)),
    "fee": ((0, 100), (0.25, 90), (0.75, 70), (1.5, 40), (3.0, 0)),
    "return_consistency": ((0, 0), (25, 25), (50, 50), (70, 75), (85, 90), (100, 100)),
}


def score_registered_factor(name: str, raw_value: Any) -> float | None:
    value = _number(raw_value)
    if value is None:
        return None
    if name in {"max_drawdown", "volatility", "fee"}:
        value = abs(value)
    if name == "return_consistency" and 0 <= value <= 1:
        value *= 100
    points = _BREAKPOINTS.get(name)
    if points is None:
        if name in {
            "uzi_overall_score",
            "count_coverage",
            "weight_coverage",
            "disclosure_freshness",
            "concentration_risk",
            "lookthrough_completeness",
            "unsupported_weight",
        }:
            if name in {"count_coverage", "weight_coverage", "lookthrough_completeness"} and 0 <= value <= 1:
                value *= 100
            return _clamp(value)
        return None
    return round(_interpolate(value, points), 6)


def weighted_bounds(
    values: dict[str, float | None],
    weights: dict[str, float],
    *,
    confidence: float = 1.0,
) -> ScoreBounds:
    lower = 0.0
    observed_weight = 0.0
    missing: list[str] = []
    normalized_values: dict[str, float | None] = {}
    for name, weight in weights.items():
        value = values.get(name)
        normalized_values[name] = None if value is None else _clamp(value)
        if value is None:
            missing.append(name)
            continue
        lower += _clamp(value) * weight
        observed_weight += weight
    upper = lower + max(0.0, 1.0 - observed_weight) * 100.0
    return ScoreBounds(
        lower=_clamp(lower),
        upper=_clamp(upper),
        coverage=_unit(observed_weight),
        confidence=_unit(min(confidence, observed_weight if observed_weight else 0.0)),
        values=normalized_values,
        missing=tuple(missing),
    )


def extract_fund_factor_values(payload: dict[str, Any]) -> dict[str, float | None]:
    direct = _record(payload.get("factor_values"))
    returns = _record(payload.get("returns_pct"))
    risk = _record(payload.get("risk"))
    benchmark = _record(payload.get("benchmark"))
    comparison = _record(payload.get("comparison"))
    values = {
        "year_1_return": _first_number(
            direct.get("year_1_return"),
            returns.get("year_1"),
            payload.get("year_1_return_pct"),
        ),
        "since_inception_annualized": _first_number(
            direct.get("since_inception_annualized"),
            risk.get("since_inception_annualized_return_pct"),
            risk.get("annualized_return_pct"),
            payload.get("annualized_return_pct"),
        ),
        "benchmark_excess_year_1": _first_number(
            direct.get("benchmark_excess_year_1"),
            benchmark.get("excess_return_year_1_pct"),
            comparison.get("benchmark_excess_year_1_pct"),
            payload.get("benchmark_excess_year_1_pct"),
        ),
        "max_drawdown": _first_number(
            direct.get("max_drawdown"),
            risk.get("max_drawdown_pct"),
            payload.get("max_drawdown_pct"),
        ),
        "volatility": _first_number(
            direct.get("volatility"),
            risk.get("annualized_volatility_pct"),
            risk.get("volatility_pct"),
            payload.get("volatility_pct"),
        ),
        "fee": _first_number(
            direct.get("fee"),
            payload.get("fee_pct"),
            _record(payload.get("fees")).get("management_fee_pct"),
        ),
        "return_consistency": _first_number(
            direct.get("return_consistency"),
            risk.get("positive_period_ratio"),
            risk.get("return_consistency_pct"),
            payload.get("return_consistency_pct"),
        ),
    }
    return {name: score_registered_factor(name, value) for name, value in values.items()}


def fund_performance_score(payload: dict[str, Any], document: StrategyDocumentV1) -> ScoreBounds:
    return weighted_bounds(
        extract_fund_factor_values(payload),
        document.factor_weights.fund_performance_risk,
        confidence=_unit(_first_number(payload.get("confidence"), 1.0) or 0.0),
    )


def holding_stock_quality_score(
    holdings: Iterable[dict[str, Any]],
    *,
    expected_count: int,
) -> ScoreBounds:
    rows = [dict(row) for row in holdings]
    if expected_count <= 0:
        return ScoreBounds(0, 100, 0, 0, {"uzi_overall_score": None}, ("uzi_overall_score",))

    raw_weights = [_first_number(row.get("effective_weight_pct"), row.get("fund_weight_pct")) for row in rows]
    reliable_weights = all(value is not None and value >= 0 for value in raw_weights) and sum(
        value or 0.0 for value in raw_weights
    ) > 0
    if reliable_weights:
        expected_weight = sum(value or 0.0 for value in raw_weights)
        # Missing top-N slots retain their score mass.  Re-normalising only the
        # disclosed rows to 100% would silently amplify incomplete evidence.
        disclosed_count_mass = min(1.0, len(rows) / expected_count)
        normalized_weights = [
            ((value or 0.0) / expected_weight) * disclosed_count_mass
            for value in raw_weights
        ]
    else:
        normalized_weights = [1.0 / expected_count for _ in rows]

    lower = 0.0
    observed_weight = 0.0
    observed_count = 0
    confidences: list[float] = []
    for row, weight in zip(rows, normalized_weights):
        score = extract_uzi_score(row)
        if score is None or str(row.get("status") or "") not in {"completed", "available", "final"}:
            continue
        lower += score * weight
        observed_weight += weight
        observed_count += 1
        confidences.append(_unit(_first_number(row.get("confidence"), 1.0) or 0.0))
    count_coverage = min(1.0, observed_count / expected_count)
    weight_coverage = min(1.0, observed_weight)
    coverage = min(count_coverage, weight_coverage) if reliable_weights else count_coverage
    missing_weight = max(0.0, 1.0 - observed_weight)
    confidence = min(confidences, default=0.0)
    confidence = min(confidence, coverage)
    return ScoreBounds(
        lower=_clamp(lower),
        upper=_clamp(lower + missing_weight * 100.0),
        coverage=_unit(coverage),
        confidence=_unit(confidence),
        values={"uzi_overall_score": round(lower, 6) if observed_count else None},
        missing=() if observed_count == expected_count else ("uzi_overall_score",),
    )


def holding_structure_score(
    *,
    holdings: Iterable[dict[str, Any]],
    expected_count: int,
    disclosure_age_days: int | None,
    is_lookthrough: bool,
    document: StrategyDocumentV1,
) -> ScoreBounds:
    rows = [dict(row) for row in holdings]
    completed = [row for row in rows if str(row.get("status") or "") in {"completed", "available", "final"}]
    count_coverage = len(completed) / expected_count if expected_count else 0.0
    all_weights = [_first_number(row.get("effective_weight_pct"), row.get("fund_weight_pct")) for row in rows]
    weight_available = all(value is not None and value >= 0 for value in all_weights) and sum(
        value or 0.0 for value in all_weights
    ) > 0
    if weight_available:
        total_weight = sum(value or 0.0 for value in all_weights)
        completed_weight = sum(
            (weight or 0.0)
            for row, weight in zip(rows, all_weights)
            if row in completed
        )
        weight_coverage = completed_weight / total_weight
        unsupported_weight = sum(
            (weight or 0.0)
            for row, weight in zip(rows, all_weights)
            if not bool(row.get("supported", True))
        ) / total_weight
    else:
        weight_coverage = None
        unsupported_weight = None
    concentration = max((value or 0.0 for value in all_weights), default=0.0) if weight_available else None
    values = {
        "count_coverage": score_registered_factor("count_coverage", count_coverage),
        "weight_coverage": score_registered_factor("weight_coverage", weight_coverage),
        "disclosure_freshness": _freshness_score(disclosure_age_days, document.quality_gates.max_holding_staleness_days),
        "concentration_risk": None if concentration is None else _clamp(100.0 - max(0.0, concentration - 10.0) * 3.0),
        "lookthrough_completeness": 100.0 if not is_lookthrough else score_registered_factor("lookthrough_completeness", weight_coverage),
        "unsupported_weight": None if unsupported_weight is None else _clamp(100.0 - unsupported_weight * 100.0),
    }
    return weighted_bounds(
        values,
        document.factor_weights.holding_structure_quality,
        confidence=min(count_coverage, weight_coverage if weight_coverage is not None else count_coverage),
    )


def composite_fund_score(
    *,
    fund_payload: dict[str, Any],
    holdings: Iterable[dict[str, Any]],
    expected_count: int,
    disclosure_age_days: int | None,
    is_lookthrough: bool,
    document: StrategyDocumentV1,
) -> CompositeScore:
    modules = {
        "fund_performance_risk": fund_performance_score(fund_payload, document),
        "holding_stock_quality": holding_stock_quality_score(
            holdings,
            expected_count=expected_count,
        ),
        "holding_structure_quality": holding_structure_score(
            holdings=holdings,
            expected_count=expected_count,
            disclosure_age_days=disclosure_age_days,
            is_lookthrough=is_lookthrough,
            document=document,
        ),
    }
    weights = document.fund_composite.model_dump(mode="python")
    lower = sum(modules[name].lower * float(weights[name]) for name in modules)
    upper = sum(modules[name].upper * float(weights[name]) for name in modules)
    coverage = sum(modules[name].coverage * float(weights[name]) for name in modules)
    confidence = min((module.confidence for module in modules.values()), default=0.0)
    confidence = min(confidence, coverage)
    return CompositeScore(
        lower=_clamp(lower),
        upper=_clamp(upper),
        coverage=_unit(coverage),
        confidence=_unit(confidence),
        modules=modules,
    )


def extract_uzi_score(payload: dict[str, Any]) -> float | None:
    direct = _first_number(
        payload.get("uzi_overall_score"),
        payload.get("overall_score"),
        _record(payload.get("synthesis")).get("overall_score"),
    )
    if direct is not None:
        return _clamp(direct)
    source = _record(payload.get("source")) or _record(payload.get("source_json"))
    direct = _first_number(
        source.get("uzi_overall_score"),
        source.get("overall_score"),
        _record(source.get("synthesis")).get("overall_score"),
    )
    if direct is not None:
        return _clamp(direct)
    results = source.get("results")
    if isinstance(results, list):
        for result in results:
            score = _first_number(_record(_record(result).get("synthesis")).get("overall_score"))
            if score is not None:
                return _clamp(score)
    return None


def point_in_time_available(
    *,
    as_of_date: date,
    published_at: str | None,
    first_seen_at: str | None,
) -> bool:
    for value in (published_at, first_seen_at):
        if not value:
            continue
        try:
            observed = date.fromisoformat(str(value)[:10])
        except ValueError:
            continue
        return observed <= as_of_date
    return False


def _freshness_score(age_days: int | None, maximum: int) -> float | None:
    if age_days is None:
        return None
    if age_days <= 30:
        return 100.0
    if age_days >= maximum:
        return 0.0
    return _clamp(100.0 * (maximum - age_days) / max(1, maximum - 30))


def _interpolate(value: float, points: tuple[tuple[float, float], ...]) -> float:
    if value <= points[0][0]:
        return points[0][1]
    if value >= points[-1][0]:
        return points[-1][1]
    for (left_x, left_y), (right_x, right_y) in zip(points, points[1:]):
        if left_x <= value <= right_x:
            ratio = (value - left_x) / (right_x - left_x)
            return left_y + ratio * (right_y - left_y)
    return 0.0


def _record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_number(*values: Any) -> float | None:
    for value in values:
        converted = _number(value)
        if converted is not None:
            return converted
    return None


def _clamp(value: float) -> float:
    return min(100.0, max(0.0, float(value)))


def _unit(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


__all__ = [
    "CompositeScore",
    "ScoreBounds",
    "composite_fund_score",
    "extract_fund_factor_values",
    "extract_uzi_score",
    "fund_performance_score",
    "holding_stock_quality_score",
    "holding_structure_score",
    "point_in_time_available",
    "score_registered_factor",
    "weighted_bounds",
]
