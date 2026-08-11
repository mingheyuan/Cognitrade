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
        "uzi_analyze_stock",
        "quick_scan",
        "uzi_dcf",
        "uzi_panel_only",
        "uzi_scan_trap",
        "uzi_segmental_model",
        "uzi_comps",
        "uzi_lbo",
        "uzi_initiate",
        "uzi_earnings",
        "uzi_catalysts",
        "uzi_thesis",
        "uzi_screen",
        "uzi_dd",
        "uzi_ic_memo",
        "uzi_ai_readiness",
        "uzi_earnings_preview",
        "uzi_model_update",
        "uzi_three_statement_method",
        "uzi_accretion_dilution_method",
        "uzi_morning_note_method",
        "uzi_sector_review_method",
        "uzi_porter_bcg_method",
        "uzi_unit_economics_method",
        "uzi_value_creation_plan_method",
        "uzi_portfolio_rebalance_method",
        "uzi_returns",
        "uzi_rebalance",
        "uzi_portfolio_batch",
        "stock_compare",
        "stock_screen",
        "fund_research",
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
        "sector",
        "lhb",
        "capital_flow",
        "sentiment",
        "events",
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
        "fund_holders",
        "similar_stocks",
    }
)
_METHODOLOGIES = frozenset(
    {
        "comps",
        "dcf",
        "cashflow_resilience",
        "three_statement",
        "lbo",
        "quick_lbo",
        "ma_accretion",
        "accretion_dilution",
        "segmental",
        "initiation_report",
        "earnings_review",
        "catalyst_calendar",
        "thesis_tracker",
        "morning_note",
        "ic_memo",
        "porter_bcg",
        "dd_checklist",
        "unit_economics",
        "value_creation_plan",
        "portfolio_rebalance",
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
_RESEARCH_PROFILES = frozenset({"lite", "medium", "deep"})
_UZI_SCHOOLS = frozenset("ABCDEFGHI")
_UZI_PROFILE_INTENTS = frozenset({
    "stock_check",
    "uzi_analyze_stock",
    "stock_compare",
    "stock_screen",
    "sector_research",
    "review_thesis",
    "uzi_portfolio_batch",
})
_INVESTMENT_CYCLE_INTENTS = frozenset({"fund_research", "stock_screen"})

_COMMON_REQUEST_FIELDS = {"planning_context_hints", "background"}
_STOCK_FIELDS = {
    "symbol",
    "snapshot_types",
    "allow_partial_snapshot_types",
    "investment_cycle",
    "research_profile",
    "school",
    "model_updates",
    "method_assumptions",
    *_COMMON_REQUEST_FIELDS,
}
_CONSTRAINTS_BY_INTENT: dict[str, set[str]] = {
    "stock_check": set(_STOCK_FIELDS),
    "uzi_analyze_stock": set(_STOCK_FIELDS),
    "quick_scan": {"symbol", *_COMMON_REQUEST_FIELDS},
    "uzi_dcf": {"symbol", "method_assumptions", *_COMMON_REQUEST_FIELDS},
    "uzi_panel_only": {"symbol", *_COMMON_REQUEST_FIELDS},
    "uzi_scan_trap": {"symbol", *_COMMON_REQUEST_FIELDS},
    "uzi_segmental_model": {"symbol", "segmental_model", *_COMMON_REQUEST_FIELDS},
    "uzi_comps": {"symbol", *_COMMON_REQUEST_FIELDS},
    "uzi_lbo": {"symbol", "method_assumptions", *_COMMON_REQUEST_FIELDS},
    "uzi_initiate": {"symbol", *_COMMON_REQUEST_FIELDS},
    "uzi_earnings": {"symbol", "method_assumptions", *_COMMON_REQUEST_FIELDS},
    "uzi_catalysts": {"symbol", *_COMMON_REQUEST_FIELDS},
    "uzi_thesis": {"symbol", "method_assumptions", *_COMMON_REQUEST_FIELDS},
    "uzi_screen": {"symbol", "method_assumptions", *_COMMON_REQUEST_FIELDS},
    "uzi_dd": {"symbol", *_COMMON_REQUEST_FIELDS},
    "uzi_ic_memo": {"symbol", *_COMMON_REQUEST_FIELDS},
    "uzi_ai_readiness": {"symbol", *_COMMON_REQUEST_FIELDS},
    "uzi_earnings_preview": {"symbol", *_COMMON_REQUEST_FIELDS},
    "uzi_model_update": {"symbol", "model_updates", *_COMMON_REQUEST_FIELDS},
    "uzi_three_statement_method": {"symbol", "method_assumptions", *_COMMON_REQUEST_FIELDS},
    "uzi_accretion_dilution_method": {"symbol", "method_assumptions", *_COMMON_REQUEST_FIELDS},
    "uzi_morning_note_method": {"symbol", *_COMMON_REQUEST_FIELDS},
    "uzi_sector_review_method": {"symbol", *_COMMON_REQUEST_FIELDS},
    "uzi_porter_bcg_method": {"symbol", *_COMMON_REQUEST_FIELDS},
    "uzi_unit_economics_method": {"symbol", "method_assumptions", *_COMMON_REQUEST_FIELDS},
    "uzi_value_creation_plan_method": {"symbol", "method_assumptions", *_COMMON_REQUEST_FIELDS},
    "uzi_portfolio_rebalance_method": {"portfolio_holdings", "asset_class_targets", *_COMMON_REQUEST_FIELDS},
    "uzi_returns": {"portfolio_holdings", "benchmark_return", *_COMMON_REQUEST_FIELDS},
    "uzi_rebalance": {
        "portfolio_holdings", "rebalance_targets", "drift_threshold",
        "accept_uzi_native_fee_schedule", *_COMMON_REQUEST_FIELDS,
    },
    "uzi_portfolio_batch": {
        "portfolio_holdings", "portfolio_name", "research_profile", "school", *_COMMON_REQUEST_FIELDS,
    },
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
        "research_profile",
        *_COMMON_REQUEST_FIELDS,
    },
    "fund_research": {
        "fund_codes",
        "fund_category",
        "candidate_pool_size",
        "investment_cycle",
        *_COMMON_REQUEST_FIELDS,
    },
    "sector_research": {"sector_query", "include_policy_snapshot", "research_profile", *_COMMON_REQUEST_FIELDS},
    "distill_kol_logic": set(_COMMON_REQUEST_FIELDS),
    "review_thesis": {
        "review_target_run_id",
        "snapshot_types",
        "allow_partial_snapshot_types",
        "include_policy_snapshot",
        "investment_cycle",
        "research_profile",
        *_COMMON_REQUEST_FIELDS,
    },
}
def safe_identifier(value: str, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ApiError(
            status_code=400,
            code="invalid_identifier",
            message=f"{field_name} 包含不支持的字符或长度不符合要求",
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
            message="user_query 不能为空",
            details={"field": "user_query"},
        )
    if len(query) > 20_000:
        raise ApiError(
            status_code=422,
            code="invalid_query",
            message="user_query 不能超过 20000 个字符",
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
            message=f"不支持的研究 intent：{intent}",
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
            message="请求包含当前 intent 不支持的约束字段",
            details={"intent": intent, "fields": unknown_fields},
        )
    if intent in _UZI_PROFILE_INTENTS and not str(constraints.get("research_profile") or "").strip():
        raise ApiError(
            status_code=422,
            code="missing_constraint",
            message="research_profile 为必填字段，可选值为 lite、medium 或 deep",
            details={"intent": intent, "field": "research_profile", "allowed": sorted(_RESEARCH_PROFILES)},
        )
    if intent in _INVESTMENT_CYCLE_INTENTS and not str(constraints.get("investment_cycle") or "").strip():
        raise ApiError(
            status_code=422,
            code="missing_constraint",
            message="investment_cycle 为必填字段，可选值为 short_term、mid_term 或 long_term",
            details={"intent": intent, "field": "investment_cycle", "allowed": sorted(_INVESTMENT_CYCLES)},
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
    elif intent in {"uzi_returns", "uzi_rebalance", "uzi_portfolio_rebalance_method", "uzi_portfolio_batch"}:
        _validate_uzi_portfolio_method_constraints(
            constraints,
            allow_missing_weights=intent == "uzi_portfolio_batch",
        )
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
    if "background" in constraints and not isinstance(constraints["background"], bool):
        _invalid_constraint("background", "must be a boolean")

    if "investment_cycle" in constraints:
        _require_enum(constraints["investment_cycle"], field_name="investment_cycle", allowed=_INVESTMENT_CYCLES)
    if "research_profile" in constraints:
        _require_enum(constraints["research_profile"], field_name="research_profile", allowed=_RESEARCH_PROFILES)
    if "school" in constraints:
        school = _require_non_empty_string(constraints["school"], field_name="school").upper()
        if school not in _UZI_SCHOOLS:
            _invalid_constraint("school", "must be one of A, B, C, D, E, F, G, H or I")
        constraints["school"] = school
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
    if "method_assumptions" in constraints:
        _validate_method_assumptions(constraints["method_assumptions"])

    for field_name in (
        "allow_partial_snapshot_types",
        "include_policy_snapshot",
        "skip_enrichment",
        "auto_approve",
        "force_auto_approve",
        "accept_uzi_native_fee_schedule",
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
            max_items=4,
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


def _validate_uzi_portfolio_method_constraints(
    constraints: dict[str, Any],
    *,
    allow_missing_weights: bool = False,
) -> None:
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
        supplied_weight = holding.get("weight")
        if supplied_weight is None and allow_missing_weights:
            weight = None
        else:
            weight = _require_finite_number(
                supplied_weight,
                field_name=f"portfolio_holdings.{index}.weight",
            )
        if weight is not None:
            if weight < 0:
                _invalid_constraint(f"portfolio_holdings.{index}.weight", "must be non-negative")
            if allow_missing_weights and weight > 1.0:
                # UZI --portfolio parses CSV weights above 1 as percentages
                # before applying its native remaining-weight normalization.
                weight = weight / 100.0
                holding["weight"] = weight
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
        for field_name in ("name", "industry", "market", "asset_class"):
            if holding.get(field_name) is not None:
                _require_non_empty_string(
                    holding[field_name],
                    field_name=f"portfolio_holdings.{index}.{field_name}",
                )
    if len(tickers) != len(set(tickers)):
        _invalid_constraint("portfolio_holdings", "must not contain duplicate tickers")
    if total_weight <= 0 and not allow_missing_weights:
        _invalid_constraint("portfolio_holdings", "weight total must be positive")

    if "portfolio_name" in constraints:
        _require_non_empty_string(constraints["portfolio_name"], field_name="portfolio_name")

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

    if "asset_class_targets" in constraints:
        targets = constraints["asset_class_targets"]
        if not isinstance(targets, dict) or not targets:
            _invalid_constraint("asset_class_targets", "must be a non-empty object")
        classes = {
            str(holding.get("asset_class") or "").strip()
            for holding in holdings
            if str(holding.get("asset_class") or "").strip()
        }
        if len(classes) != len({str(key).strip() for key in targets}):
            _invalid_constraint("asset_class_targets", "must contain exactly one target for each observed asset class")
        if set(map(str, targets)) != classes:
            _invalid_constraint("asset_class_targets", "must match the asset_class values in portfolio_holdings")
        total_target = 0.0
        for asset_class, target in targets.items():
            number = _require_finite_number(target, field_name=f"asset_class_targets.{asset_class}")
            if number < 0:
                _invalid_constraint(f"asset_class_targets.{asset_class}", "must be non-negative")
            total_target += number
        if total_target <= 0:
            _invalid_constraint("asset_class_targets", "target total must be positive")


def _validate_method_assumptions(value: Any) -> None:
    if not isinstance(value, dict) or not value:
        _invalid_constraint("method_assumptions", "must be a non-empty object")
    allowed_methods = {
        "dcf",
        "three_statement",
        "quick_lbo",
        "accretion_dilution",
        "earnings_review",
        "thesis_tracker",
        "quantitative_screen",
        "value_creation_plan",
        "unit_economics",
    }
    unknown = sorted(str(key) for key in value if str(key) not in allowed_methods)
    if unknown:
        _invalid_constraint("method_assumptions", f"contains unsupported methods: {', '.join(unknown)}")

    def visit(item: Any, path: str) -> None:
        if item is None or isinstance(item, str):
            return
        if isinstance(item, bool):
            return
        if isinstance(item, (int, float)):
            _require_finite_number(item, field_name=path)
            return
        if isinstance(item, list):
            if not item:
                _invalid_constraint(path, "must not contain an empty array")
            for index, nested in enumerate(item):
                visit(nested, f"{path}.{index}")
            return
        if isinstance(item, dict):
            if not item:
                _invalid_constraint(path, "must not contain an empty object")
            for key, nested in item.items():
                if not str(key).strip():
                    _invalid_constraint(path, "must not contain an empty key")
                visit(nested, f"{path}.{key}")
            return
        _invalid_constraint(path, "contains an unsupported value type")

    for method, assumptions in value.items():
        if not isinstance(assumptions, dict) or not assumptions:
            _invalid_constraint(f"method_assumptions.{method}", "must be a non-empty object")
        visit(assumptions, f"method_assumptions.{method}")
        _validate_method_assumption_contract(str(method), assumptions)


def _validate_method_assumption_contract(method: str, assumptions: dict[str, Any]) -> None:
    if method == "dcf":
        allowed = {
            "stage1_growth", "stage2_growth", "stage1_years", "stage2_years",
            "terminal_g", "beta", "tax", "target_debt_ratio",
        }
        unknown = sorted(set(map(str, assumptions)) - allowed)
        if unknown:
            _invalid_constraint("method_assumptions.dcf", f"contains unsupported fields: {', '.join(unknown)}")
        for key, item in assumptions.items():
            number = _require_finite_number(item, field_name=f"method_assumptions.dcf.{key}")
            if key in {"stage1_years", "stage2_years"} and (number <= 0 or number != int(number)):
                _invalid_constraint(f"method_assumptions.dcf.{key}", "must be a positive integer")
        return

    if method == "earnings_review":
        if set(assumptions) != {"consensus"} or not isinstance(assumptions.get("consensus"), dict):
            _invalid_constraint("method_assumptions.earnings_review", "must contain exactly a consensus object")
        return

    if method == "thesis_tracker":
        if set(assumptions) != {"direction"}:
            _invalid_constraint("method_assumptions.thesis_tracker", "must contain exactly direction")
        _require_enum(assumptions["direction"], field_name="method_assumptions.thesis_tracker.direction", allowed={"long", "short"})
        return

    if method == "quantitative_screen":
        if set(assumptions) != {"style"}:
            _invalid_constraint("method_assumptions.quantitative_screen", "must contain exactly style")
        _require_enum(assumptions["style"], field_name="method_assumptions.quantitative_screen.style", allowed=_SCREEN_STYLES)
        return

    if method in {"unit_economics", "value_creation_plan"}:
        if set(assumptions) != {"accept_uzi_native_defaults"} or assumptions.get("accept_uzi_native_defaults") is not True:
            _invalid_constraint(
                f"method_assumptions.{method}",
                "must explicitly set accept_uzi_native_defaults=true",
            )
        return

    required_keys = {
        "three_statement": {
            "revenue_growth_y1", "revenue_growth_y2", "revenue_growth_y3",
            "revenue_growth_y4", "revenue_growth_y5", "gross_margin",
            "opex_pct_revenue", "tax_rate", "capex_pct_revenue",
            "dep_pct_revenue", "nwc_pct_revenue",
        },
        "quick_lbo": {
            "entry_multiple", "debt_multiple", "exit_multiple",
            "hold_years", "ebitda_growth", "interest_rate",
        },
        "accretion_dilution": {
            "target", "premium_pct", "cash_pct", "synergies_yi", "new_debt_rate",
        },
    }[method]
    actual_keys = {str(key) for key in assumptions}
    missing = sorted(required_keys - actual_keys)
    unknown = sorted(actual_keys - required_keys)
    if missing:
        _invalid_constraint(
            f"method_assumptions.{method}",
            f"missing required fields: {', '.join(missing)}",
        )
    if unknown:
        _invalid_constraint(
            f"method_assumptions.{method}",
            f"contains unsupported fields: {', '.join(unknown)}",
        )

    if method == "three_statement":
        for key in required_keys:
            number = _require_finite_number(assumptions[key], field_name=f"method_assumptions.three_statement.{key}")
            if key.startswith("revenue_growth_") and number <= -1:
                _invalid_constraint(f"method_assumptions.three_statement.{key}", "must be greater than -1")
        return

    if method == "quick_lbo":
        numbers = {
            key: _require_finite_number(
                assumptions[key], field_name=f"method_assumptions.quick_lbo.{key}"
            )
            for key in required_keys
        }
        if numbers["entry_multiple"] <= 0 or numbers["exit_multiple"] <= 0:
            _invalid_constraint(
                "method_assumptions.quick_lbo",
                "entry_multiple and exit_multiple must be positive",
            )
        if numbers["debt_multiple"] < 0:
            _invalid_constraint(
                "method_assumptions.quick_lbo.debt_multiple", "must be non-negative"
            )
        if numbers["ebitda_growth"] <= -1:
            _invalid_constraint(
                "method_assumptions.quick_lbo.ebitda_growth",
                "must be greater than -1",
            )
        hold_years = numbers["hold_years"]
        if hold_years <= 0 or hold_years != int(hold_years):
            _invalid_constraint(
                "method_assumptions.quick_lbo.hold_years",
                "must be a positive integer",
            )
        return

    if method == "accretion_dilution":
        if not isinstance(assumptions["target"], dict) or not assumptions["target"]:
            _invalid_constraint("method_assumptions.accretion_dilution.target", "must be a non-empty object")
        target = assumptions["target"]
        target_required = {"ticker", "name", "shares_yi", "price", "eps", "pe", "net_income_yi"}
        target_actual = {str(key) for key in target}
        target_missing = sorted(target_required - target_actual)
        target_unknown = sorted(target_actual - target_required)
        if target_missing:
            _invalid_constraint(
                "method_assumptions.accretion_dilution.target",
                f"missing required fields: {', '.join(target_missing)}",
            )
        if target_unknown:
            _invalid_constraint(
                "method_assumptions.accretion_dilution.target",
                f"contains unsupported fields: {', '.join(target_unknown)}",
            )
        _require_non_empty_string(target["ticker"], field_name="method_assumptions.accretion_dilution.target.ticker")
        _require_non_empty_string(target["name"], field_name="method_assumptions.accretion_dilution.target.name")
        for key in target_required - {"ticker", "name"}:
            number = _require_finite_number(
                target[key], field_name=f"method_assumptions.accretion_dilution.target.{key}"
            )
            if number < 0:
                _invalid_constraint(
                    f"method_assumptions.accretion_dilution.target.{key}", "must be non-negative"
                )
        numbers = {
            key: _require_finite_number(
                assumptions[key], field_name=f"method_assumptions.accretion_dilution.{key}"
            )
            for key in required_keys - {"target"}
        }
        if not 0 <= numbers["cash_pct"] <= 1:
            _invalid_constraint("method_assumptions.accretion_dilution.cash_pct", "must be between 0 and 1")
        return


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
    localized_reason = _constraint_reason_zh(reason)
    raise ApiError(
        status_code=422,
        code="invalid_constraint",
        message=f"约束字段 {field_name} 无效：{localized_reason}",
        details={"field": field_name, "reason": localized_reason},
    )


def _constraint_reason_zh(reason: str) -> str:
    fixed = {
        "must be an object": "必须是对象",
        "must be a boolean": "必须是布尔值",
        "must not contain duplicate values": "不能包含重复值",
        "must be between 0 and 1": "必须在 0 到 1 之间",
        "must identify at least two distinct symbols": "必须至少包含两个不同的 symbol",
        "must not exceed candidate_pool_size": "不能超过 candidate_pool_size",
        "must contain six-digit fund codes": "必须包含六位基金代码",
        "must not exceed 20 for fund research": "基金研究时不能超过 20",
        "must be a non-empty object": "必须是非空对象",
        "must be a non-empty array": "必须是非空数组",
        "must contain at most 100 items": "最多只能包含 100 项",
        "must be non-negative": "不能为负数",
        "must not contain duplicate tickers": "不能包含重复的 ticker",
        "weight total must be positive": "weight 总和必须大于 0",
        "must be between 0 and 100": "必须在 0 到 100 之间",
        "must contain exactly one target for each holding": "每个 holding 必须且只能有一个 target",
        "target total must be positive": "target 总和必须大于 0",
        "must contain exactly one target for each observed asset class": "每个已观测的 asset_class 必须且只能有一个 target",
        "must match the asset_class values in portfolio_holdings": "必须与 portfolio_holdings 中的 asset_class 值一致",
        "must not contain an empty array": "不能包含空数组",
        "must not contain an empty object": "不能包含空对象",
        "must not contain an empty key": "不能包含空键名",
        "contains an unsupported value type": "包含不支持的值类型",
        "must be a positive integer": "必须是正整数",
        "must contain exactly a consensus object": "必须且只能包含 consensus 对象",
        "must contain exactly direction": "必须且只能包含 direction",
        "must contain exactly style": "必须且只能包含 style",
        "must explicitly set accept_uzi_native_defaults=true": "必须明确设置 accept_uzi_native_defaults=true",
        "must be greater than -1": "必须大于 -1",
        "entry_multiple and exit_multiple must be positive": "entry_multiple 和 exit_multiple 必须大于 0",
        "must be a non-empty string": "必须是非空字符串",
        "must be an array of strings": "必须是字符串数组",
        "must contain only non-empty strings": "只能包含非空字符串",
        "must be an integer": "必须是整数",
        "must be a number": "必须是数字",
        "must be finite": "必须是有限数值",
    }
    normalized = str(reason or "").strip()
    if normalized in fixed:
        return fixed[normalized]
    replacements = (
        (r"^contains unsupported values: (.+)$", r"包含不支持的值：\1"),
        (r"^contains unsupported fields: (.+)$", r"包含不支持的字段：\1"),
        (r"^contains unsupported methods: (.+)$", r"包含不支持的方法：\1"),
        (r"^missing required fields: (.+)$", r"缺少必填字段：\1"),
        (r"^item (\d+) must be an object$", r"第 \1 项必须是对象"),
        (r"^must contain at least (\d+) item\(s\)$", r"至少必须包含 \1 项"),
        (r"^must contain at most (\d+) item\(s\)$", r"最多只能包含 \1 项"),
        (r"^must be one of: (.+)$", r"必须是以下值之一：\1"),
        (r"^must be greater than or equal to (.+)$", r"必须大于或等于 \1"),
        (r"^must be less than or equal to (.+)$", r"必须小于或等于 \1"),
    )
    for pattern, replacement in replacements:
        if re.fullmatch(pattern, normalized):
            return re.sub(pattern, replacement, normalized)
    return "字段值不符合要求"


__all__ = ["SUPPORTED_RESEARCH_INTENTS", "safe_identifier", "validate_research_request"]
