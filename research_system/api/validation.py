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
        "fund_research",
        "portfolio_research",
        "sector_research",
        "distill_kol_logic",
        "review_thesis",
    }
)

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_INVESTMENT_CYCLES = frozenset({"short_term", "mid_term", "long_term"})
_FUND_CATEGORIES = frozenset({"全部", "股票型", "混合型", "债券型", "指数型", "ETF", "LOF", "QDII", "FOF"})
_STOCK_SNAPSHOT_TYPES = frozenset(
    {
        "market",
        "fundamental",
        "notice",
        "lhb",
        "capital_flow",
        "sentiment",
        "events",
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
    }
)
_METHODOLOGIES = frozenset(
    {
        "comps",
        "dcf",
        "cashflow_resilience",
        "three_statement",
        "lbo",
        "ma_accretion",
        "segmental",
        "ai_readiness",
        "earnings_preview",
        "model_update",
        "returns_attribution",
        "holding_rebalance",
    }
)
_RESEARCH_METHODS = frozenset(
    {
        *_METHODOLOGIES,
        "panel",
        "risk",
        "report",
    }
)
_SCREEN_STYLES = frozenset({"value", "growth", "quality", "gulp", "short"})
_RESEARCH_PROFILES = frozenset({"quick", "lite", "medium", "deep"})

