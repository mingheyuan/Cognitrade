"""Pinned UZI 3.9.2 pure-method execution inside the Finance DomainPack.

The upstream functions are called without rewriting their formulas.  Agent OS
adds only the outer result envelope, source lineage, explicit-input gates and
missing-data state.  A method is not invoked when doing so would make an
upstream numeric default stand in for a missing real observation or a user
decision that has not been supplied.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Mapping

from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import (
    UZI_SOURCE_COMMIT,
    Uzi392FetcherRuntime,
)


UZI_NATIVE_METHOD_IDS: tuple[str, ...] = (
    "dcf",
    "comps",
    "three_statement",
    "quick_lbo",
    "accretion_dilution",
    "initiation_report",
    "earnings_review",
    "catalyst_calendar",
    "thesis_tracker",
    "morning_note",
    "quantitative_screen",
    "sector_review",
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
)

_FETCHER_BY_SNAPSHOT_TYPE = {
    "market": "0_basic",
    "fundamental": "1_financials",
    "kline": "2_kline",
    "macro": "3_macro",
    "peers": "4_peers",
    "chain": "5_chain",
    "research": "6_research",
    "industry": "7_industry",
    "materials": "8_materials",
    "futures": "9_futures",
    "valuation": "10_valuation",
    "governance": "11_governance",
    "capital_flow": "12_capital_flow",
    "policy": "13_policy",
    "moat": "14_moat",
    "events": "15_events",
    "lhb": "16_lhb",
    "sentiment": "17_sentiment",
    "trap": "18_trap",
    "contests": "19_contests",
    "fund_holders": "fund_holders",
    "similar_stocks": "similar_stocks",
}

_REQUIRED_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "dcf": ("0_basic", "1_financials"),
    "comps": ("0_basic", "1_financials", "similar_stocks"),
    "three_statement": ("1_financials",),
    "quick_lbo": ("1_financials",),
    "accretion_dilution": ("0_basic", "1_financials"),
    "initiation_report": ("0_basic", "1_financials"),
    "earnings_review": ("0_basic", "1_financials"),
    "catalyst_calendar": ("0_basic", "15_events"),
    "thesis_tracker": ("0_basic", "1_financials", "2_kline", "10_valuation"),
    "morning_note": ("0_basic", "2_kline"),
    "quantitative_screen": ("0_basic", "1_financials", "2_kline", "10_valuation"),
    "sector_review": ("0_basic", "7_industry"),
    "ic_memo": ("0_basic", "1_financials"),
    "porter_bcg": ("0_basic", "7_industry"),
    "dd_checklist": ("0_basic", "1_financials"),
    "unit_economics": ("0_basic", "1_financials"),
    "value_creation_plan": ("0_basic", "1_financials"),
    "ai_readiness": ("0_basic", "5_chain", "7_industry", "14_moat", "15_events"),
    "earnings_preview": ("0_basic", "1_financials", "6_research"),
    "model_update": ("0_basic", "1_financials"),
}


def build_uzi_raw(snapshot_rows: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
    _initialize_native_imports()
    market_router = importlib.import_module("lib.market_router")
    target_info = market_router.parse_ticker(symbol)
    target_symbol = target_info.full
    dimensions: dict[str, dict[str, Any]] = {}
    for row in snapshot_rows:
        if not isinstance(row, dict):
            continue
        row_symbol = str(row.get("symbol") or "").strip()
        if not row_symbol:
            continue
        try:
            row_target = market_router.parse_ticker(row_symbol).full
        except (TypeError, ValueError):
            row_target = row_symbol
        if row_target != target_symbol:
            continue
        snapshot_type = str(row.get("snapshot_type") or "").strip()
        fetcher_id = _FETCHER_BY_SNAPSHOT_TYPE.get(snapshot_type)
        if not fetcher_id:
            continue
        facts = row.get("facts") if isinstance(row.get("facts"), dict) else {}
        raw = facts.get("_uzi_raw_result")
        if isinstance(raw, dict):
            native_result = dict(raw)
            native_data = dict(native_result.get("data") or {})
            # Recovery sources are allowed to fill an absent field only.  The
            # exact upstream fetcher object remains authoritative and is never
            # overwritten.  Metadata/provenance keys stay outside ``data``.
            recovered = {
                str(key): value
                for key, value in facts.items()
                if not str(key).startswith("_")
                and key
                not in {
                    "availability",
                    "data_gap",
                    "message",
                    "degrade_reason",
                    "browser_evidence",
                    "fallback_search_results",
                }
            }
            recovered_fields: list[str] = []
            for key, value in recovered.items():
                if (
                    key not in native_data or _missing_native_value(native_data.get(key))
                ) and not _missing_native_value(value):
                    native_data[key] = value
                    recovered_fields.append(key)
            native_result["data"] = native_data
            if recovered_fields:
                native_result["recovery"] = {
                    "field_policy": "fill_missing_only",
                    "fields": recovered_fields,
                    "source_trace": list(row.get("source_trace") or []),
                }
            dimensions[fetcher_id] = native_result
    raw: dict[str, Any] = {
        "ticker": target_info.full,
        "market": target_info.market,
        "dimensions": dimensions,
    }
    holders = (dimensions.get("fund_holders") or {}).get("data") or {}
    if isinstance(holders, dict) and isinstance(holders.get("holders"), list):
        raw["fund_managers"] = list(holders["holders"])
    return raw


def _missing_native_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in {"", "—", "-", "N/A", "None"}
    return isinstance(value, (list, dict)) and not value


def execute_native_method(
    method_id: str,
    snapshot_rows: list[dict[str, Any]],
    symbols: list[str],
    inputs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if method_id not in UZI_NATIVE_METHOD_IDS:
        raise ValueError(f"Unsupported UZI 3.9.2 native method: {method_id}")
    inputs = dict(inputs or {})
    if method_id in {"returns_attribution", "holding_rebalance", "portfolio_rebalance"}:
        return _execute_portfolio_method(method_id, inputs)
    results = [_execute_stock_method(method_id, snapshot_rows, symbol, inputs) for symbol in symbols]
    gaps = list(
        dict.fromkeys(
            f"{row.get('symbol') or 'unknown'}:{gap}"
            for row in results
            for gap in list(row.get("data_gap") or [])
        )
    )
    completed = [row for row in results if isinstance(row.get("result"), dict)]
    return {
        "method_id": method_id,
        "source_commit": UZI_SOURCE_COMMIT,
        "implementation": "vendored_uzi_3_9_2_pure_function",
        "results": results,
        "quality_status": "available" if results and len(completed) == len(results) and not gaps else "degraded" if completed else "data_gap",
        "data_gap": gaps or ([] if results else ["symbols_missing"]),
        "assumptions": _method_assumptions(inputs, method_id),
    }


def _execute_stock_method(
    method_id: str,
    snapshot_rows: list[dict[str, Any]],
    symbol: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    raw = build_uzi_raw(snapshot_rows, symbol)
    dimensions = dict(raw.get("dimensions") or {})
    missing = [dimension for dimension in _REQUIRED_DIMENSIONS.get(method_id, ()) if not _dimension_available(dimensions.get(dimension))]
    if missing:
        return {"symbol": symbol, "result": None, "data_gap": [f"dimension_missing:{item}" for item in missing]}

    _initialize_native_imports()
    features = importlib.import_module("lib.stock_features").extract_features(raw, {})
    assumptions = _method_assumptions(inputs, method_id)
    explicit_gap = _explicit_input_gap(method_id, assumptions, features)
    if explicit_gap:
        return {"symbol": symbol, "result": None, "data_gap": explicit_gap}

    fin = importlib.import_module("lib.fin_models")
    workflow = importlib.import_module("lib.research_workflow")
    deep = importlib.import_module("lib.deep_analysis_methods")
    if method_id == "dcf":
        result = fin.compute_dcf(features, assumptions or None)
    elif method_id == "comps":
        result = fin.build_comps_table(features, _peer_features(dimensions))
    elif method_id == "three_statement":
        result = fin.project_three_stmt(features, assumptions)
    elif method_id == "quick_lbo":
        result = fin.quick_lbo(features, **assumptions)
    elif method_id == "accretion_dilution":
        target = dict(assumptions.pop("target"))
        result = fin.accretion_dilution(features, target, **assumptions)
    elif method_id == "initiation_report":
        result = workflow.build_initiating_coverage(features, raw, fin.compute_dcf(features), fin.build_comps_table(features, _peer_features(dimensions)))
    elif method_id == "earnings_review":
        result = workflow.build_earnings_analysis(features, raw, consensus=assumptions.get("consensus"))
    elif method_id == "catalyst_calendar":
        result = workflow.build_catalyst_calendar(features, raw)
    elif method_id == "thesis_tracker":
        result = workflow.build_thesis_tracker(features, raw, direction=str(assumptions.get("direction") or "long"))
    elif method_id == "morning_note":
        result = workflow.build_morning_note(features, raw)
    elif method_id == "quantitative_screen":
        result = workflow.run_idea_screen(features, style=str(assumptions.get("style") or "quality"))
    elif method_id == "sector_review":
        result = workflow.build_sector_overview(features, raw)
    elif method_id == "ic_memo":
        result = deep.build_ic_memo(features, raw, fin.compute_dcf(features), fin.build_comps_table(features, _peer_features(dimensions)))
    elif method_id == "porter_bcg":
        result = deep.build_competitive_analysis(features, raw)
    elif method_id == "dd_checklist":
        result = deep.build_dd_checklist(features, raw)
    elif method_id == "unit_economics":
        result = deep.build_unit_economics(features, raw)
    elif method_id == "value_creation_plan":
        result = deep.build_value_creation_plan(features, raw)
    elif method_id == "ai_readiness":
        result = importlib.import_module("lib.tier1.ai_readiness").build_ai_readiness(features, raw)
    elif method_id == "earnings_preview":
        result = importlib.import_module("lib.tier1.earnings_preview").build_earnings_preview(features, raw)
    elif method_id == "model_update":
        dcf = fin.compute_dcf(features)
        comps = fin.build_comps_table(features, _peer_features(dimensions))
        result = importlib.import_module("lib.tier1.model_update").build_model_update(features, raw, assumptions, dcf, comps)
    else:
        raise AssertionError(method_id)
    return {"symbol": symbol, "result": result, "data_gap": []}


def _execute_portfolio_method(method_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
    _initialize_native_imports()
    holdings = [dict(row) for row in list(inputs.get("portfolio_holdings") or []) if isinstance(row, dict)]
    gaps: list[str] = []
    if not holdings:
        gaps.append("portfolio_holdings_missing")
    if method_id == "returns_attribution" and any(row.get("return_pct") is None for row in holdings):
        gaps.append("return_pct_missing")
    if method_id == "holding_rebalance" and inputs.get("accept_uzi_native_fee_schedule") is not True:
        gaps.append("accept_uzi_native_fee_schedule_required")
    if method_id == "holding_rebalance" and not isinstance(inputs.get("rebalance_targets"), dict):
        gaps.append("rebalance_targets_missing")
    if method_id == "portfolio_rebalance" and not isinstance(inputs.get("asset_class_targets"), dict):
        gaps.append("asset_class_targets_missing")
    if gaps:
        result = None
    elif method_id == "returns_attribution":
        result = importlib.import_module("lib.tier1.returns_attrib").build_returns_attribution(holdings, inputs.get("benchmark_return"))
    elif method_id == "holding_rebalance":
        threshold = inputs.get("drift_threshold")
        result = importlib.import_module("lib.tier1.rebalance").build_rebalance(
            holdings,
            inputs.get("rebalance_targets"),
            float(5.0 if threshold is None else threshold),
        )
    else:
        result = importlib.import_module("lib.deep_analysis_methods").build_portfolio_rebalance(holdings, inputs.get("asset_class_targets"))
    return {
        "method_id": method_id,
        "source_commit": UZI_SOURCE_COMMIT,
        "implementation": "vendored_uzi_3_9_2_pure_function",
        "results": [{"symbol": None, "result": result, "data_gap": gaps}],
        "quality_status": "available" if result is not None else "data_gap",
        "data_gap": gaps,
        "assumptions": {
            key: inputs.get(key)
            for key in ("benchmark_return", "rebalance_targets", "drift_threshold", "asset_class_targets", "accept_uzi_native_fee_schedule")
            if key in inputs
        },
    }


def _initialize_native_imports() -> None:
    Uzi392FetcherRuntime(workspace_root=Path(__file__).resolve().parents[3] / "workspace")


def _dimension_available(value: object) -> bool:
    if not isinstance(value, dict) or value.get("error"):
        return False
    return isinstance(value.get("data"), dict) and bool(value["data"])


def _method_assumptions(inputs: Mapping[str, Any], method_id: str) -> dict[str, Any]:
    all_assumptions = inputs.get("method_assumptions")
    if isinstance(all_assumptions, dict) and isinstance(all_assumptions.get(method_id), dict):
        return dict(all_assumptions[method_id])
    if method_id == "model_update" and isinstance(inputs.get("model_updates"), dict):
        return dict(inputs["model_updates"])
    return {}


def _explicit_input_gap(method_id: str, assumptions: dict[str, Any], features: dict[str, Any]) -> list[str]:
    required: dict[str, tuple[str, ...]] = {
        "three_statement": (
            "revenue_growth_y1", "revenue_growth_y2", "revenue_growth_y3", "revenue_growth_y4", "revenue_growth_y5",
            "gross_margin", "opex_pct_revenue", "tax_rate", "capex_pct_revenue", "dep_pct_revenue", "nwc_pct_revenue",
        ),
        "quick_lbo": ("entry_multiple", "debt_multiple", "exit_multiple", "hold_years", "ebitda_growth", "interest_rate"),
        "accretion_dilution": ("target", "premium_pct", "cash_pct", "synergies_yi", "new_debt_rate"),
        "thesis_tracker": ("direction",),
        "quantitative_screen": ("style",),
        "model_update": ("__non_empty__",),
    }
    gaps = [f"explicit_assumption_missing:{key}" for key in required.get(method_id, ()) if key == "__non_empty__" and not assumptions or key != "__non_empty__" and key not in assumptions]
    if method_id == "quick_lbo" and not features.get("ebitda_yi"):
        gaps.append("reported_ebitda_missing")
    if method_id == "accretion_dilution" and isinstance(assumptions.get("target"), dict):
        gaps.extend(
            f"explicit_assumption_missing:target.{key}"
            for key in ("ticker", "name", "shares_yi", "price", "eps", "pe", "net_income_yi")
            if assumptions["target"].get(key) is None or assumptions["target"].get(key) == ""
        )
    if method_id == "thesis_tracker" and assumptions.get("direction") not in {"long", "short"}:
        gaps.append("explicit_assumption_invalid:direction")
    if method_id == "quantitative_screen" and assumptions.get("style") not in {"value", "growth", "quality", "short", "gulp"}:
        gaps.append("explicit_assumption_invalid:style")
    if method_id in {"unit_economics", "value_creation_plan"} and assumptions.get("accept_uzi_native_defaults") is not True:
        gaps.append("accept_uzi_native_defaults_required")
    return gaps


def _peer_features(dimensions: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    data = (dimensions.get("similar_stocks") or {}).get("data") or {}
    peers = data.get("similar_stocks") if isinstance(data, dict) else None
    result: list[dict[str, Any]] = []
    for row in peers or []:
        if not isinstance(row, dict):
            continue
        result.append(
            {
                **row,
                "ticker": row.get("ticker") or row.get("code"),
                "pe": row.get("pe") or row.get("pe_ttm"),
                "market_cap_yi": row.get("market_cap_yi") or row.get("market_cap"),
            }
        )
    return result


__all__ = ["UZI_NATIVE_METHOD_IDS", "build_uzi_raw", "execute_native_method"]
