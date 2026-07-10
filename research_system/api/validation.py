from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any

from research_system.api.errors import ApiError
from research_system.schemas.protocol import RequestEnvelope


SUPPORTED_RESEARCH_INTENTS = frozenset(
    {
        "stock_check",
        "stock_compare",
        "stock_screen",
        "sector_research",
        "distill_kol_logic",
        "review_thesis",
    }
)

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_INVESTMENT_CYCLES = frozenset({"short_term", "mid_term", "long_term"})
_STOCK_SNAPSHOT_TYPES = frozenset(
    {"market", "fundamental", "notice", "lhb", "capital_flow", "sentiment", "events"}
)
_METHODOLOGIES = frozenset({"comps", "dcf", "cashflow_resilience"})
_SCREEN_STYLES = frozenset({"value", "growth", "quality", "gulp", "short"})

_PLANNING_FIELDS = {"planning_context_hints"}
_STOCK_FIELDS = {
    "symbol",
    "snapshot_types",
    "allow_partial_snapshot_types",
    "investment_cycle",
    *_PLANNING_FIELDS,
}
_CONSTRAINTS_BY_INTENT: dict[str, set[str]] = {
    "stock_check": set(_STOCK_FIELDS),
    "stock_compare": {"symbols", *_STOCK_FIELDS},
    "stock_screen": {
        "sector_query",
        "candidate_pool_size",
        "investment_cycle",
        "prefer_methodology",
        "screen_style",
        "top_k",
        "min_score",
        "watchlist_top_k",
        "watchlist_min_score",
        "enrichment_top_n",
        "skip_enrichment",
        *_PLANNING_FIELDS,
    },
    "sector_research": {"sector_query", "include_policy_snapshot", *_PLANNING_FIELDS},
    "distill_kol_logic": set(),
    "review_thesis": {
        "review_target_run_id",
        "snapshot_types",
        "allow_partial_snapshot_types",
        "include_policy_snapshot",
        "investment_cycle",
        *_PLANNING_FIELDS,
    },
}
def safe_identifier(value: str, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ApiError(
            status_code=400,
            code="invalid_identifier",
            message=f"{field_name} contains unsupported characters or has an invalid length",
            details={"field": field_name},
        )
    return normalized


def validate_research_request(request: RequestEnvelope) -> RequestEnvelope:
    query = str(request.user_query or "").strip()
    if not query:
        raise ApiError(
            status_code=422,
            code="invalid_query",
            message="user_query must not be empty",
            details={"field": "user_query"},
        )
    if len(query) > 20_000:
        raise ApiError(
            status_code=422,
            code="invalid_query",
            message="user_query must not exceed 20000 characters",
            details={"field": "user_query"},
        )

    intent = str(request.intent or "").strip() or None
    if intent is not None and not intent.startswith("domain:") and intent not in SUPPORTED_RESEARCH_INTENTS:
        raise ApiError(
            status_code=422,
            code="invalid_intent",
            message=f"Unsupported research intent: {intent}",
            details={"intent": intent, "supported_intents": sorted(SUPPORTED_RESEARCH_INTENTS)},
        )

    constraints = dict(request.constraints or {})
    if intent is None or intent.startswith("domain:"):
        return request.model_copy(update={"intent": intent, "user_query": query, "constraints": constraints})

    allowed_fields = _CONSTRAINTS_BY_INTENT[intent]
    unknown_fields = sorted(str(key) for key in constraints if str(key) not in allowed_fields)
    if unknown_fields:
        raise ApiError(
            status_code=422,
            code="unknown_constraint",
            message="Request contains constraints that are not available for this intent",
            details={"intent": intent, "fields": unknown_fields},
        )

    _validate_common_constraints(constraints)
    if intent == "stock_compare":
        _validate_stock_compare_constraints(constraints)
    elif intent == "stock_screen":
        _validate_stock_screen_constraints(constraints)
    return request.model_copy(update={"intent": intent, "user_query": query, "constraints": constraints})


def _validate_common_constraints(constraints: dict[str, Any]) -> None:
    for field_name in ("symbol", "sector_query"):
        if field_name in constraints:
            _require_non_empty_string(constraints[field_name], field_name=field_name)

    if "planning_context_hints" in constraints and not isinstance(constraints["planning_context_hints"], dict):
        _invalid_constraint("planning_context_hints", "must be an object")

    if "investment_cycle" in constraints:
        _require_enum(constraints["investment_cycle"], field_name="investment_cycle", allowed=_INVESTMENT_CYCLES)
    if "prefer_methodology" in constraints:
        _require_enum(constraints["prefer_methodology"], field_name="prefer_methodology", allowed=_METHODOLOGIES)
    if "screen_style" in constraints:
        _require_enum(constraints["screen_style"], field_name="screen_style", allowed=_SCREEN_STYLES)
    if "snapshot_types" in constraints:
        snapshots = _require_string_list(constraints["snapshot_types"], field_name="snapshot_types", min_items=1)
        unsupported = sorted(set(snapshots) - _STOCK_SNAPSHOT_TYPES)
        if unsupported:
            _invalid_constraint("snapshot_types", f"contains unsupported values: {', '.join(unsupported)}")
        if len(snapshots) != len(set(snapshots)):
            _invalid_constraint("snapshot_types", "must not contain duplicate values")

    for field_name in (
        "allow_partial_snapshot_types",
        "include_policy_snapshot",
        "skip_enrichment",
        "auto_approve",
        "force_auto_approve",
    ):
        if field_name in constraints and not isinstance(constraints[field_name], bool):
            _invalid_constraint(field_name, "must be a boolean")

    for field_name in ("review_target_run_id", "target_run_id"):
        if field_name in constraints:
            value = _require_non_empty_string(constraints[field_name], field_name=field_name)
            safe_identifier(value, field_name=field_name)
    if "target_run_ids" in constraints:
        run_ids = _require_string_list(constraints["target_run_ids"], field_name="target_run_ids", min_items=1)
        for run_id in run_ids:
            safe_identifier(run_id, field_name="target_run_ids")
        if len(run_ids) != len(set(run_ids)):
            _invalid_constraint("target_run_ids", "must not contain duplicate values")

    integer_ranges = {
        "candidate_pool_size": (1, 100),
        "top_k": (1, 100),
        "watchlist_top_k": (0, 100),
        "enrichment_top_n": (0, 100),
        "min_dimension_sample_size": (1, None),
        "min_valuation_sample_size": (1, None),
        "min_review_pattern_sample_size": (1, None),
        "max_memory_support_age_days": (1, None),
        "min_dimension_memory_support_entity_scope_count": (1, None),
        "min_valuation_memory_support_entity_scope_count": (1, None),
    }
    for field_name, (minimum, maximum) in integer_ranges.items():
        if field_name in constraints:
            _require_integer(constraints[field_name], field_name=field_name, minimum=minimum, maximum=maximum)

    for field_name in ("min_score", "watchlist_min_score"):
        if field_name in constraints:
            _require_finite_number(constraints[field_name], field_name=field_name)
    for field_name in ("min_dimension_average_confidence", "min_valuation_average_confidence"):
        if field_name in constraints:
            value = _require_finite_number(constraints[field_name], field_name=field_name)
            if value < 0.0 or value > 1.0:
                _invalid_constraint(field_name, "must be between 0 and 1")


def _validate_stock_compare_constraints(constraints: dict[str, Any]) -> None:
    structured_symbols: list[str] = []
    if "symbols" in constraints:
        supplied_symbols = _require_string_list(
            constraints["symbols"],
            field_name="symbols",
            min_items=1,
            max_items=20,
        )
        if len(supplied_symbols) != len(set(supplied_symbols)):
            _invalid_constraint("symbols", "must not contain duplicate values")
        structured_symbols.extend(supplied_symbols)
    if "symbol" in constraints:
        structured_symbols.append(_require_non_empty_string(constraints["symbol"], field_name="symbol"))
    if "symbols" in constraints and len(dict.fromkeys(structured_symbols)) < 2:
        _invalid_constraint("symbols", "must identify at least two distinct symbols")


def _validate_stock_screen_constraints(constraints: dict[str, Any]) -> None:
    candidate_pool_size = int(constraints.get("candidate_pool_size", 8))
    for field_name, default in (("top_k", 5), ("watchlist_top_k", 2), ("enrichment_top_n", 2)):
        value = int(constraints.get(field_name, default))
        if value > candidate_pool_size:
            _invalid_constraint(field_name, "must not exceed candidate_pool_size")


def _require_non_empty_string(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid_constraint(field_name, "must be a non-empty string")
    return value.strip()


def _require_string_list(
    value: Any,
    *,
    field_name: str,
    min_items: int = 0,
    max_items: int | None = None,
) -> list[str]:
    if not isinstance(value, list):
        _invalid_constraint(field_name, "must be an array of strings")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            _invalid_constraint(field_name, "must contain only non-empty strings")
        normalized.append(item.strip())
    if len(normalized) < min_items:
        _invalid_constraint(field_name, f"must contain at least {min_items} item(s)")
    if max_items is not None and len(normalized) > max_items:
        _invalid_constraint(field_name, f"must contain at most {max_items} item(s)")
    return normalized


def _require_enum(value: Any, *, field_name: str, allowed: Iterable[str]) -> str:
    normalized = str(value or "").strip()
    allowed_values = set(allowed)
    if not isinstance(value, str) or normalized not in allowed_values:
        _invalid_constraint(field_name, f"must be one of: {', '.join(sorted(allowed_values))}")
    return normalized


def _require_integer(
    value: Any,
    *,
    field_name: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _invalid_constraint(field_name, "must be an integer")
    if minimum is not None and value < minimum:
        _invalid_constraint(field_name, f"must be greater than or equal to {minimum}")
    if maximum is not None and value > maximum:
        _invalid_constraint(field_name, f"must be less than or equal to {maximum}")
    return value


def _require_finite_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _invalid_constraint(field_name, "must be a number")
    normalized = float(value)
    if not math.isfinite(normalized):
        _invalid_constraint(field_name, "must be finite")
    return normalized


def _invalid_constraint(field_name: str, reason: str) -> None:
    raise ApiError(
        status_code=422,
        code="invalid_constraint",
        message=f"Invalid constraint {field_name}: {reason}",
        details={"field": field_name, "reason": reason},
    )


__all__ = ["SUPPORTED_RESEARCH_INTENTS", "safe_identifier", "validate_research_request"]