_PLANNING_FIELDS = {"planning_context_hints"}
_STOCK_FIELDS = {
    "symbol",
    "snapshot_types",
    "allow_partial_snapshot_types",
    "investment_cycle",
    "research_profile",
    "requested_methods",
    "model_updates",
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
    "fund_research": {
        "fund_codes",
        "fund_category",
        "candidate_pool_size",
        "investment_cycle",
        *_PLANNING_FIELDS,
    },
    "portfolio_research": {
        "portfolio_holdings",
        "benchmark_return",
        "rebalance_targets",
        "drift_threshold",
        "fee_model",
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


def validate_research_request(
    request: RequestEnvelope,
    *,
    supported_intents: Iterable[str] | None = None,
    supported_methods: Iterable[str] | None = None,
    supported_snapshot_types: Iterable[str] | None = None,
) -> RequestEnvelope:
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
    allowed_intents = frozenset(
        str(item).strip()
        for item in (
            supported_intents
            if supported_intents is not None
            else SUPPORTED_RESEARCH_INTENTS
        )
        if str(item).strip()
    )
    is_core_intent = bool(intent and intent.startswith("core."))
    if (
        intent is not None
        and not intent.startswith("domain:")
        and not is_core_intent
        and intent not in allowed_intents
    ):
        raise ApiError(
            status_code=422,
            code="invalid_intent",
            message=f"Unsupported research intent: {intent}",
            details={"intent": intent, "supported_intents": sorted(allowed_intents)},
        )

    constraints = dict(request.constraints or {})
    if intent is None or intent.startswith("domain:") or is_core_intent:
        return request.model_copy(update={"intent": intent, "user_query": query, "constraints": constraints})

    allowed_fields = _CONSTRAINTS_BY_INTENT.get(intent)
    if allowed_fields is None:
        # Constraint ownership belongs to an installed Pack. Core only performs
        # generic shape validation for an intent it does not define.
        return request.model_copy(update={"intent": intent, "user_query": query, "constraints": constraints})
    unknown_fields = sorted(str(key) for key in constraints if str(key) not in allowed_fields)
    if unknown_fields:
        raise ApiError(
            status_code=422,
            code="unknown_constraint",
            message="Request contains constraints that are not available for this intent",
            details={"intent": intent, "fields": unknown_fields},
        )

    _validate_common_constraints(
        constraints,
        supported_methods=supported_methods,
        supported_snapshot_types=supported_snapshot_types,
    )
    if intent == "stock_compare":
        _validate_stock_compare_constraints(constraints)
    elif intent == "stock_screen":
        _validate_stock_screen_constraints(constraints)
    elif intent == "fund_research":
        _validate_fund_research_constraints(constraints)
    elif intent == "portfolio_research":
        _validate_portfolio_research_constraints(constraints)
    return request.model_copy(update={"intent": intent, "user_query": query, "constraints": constraints})


def _validate_common_constraints(
    constraints: dict[str, Any],
    *,
    supported_methods: Iterable[str] | None = None,
    supported_snapshot_types: Iterable[str] | None = None,
) -> None:
    for field_name in ("symbol", "sector_query"):
        if field_name in constraints:
            _require_non_empty_string(constraints[field_name], field_name=field_name)

    if "planning_context_hints" in constraints and not isinstance(constraints["planning_context_hints"], dict):
        _invalid_constraint("planning_context_hints", "must be an object")

    if "investment_cycle" in constraints:
        _require_enum(constraints["investment_cycle"], field_name="investment_cycle", allowed=_INVESTMENT_CYCLES)
    if "research_profile" in constraints:
        _require_enum(constraints["research_profile"], field_name="research_profile", allowed=_RESEARCH_PROFILES)
    if "requested_methods" in constraints:
        methods = _require_string_list(constraints["requested_methods"], field_name="requested_methods", min_items=1)
        allowed_methods = frozenset(
            str(item).strip().lower()
            for item in (
                supported_methods
                if supported_methods is not None
                else _RESEARCH_METHODS
            )
            if str(item).strip()
        )
        unsupported = sorted(set(methods) - allowed_methods)
        if unsupported:
            _invalid_constraint(
                "requested_methods",
                f"contains unsupported values: {', '.join(unsupported)}",
            )
        if len(methods) != len(set(methods)):
            _invalid_constraint("requested_methods", "must not contain duplicate values")
    if "prefer_methodology" in constraints:
        allowed_methods = frozenset(
            str(item).strip().lower()
            for item in (supported_methods if supported_methods is not None else _RESEARCH_METHODS)
            if str(item).strip()
        )
        _require_enum(constraints["prefer_methodology"], field_name="prefer_methodology", allowed=allowed_methods)
    if "screen_style" in constraints:
        _require_enum(constraints["screen_style"], field_name="screen_style", allowed=_SCREEN_STYLES)
    if "snapshot_types" in constraints:
        snapshots = _require_string_list(constraints["snapshot_types"], field_name="snapshot_types", min_items=1)
        allowed_snapshot_types = frozenset(
            str(item).strip().lower()
            for item in (
                supported_snapshot_types
                if supported_snapshot_types is not None
                else _STOCK_SNAPSHOT_TYPES
            )
            if str(item).strip()
        )
        unsupported = sorted(set(snapshots) - allowed_snapshot_types)
        if unsupported:
            _invalid_constraint("snapshot_types", f"contains unsupported values: {', '.join(unsupported)}")
        if len(snapshots) != len(set(snapshots)):
            _invalid_constraint("snapshot_types", "must not contain duplicate values")
    if "model_updates" in constraints:
        _validate_model_updates(constraints["model_updates"])

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


def _validate_fund_research_constraints(constraints: dict[str, Any]) -> None:
    if "fund_category" in constraints:
        _require_enum(
            constraints["fund_category"],
            field_name="fund_category",
            allowed=_FUND_CATEGORIES,
        )
    if "fund_codes" in constraints:
        codes = _require_string_list(
            constraints["fund_codes"],
            field_name="fund_codes",
            min_items=1,
            max_items=20,
        )
        if len(codes) != len(set(codes)):
            _invalid_constraint("fund_codes", "must not contain duplicate values")
        invalid = [code for code in codes if not re.fullmatch(r"\d{6}", code)]
        if invalid:
            _invalid_constraint("fund_codes", "must contain six-digit fund codes")
    if int(constraints.get("candidate_pool_size", 8)) > 20:
        _invalid_constraint("candidate_pool_size", "must not exceed 20 for fund research")


def _validate_model_updates(value: Any) -> None:
    if not isinstance(value, dict) or not value:
        _invalid_constraint("model_updates", "must be a non-empty object")
    allowed = {
        "rev_growth",
        "gross_margin",
        "net_margin",
        "capex_pct",
        "stage1_growth",
        "terminal_g",
        "beta",
        "target_pe",
        "target_price",
    }
    unknown = sorted(str(key) for key in value if str(key) not in allowed)
    if unknown:
        _invalid_constraint("model_updates", f"contains unsupported values: {', '.join(unknown)}")
    for key, item in value.items():
        _require_finite_number(item, field_name=f"model_updates.{key}")


def _validate_portfolio_research_constraints(constraints: dict[str, Any]) -> None:
    holdings = constraints.get("portfolio_holdings")
    if not isinstance(holdings, list) or not holdings:
        _invalid_constraint("portfolio_holdings", "must be a non-empty array")
    if len(holdings) > 100:
        _invalid_constraint("portfolio_holdings", "must contain at most 100 items")
    tickers: list[str] = []
    total_weight = 0.0
    for index, holding in enumerate(holdings):
        if not isinstance(holding, dict):
            _invalid_constraint("portfolio_holdings", f"item {index} must be an object")
        ticker = _require_non_empty_string(
            holding.get("ticker") or holding.get("symbol"),
            field_name=f"portfolio_holdings.{index}.ticker",
        )
        tickers.append(ticker)
        weight = _require_finite_number(
            holding.get("weight"),
            field_name=f"portfolio_holdings.{index}.weight",
        )
        if weight < 0:
            _invalid_constraint(f"portfolio_holdings.{index}.weight", "must be non-negative")
        total_weight += weight
        for field_name in ("return_pct", "value", "market_value", "quantity", "price"):
            if holding.get(field_name) is None:
                continue
            number = _require_finite_number(
                holding[field_name],
                field_name=f"portfolio_holdings.{index}.{field_name}",
            )
            if field_name in {"value", "market_value", "quantity", "price"} and number < 0:
                _invalid_constraint(f"portfolio_holdings.{index}.{field_name}", "must be non-negative")
        for field_name in ("name", "industry", "market"):
            if holding.get(field_name) is not None:
                _require_non_empty_string(
                    holding[field_name],
                    field_name=f"portfolio_holdings.{index}.{field_name}",
                )
    if len(tickers) != len(set(tickers)):
        _invalid_constraint("portfolio_holdings", "must not contain duplicate tickers")
    if total_weight <= 0:
        _invalid_constraint("portfolio_holdings", "weight total must be positive")

    if "benchmark_return" in constraints:
        _require_finite_number(constraints["benchmark_return"], field_name="benchmark_return")
    if "drift_threshold" in constraints:
        threshold = _require_finite_number(constraints["drift_threshold"], field_name="drift_threshold")
        if threshold < 0 or threshold > 100:
            _invalid_constraint("drift_threshold", "must be between 0 and 100")

    if "rebalance_targets" in constraints:
        targets = constraints["rebalance_targets"]
        if not isinstance(targets, dict) or not targets:
            _invalid_constraint("rebalance_targets", "must be a non-empty object")
        if set(map(str, targets)) != set(tickers):
            _invalid_constraint("rebalance_targets", "must contain exactly one target for each holding")
        total_target = 0.0
        for ticker, target in targets.items():
            number = _require_finite_number(target, field_name=f"rebalance_targets.{ticker}")
            if number < 0:
                _invalid_constraint(f"rebalance_targets.{ticker}", "must be non-negative")
            total_target += number
        if total_target <= 0:
            _invalid_constraint("rebalance_targets", "target total must be positive")

    if "fee_model" in constraints:
        fee_model = constraints["fee_model"]
        if not isinstance(fee_model, dict) or not fee_model:
            _invalid_constraint("fee_model", "must be a non-empty object")
        for market, raw_spec in fee_model.items():
            if not isinstance(raw_spec, dict) or not raw_spec:
                _invalid_constraint(f"fee_model.{market}", "must be a non-empty object")
            unknown = sorted(
                str(key)
                for key in raw_spec
                if str(key) not in {"commission_rate", "buy_stamp_rate", "sell_stamp_rate"}
            )
            if unknown:
                _invalid_constraint(f"fee_model.{market}", f"contains unsupported fields: {', '.join(unknown)}")
            for key, rate in raw_spec.items():
                number = _require_finite_number(rate, field_name=f"fee_model.{market}.{key}")
                if number < 0 or number > 1:
                    _invalid_constraint(f"fee_model.{market}.{key}", "must be between 0 and 1")


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
