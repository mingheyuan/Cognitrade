from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any, Callable


UZI_METHOD_PRODUCT_VERSION = "3.9.2-adapted-products-v1"

METHOD_ARTIFACT_TYPES: dict[str, str] = {
    "cashflow_resilience": "financial_analysis",
    "three_statement": "three_statement_model",
    "quick_lbo": "quick_lbo",
    "accretion_dilution": "accretion_dilution",
    "segmental": "segmental_model",
    "initiation_report": "initiation_report",
    "earnings_review": "earnings_review",
    "catalyst_calendar": "catalyst_calendar",
    "thesis_tracker": "thesis_tracker",
    "morning_note": "morning_note",
    "ic_memo": "ic_memo",
    "porter_bcg": "porter_bcg",
    "dd_checklist": "dd_checklist",
    "unit_economics": "unit_economics",
    "value_creation_plan": "value_creation_plan",
}


def build_method_product(
    method_id: str,
    snapshot_rows: list[dict[str, Any]],
    symbols: list[str],
    inputs: Mapping[str, Any] | None = None,
    valuation: Mapping[str, Any] | None = None,
    risk_scan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    builders: dict[str, Callable[..., dict[str, Any]]] = {
        "cashflow_resilience": _cashflow_resilience,
        "three_statement": _three_statement,
        "quick_lbo": _quick_lbo,
        "accretion_dilution": _accretion_dilution,
        "segmental": _segmental_model,
        "initiation_report": _initiation_report,
        "earnings_review": _earnings_review,
        "catalyst_calendar": _catalyst_calendar,
        "thesis_tracker": _thesis_tracker,
        "morning_note": _morning_note,
        "ic_memo": _ic_memo,
        "porter_bcg": _porter_bcg,
        "dd_checklist": _dd_checklist,
        "unit_economics": _unit_economics,
        "value_creation_plan": _value_creation_plan,
    }
    if method_id not in builders:
        raise ValueError(f"Unsupported UZI method product: {method_id}")
    results = [
        builders[method_id](
            _snapshot_map(snapshot_rows, symbol),
            symbol,
            dict(inputs or {}),
            _valuation_for_symbol(valuation, symbol),
            dict(risk_scan or {}),
        )
        for symbol in symbols
    ]
    gaps = _unique(
        f"{result.get('symbol') or 'unknown'}:{gap}"
        for result in results
        for gap in list(result.get("data_gap") or [])
    )
    assumptions = _unique(
        f"{result.get('symbol') or 'unknown'}:{flag}"
        for result in results
        for flag in list(result.get("assumption_flags") or [])
    )
    statuses = [str(result.get("quality_status") or "data_gap") for result in results]
    if not results or all(status == "data_gap" for status in statuses):
        quality = "data_gap"
    elif gaps:
        quality = "degraded"
    elif assumptions or any(status == "assumption_only" for status in statuses):
        quality = "assumption_only"
    else:
        quality = "available"
    return {
        "methodology_version": UZI_METHOD_PRODUCT_VERSION,
        "method": method_id,
        "artifact_type": METHOD_ARTIFACT_TYPES[method_id],
        "results": results,
        "quality_status": quality,
        "data_gap": gaps or ([] if results else ["symbols_missing"]),
        "assumption_flags": assumptions,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _cashflow_resilience(
    snapshots: dict[str, dict[str, Any]],
    symbol: str,
    _inputs: dict[str, Any],
    _valuation: dict[str, Any],
    _risk: dict[str, Any],
) -> dict[str, Any]:
    """Describe cash-flow resilience from reported facts without stress-test defaults."""

    fundamental = _facts(snapshots.get("fundamental"))
    operating_cash_flow = _find_number(
        fundamental,
        ("operating_cash_flow", "operating_cash_flow_yi", "act_cash_flow_net", "经营活动产生的现金流量净额", "经营活动现金流净额"),
    )
    free_cash_flow = _find_number(
        fundamental,
        ("free_cash_flow", "free_cash_flow_yi", "fcf_latest_yi", "自由现金流"),
    )
    net_profit = _find_number(
        fundamental,
        ("net_profit", "net_profit_latest_yi", "parent_holder_net_profit", "归母净利润", "净利润"),
    )
    revenue = _find_number(
        fundamental,
        ("revenue", "revenue_latest_yi", "operating_income_total", "营业总收入", "营业收入"),
    )
    total_assets = _find_number(fundamental, ("total_assets", "total_assets_yi", "assets_total", "资产总计"))
    total_liabilities = _find_number(
        fundamental,
        ("total_liabilities", "total_liabilities_yi", "负债合计"),
    )
    total_debt = _find_number(
        fundamental,
        ("total_debt", "total_debt_yi", "interest_bearing_debt", "有息负债", "债务合计"),
    )
    cash = _find_number(
        fundamental,
        ("cash", "cash_yi", "cash_and_equivalents", "货币资金", "现金及现金等价物"),
    )

    ocf_to_net_profit = (
        operating_cash_flow / net_profit
        if operating_cash_flow is not None and net_profit not in (None, 0)
        else None
    )
    ocf_margin = (
        operating_cash_flow / revenue
        if operating_cash_flow is not None and revenue not in (None, 0)
        else None
    )
    liabilities_to_assets = (
        total_liabilities / total_assets
        if total_liabilities is not None and total_assets not in (None, 0)
        else None
    )
    cash_to_debt = cash / total_debt if cash is not None and total_debt not in (None, 0) else None

    gaps = []
    for field, value in (
        ("operating_cash_flow_missing", operating_cash_flow),
        ("net_profit_missing", net_profit),
        ("revenue_missing", revenue),
        ("total_assets_missing", total_assets),
        ("total_liabilities_missing", total_liabilities),
        ("total_debt_missing", total_debt),
        ("cash_missing", cash),
        ("reported_free_cash_flow_missing", free_cash_flow),
    ):
        if value is None:
            gaps.append(field)

    return _result(
        symbol,
        observed={
            "operating_cash_flow": operating_cash_flow,
            "reported_free_cash_flow": free_cash_flow,
            "net_profit": net_profit,
            "revenue": revenue,
            "total_assets": total_assets,
            "total_liabilities": total_liabilities,
            "total_debt": total_debt,
            "cash": cash,
        },
        derived={
            "operating_cash_flow_to_net_profit": ocf_to_net_profit,
            "operating_cash_flow_margin": ocf_margin,
            "liabilities_to_assets": liabilities_to_assets,
            "cash_to_debt": cash_to_debt,
        },
        gaps=gaps,
        methodology={
            "calculation_basis": "reported_financial_facts_only",
            "composite_score": None,
            "stress_scenario": None,
        },
    )


def _three_statement(
    snapshots: dict[str, dict[str, Any]],
    symbol: str,
    inputs: dict[str, Any],
    _valuation: dict[str, Any],
    _risk: dict[str, Any],
) -> dict[str, Any]:
    financials = _section(snapshots, "fundamental", "financial_abstract")
    revenue = _find_number(financials, ("revenue", "营业收入", "营业总收入"))
    net_margin = _find_number(financials, ("net_margin", "销售净利率"))
    assets = _find_number(financials, ("total_assets", "资产总计"))
    liabilities = _find_number(financials, ("total_liabilities", "负债合计"))
    ocf = _find_number(financials, ("operating_cash_flow", "经营活动产生的现金流量净额"))
    gaps = []
    for key, value in (("revenue_missing", revenue), ("net_margin_missing", net_margin), ("total_assets_missing", assets), ("total_liabilities_missing", liabilities), ("operating_cash_flow_missing", ocf)):
        if value is None:
            gaps.append(key)
    assumptions = _method_assumptions(inputs, "three_statement")
    growth_path = _number_list(assumptions.get("revenue_growth_path"))
    capex_rate = _number(assumptions.get("capex_to_revenue"))
    wc_rate = _number(assumptions.get("working_capital_to_incremental_revenue"))
    if not growth_path:
        gaps.append("revenue_growth_path_assumption_missing")
    if capex_rate is None:
        gaps.append("capex_to_revenue_assumption_missing")
    if wc_rate is None:
        gaps.append("working_capital_to_incremental_revenue_assumption_missing")
    if growth_path and any(growth <= -1 for growth in growth_path):
        gaps.append("revenue_growth_path_must_be_greater_than_negative_one")
        growth_path = []
    if capex_rate is not None and capex_rate < 0:
        gaps.append("capex_to_revenue_must_be_non_negative")
        capex_rate = None
    projection: list[dict[str, Any]] = []
    if (
        revenue is not None
        and net_margin is not None
        and growth_path
        and capex_rate is not None
        and wc_rate is not None
    ):
        current = revenue
        for year, growth in enumerate(growth_path, start=1):
            next_revenue = current * (1 + growth)
            net_income = next_revenue * net_margin / 100
            capex = next_revenue * capex_rate
            working_capital_change = (next_revenue - current) * wc_rate
            projection.append(
                {
                    "year": year,
                    "revenue": round(next_revenue, 6),
                    "net_income": round(net_income, 6),
                    "capex": round(capex, 6),
                    "working_capital_change": round(working_capital_change, 6),
                    "free_cash_flow_proxy": round(net_income - capex - working_capital_change, 6),
                    "status": "assumption_only",
                }
            )
            current = next_revenue
    return _result(
        symbol,
        observed={"revenue": revenue, "net_margin_pct": net_margin, "total_assets": assets, "total_liabilities": liabilities, "operating_cash_flow": ocf},
        derived={"projection": projection},
        gaps=gaps,
        assumptions=["projection_growth_path", "capex_rate", "working_capital_rate"] if projection else [],
        methodology={"revenue_growth_path": growth_path, "capex_to_revenue": capex_rate, "working_capital_to_incremental_revenue": wc_rate},
    )


def _quick_lbo(
    snapshots: dict[str, dict[str, Any]],
    symbol: str,
    inputs: dict[str, Any],
    _valuation: dict[str, Any],
    _risk: dict[str, Any],
) -> dict[str, Any]:
    financials = _section(snapshots, "fundamental", "financial_abstract")
    ebitda = _find_number(financials, ("ebitda", "EBITDA"))
    if ebitda is None or ebitda <= 0:
        return _result(symbol, gaps=["reported_ebitda_missing"])
    assumptions = _method_assumptions(inputs, "quick_lbo")
    entry_multiple = _number(assumptions.get("entry_multiple"))
    debt_multiple = _number(assumptions.get("debt_multiple"))
    exit_multiple = _number(assumptions.get("exit_multiple"))
    growth = _number(assumptions.get("annual_ebitda_growth"))
    paydown_rate = _number(assumptions.get("annual_debt_paydown_pct"))
    hold_years_value = _number(assumptions.get("hold_years"))
    assumption_values = {
        "entry_multiple_assumption_missing": entry_multiple,
        "debt_multiple_assumption_missing": debt_multiple,
        "exit_multiple_assumption_missing": exit_multiple,
        "annual_ebitda_growth_assumption_missing": growth,
        "annual_debt_paydown_pct_assumption_missing": paydown_rate,
        "hold_years_assumption_missing": hold_years_value,
    }
    missing = [key for key, value in assumption_values.items() if value is None]
    if missing:
        return _result(symbol, observed={"ebitda": ebitda}, gaps=missing)
    invalid = []
    if entry_multiple <= 0:
        invalid.append("entry_multiple_must_be_positive")
    if debt_multiple < 0:
        invalid.append("debt_multiple_must_be_non_negative")
    if exit_multiple <= 0:
        invalid.append("exit_multiple_must_be_positive")
    if growth <= -1:
        invalid.append("annual_ebitda_growth_must_be_greater_than_negative_one")
    if not 0 <= paydown_rate <= 1:
        invalid.append("annual_debt_paydown_pct_must_be_between_zero_and_one")
    if invalid:
        return _result(symbol, observed={"ebitda": ebitda}, gaps=invalid)
    hold_years = int(hold_years_value)
    if hold_years <= 0 or hold_years_value != hold_years:
        return _result(symbol, observed={"ebitda": ebitda}, gaps=["hold_years_must_be_positive_integer"])
    entry_debt = ebitda * debt_multiple
    entry_equity = ebitda * entry_multiple - entry_debt
    if entry_equity <= 0:
        return _result(symbol, observed={"ebitda": ebitda}, gaps=["entry_equity_must_be_positive"])
    exit_ebitda = ebitda * (1 + growth) ** hold_years
    exit_debt = max(entry_debt * (1 - paydown_rate) ** hold_years, 0.0)
    exit_equity = max(exit_ebitda * exit_multiple - exit_debt, 0.0)
    moic = exit_equity / entry_equity if entry_equity > 0 else None
    irr = moic ** (1 / hold_years) - 1 if moic is not None and moic > 0 else None
    return _result(
        symbol,
        observed={"ebitda": ebitda},
        derived={"entry_equity": entry_equity, "exit_equity": exit_equity, "moic": moic, "irr": irr},
        assumptions=["lbo_transaction_assumptions"],
        methodology={"entry_multiple": entry_multiple, "debt_multiple": debt_multiple, "exit_multiple": exit_multiple, "annual_ebitda_growth": growth, "annual_debt_paydown_pct": paydown_rate, "hold_years": hold_years},
    )


def _accretion_dilution(
    snapshots: dict[str, dict[str, Any]],
    symbol: str,
    inputs: dict[str, Any],
    _valuation: dict[str, Any],
    _risk: dict[str, Any],
) -> dict[str, Any]:
    financials = _section(snapshots, "fundamental", "financial_abstract")
    acquirer_income = _find_number(financials, ("net_profit", "归母净利润", "净利润"))
    acquirer_shares = _find_number(financials, ("shares_outstanding", "total_shares", "总股本"))
    assumptions = _method_assumptions(inputs, "accretion_dilution")
    target_income = _number(assumptions.get("target_net_income"))
    new_shares = _number(assumptions.get("new_shares"))
    synergies = _number(assumptions.get("after_tax_synergies"))
    financing_cost = _number(assumptions.get("after_tax_financing_cost"))
    values = {
        "acquirer_net_income_missing": acquirer_income,
        "acquirer_shares_missing": acquirer_shares,
        "target_net_income_assumption_missing": target_income,
        "new_shares_assumption_missing": new_shares,
        "after_tax_synergies_assumption_missing": synergies,
        "after_tax_financing_cost_assumption_missing": financing_cost,
    }
    gaps = [key for key, value in values.items() if value is None]
    if acquirer_shares is not None and acquirer_shares <= 0:
        gaps.append("acquirer_shares_must_be_positive")
    if new_shares is not None and new_shares < 0:
        gaps.append("new_shares_must_be_non_negative")
    if financing_cost is not None and financing_cost < 0:
        gaps.append("after_tax_financing_cost_must_be_non_negative")
    if gaps or acquirer_shares == 0 or (acquirer_shares or 0) + (new_shares or 0) <= 0:
        return _result(symbol, observed={"acquirer_net_income": acquirer_income, "acquirer_shares": acquirer_shares}, gaps=gaps or ["pro_forma_shares_invalid"])
    standalone_eps = acquirer_income / acquirer_shares
    pro_forma_income = acquirer_income + target_income + synergies - financing_cost
    pro_forma_eps = pro_forma_income / (acquirer_shares + new_shares)
    return _result(
        symbol,
        observed={"acquirer_net_income": acquirer_income, "acquirer_shares": acquirer_shares},
        derived={"standalone_eps": standalone_eps, "pro_forma_eps": pro_forma_eps, "accretion_pct": (pro_forma_eps / standalone_eps - 1) * 100 if standalone_eps else None},
        assumptions=["explicit_transaction_assumptions"],
        methodology=assumptions,
    )


def _segmental_model(
    snapshots: dict[str, dict[str, Any]],
    symbol: str,
    _inputs: dict[str, Any],
    _valuation: dict[str, Any],
    _risk: dict[str, Any],
) -> dict[str, Any]:
    financials = _section(snapshots, "fundamental", "financial_abstract")
    raw_segments = (
        financials.get("segments")
        or financials.get("segment_revenue")
        or financials.get("主营构成")
        or financials.get("分部收入")
    )
    if not isinstance(raw_segments, list) or not raw_segments:
        return _result(symbol, gaps=["reported_segment_data_missing"])
    segments: list[dict[str, Any]] = []
    gaps: list[str] = []
    total_revenue = 0.0
    for index, raw in enumerate(raw_segments):
        if not isinstance(raw, Mapping):
            gaps.append(f"segment_{index + 1}_invalid")
            continue
        name = str(raw.get("name") or raw.get("segment") or raw.get("业务") or "").strip()
        revenue = _find_number(raw, ("revenue", "收入", "营业收入", "主营收入"))
        gross_profit = _find_number(raw, ("gross_profit", "毛利润"))
        gross_margin = _find_number(raw, ("gross_margin", "毛利率"))
        if not name:
            gaps.append(f"segment_{index + 1}_name_missing")
        if revenue is None:
            gaps.append(f"segment_{name or index + 1}_revenue_missing")
        else:
            total_revenue += revenue
        segments.append(
            {
                "name": name or None,
                "revenue": revenue,
                "gross_profit": gross_profit,
                "gross_margin_pct": gross_margin,
                "status": "available" if name and revenue is not None else "data_gap",
            }
        )
    for segment in segments:
        revenue = segment.get("revenue")
        segment["revenue_share_pct"] = (
            round(float(revenue) / total_revenue * 100, 6)
            if revenue is not None and total_revenue > 0
            else None
        )
    return _result(
        symbol,
        observed={"segments": segments, "reported_segment_revenue_total": total_revenue or None},
        gaps=gaps,
    )


def _initiation_report(
    snapshots: dict[str, dict[str, Any]], symbol: str, _inputs: dict[str, Any], valuation: dict[str, Any], _risk: dict[str, Any]
) -> dict[str, Any]:
    market = _section(snapshots, "market", "market_overview")
    financials = _section(snapshots, "fundamental", "financial_abstract")
    chain = _facts(snapshots.get("chain"))
    events = _event_rows(snapshots.get("events"))
    gaps = _missing_snapshot_gaps(snapshots, ("market", "fundamental", "chain", "industry", "events", "valuation"))
    return _result(
        symbol,
        observed={
            "company": {"name": market.get("name") or market.get("名称"), "industry": market.get("industry") or market.get("sector")},
            "business_evidence": chain,
            "financial_snapshot": financials,
            "market_snapshot": market,
            "catalysts": events[:10],
            "valuation": _valuation_summary(valuation),
        },
        derived={"coverage_state": "ready_for_review" if not gaps else "evidence_incomplete"},
        gaps=gaps,
    )


def _earnings_review(
    snapshots: dict[str, dict[str, Any]], symbol: str, _inputs: dict[str, Any], _valuation: dict[str, Any], _risk: dict[str, Any]
) -> dict[str, Any]:
    financials = _section(snapshots, "fundamental", "financial_abstract")
    research = _facts(snapshots.get("research"))
    rows = []
    gaps = []
    for metric, actual_aliases, consensus_aliases in (
        ("revenue", ("revenue", "营业收入"), ("consensus_revenue", "consensus_rev_yi")),
        ("net_profit", ("net_profit", "归母净利润"), ("consensus_net_profit",)),
        ("eps", ("eps", "基本每股收益"), ("consensus_eps", "consensus_eps_2026")),
    ):
        actual = _find_number(financials, actual_aliases)
        consensus = _find_number(research, consensus_aliases)
        if actual is None:
            gaps.append(f"actual_{metric}_missing")
        if consensus is None:
            gaps.append(f"consensus_{metric}_missing")
        delta_pct = (actual / consensus - 1) * 100 if actual is not None and consensus not in {None, 0} else None
        rows.append({"metric": metric, "actual": actual, "consensus": consensus, "delta_pct": delta_pct, "status": "available" if delta_pct is not None else "data_gap"})
    return _result(symbol, observed={"actual_vs_consensus": rows}, gaps=gaps)


def _catalyst_calendar(
    snapshots: dict[str, dict[str, Any]], symbol: str, _inputs: dict[str, Any], _valuation: dict[str, Any], _risk: dict[str, Any]
) -> dict[str, Any]:
    events = _event_rows(snapshots.get("events"))
    dated = [event for event in events if event.get("date")]
    gaps = [] if dated else ["dated_catalysts_missing"]
    if events and len(dated) < len(events):
        gaps.append("some_catalysts_undated")
    return _result(symbol, observed={"events": events, "dated_event_count": len(dated)}, gaps=gaps)


def _thesis_tracker(
    snapshots: dict[str, dict[str, Any]], symbol: str, _inputs: dict[str, Any], valuation: dict[str, Any], _risk: dict[str, Any]
) -> dict[str, Any]:
    financials = _section(snapshots, "fundamental", "financial_abstract")
    market = _section(snapshots, "market", "market_overview")
    revenue_growth = _find_number(financials, ("revenue_growth", "revenue_yoy", "营业收入同比增长"))
    ocf = _find_number(financials, ("operating_cash_flow", "经营活动产生的现金流量净额"))
    price = _find_number(market, ("latest_price", "price", "最新价"))
    fair_value = _number(valuation.get("fair_value"))
    pillars = [
        _pillar("growth", revenue_growth, "reported_revenue_growth_pct"),
        _pillar("cash_generation", ocf, "reported_operating_cash_flow"),
        _pillar("valuation", (fair_value - price) / price * 100 if fair_value is not None and price not in {None, 0} else None, "proxy_value_upside_pct"),
    ]
    gaps = [f"{pillar['pillar']}_evidence_missing" for pillar in pillars if pillar["status"] == "data_gap"]
    return _result(symbol, observed={"pillars": pillars}, gaps=gaps)


def _morning_note(
    snapshots: dict[str, dict[str, Any]], symbol: str, _inputs: dict[str, Any], _valuation: dict[str, Any], _risk: dict[str, Any]
) -> dict[str, Any]:
    market = _section(snapshots, "market", "market_overview")
    capital_flow = _facts(snapshots.get("capital_flow"))
    sentiment = _facts(snapshots.get("sentiment"))
    events = _event_rows(snapshots.get("events"))
    gaps = _missing_snapshot_gaps(snapshots, ("market", "capital_flow", "sentiment", "events"))
    return _result(symbol, observed={"market": market, "capital_flow": capital_flow, "sentiment": sentiment, "overnight_events": events[:8]}, gaps=gaps)


def _ic_memo(
    snapshots: dict[str, dict[str, Any]], symbol: str, _inputs: dict[str, Any], valuation: dict[str, Any], risk: dict[str, Any]
) -> dict[str, Any]:
    initiation = _initiation_report(snapshots, symbol, {}, valuation, risk)
    thesis = _thesis_tracker(snapshots, symbol, {}, valuation, risk)
    risk_rows = _risk_rows(risk, symbol)
    gaps = [*list(initiation.get("data_gap") or []), *list(thesis.get("data_gap") or [])]
    if not risk_rows:
        gaps.append("risk_scan_missing")
    return _result(
        symbol,
        observed={"company_and_market": initiation.get("observed"), "thesis": thesis.get("observed"), "risk_items": risk_rows},
        derived={"committee_state": "review_required", "recommendation": None},
        gaps=gaps,
        assumptions=["investment_decision_requires_human_committee"],
    )


def _porter_bcg(
    snapshots: dict[str, dict[str, Any]], symbol: str, _inputs: dict[str, Any], _valuation: dict[str, Any], _risk: dict[str, Any]
) -> dict[str, Any]:
    moat = _facts(snapshots.get("moat"))
    industry = _facts(snapshots.get("industry"))
    peers = _facts(snapshots.get("peers"))
    chain = _facts(snapshots.get("chain"))
    forces = {
        "new_entrants": _force_evidence(moat, ("entry_barrier", "intangible", "barriers")),
        "substitutes": _force_evidence(moat, ("switching", "switching_cost")),
        "supplier_power": _force_evidence(chain, ("upstream", "supplier", "suppliers")),
        "buyer_power": _force_evidence(chain, ("downstream", "customer", "customers")),
        "rivalry": _force_evidence(peers, ("peer_table", "comparison", "peers")),
    }
    market_share = _find_number({**industry, **moat}, ("market_share", "market_share_pct"))
    market_growth = _find_number(industry, ("growth", "industry_growth", "growth_pct"))
    bcg = None
    if market_share is not None and market_growth is not None:
        bcg = "star" if market_share > 3 and market_growth > 15 else "cash_cow" if market_share > 3 else "question_mark" if market_growth > 15 else "dog"
    gaps = [f"porter_{name}_evidence_missing" for name, value in forces.items() if value["status"] == "data_gap"]
    if bcg is None:
        gaps.append("bcg_market_share_or_growth_missing")
    return _result(
        symbol,
        observed={"porter_five_forces": forces, "market_share_pct": market_share, "market_growth_pct": market_growth},
        derived={"bcg_position": bcg},
        gaps=gaps,
        methodology={"bcg_market_share_threshold_pct": 3, "bcg_market_growth_threshold_pct": 15},
    )


def _dd_checklist(
    snapshots: dict[str, dict[str, Any]], symbol: str, _inputs: dict[str, Any], _valuation: dict[str, Any], _risk: dict[str, Any]
) -> dict[str, Any]:
    requirements = {
        "financial": ("fundamental", "valuation"),
        "commercial": ("industry", "peers", "chain"),
        "legal_governance": ("governance", "notice"),
        "market_risk": ("policy", "sentiment", "events", "trap"),
    }
    workstreams = []
    gaps = []
    for name, snapshot_types in requirements.items():
        items = []
        for snapshot_type in snapshot_types:
            available = _has_observation(snapshots.get(snapshot_type))
            items.append({"evidence_type": snapshot_type, "status": "available" if available else "data_gap"})
            if not available:
                gaps.append(f"dd_{snapshot_type}_missing")
        workstreams.append({"workstream": name, "items": items})
    total = sum(len(row["items"]) for row in workstreams)
    available = total - len(gaps)
    return _result(symbol, observed={"workstreams": workstreams, "coverage": available / total if total else 0}, gaps=gaps)


def _unit_economics(
    snapshots: dict[str, dict[str, Any]], symbol: str, _inputs: dict[str, Any], _valuation: dict[str, Any], _risk: dict[str, Any]
) -> dict[str, Any]:
    financials = _section(snapshots, "fundamental", "financial_abstract")
    customer_count = _find_number(financials, ("customer_count", "customers"))
    revenue = _find_number(financials, ("revenue", "营业收入"))
    gross_margin = _find_number(financials, ("gross_margin", "销售毛利率"))
    churn = _find_number(financials, ("churn_rate", "annual_churn"))
    cac = _find_number(financials, ("customer_acquisition_cost", "cac"))
    derived_metrics: dict[str, Any] = {}
    gaps = []
    if customer_count not in {None, 0} and revenue is not None:
        derived_metrics["arpu"] = revenue / customer_count
    else:
        gaps.append("customer_count_or_revenue_missing")
    if derived_metrics.get("arpu") is not None and gross_margin is not None and churn not in {None, 0}:
        derived_metrics["ltv"] = derived_metrics["arpu"] * (gross_margin / 100) / (churn / 100)
    else:
        gaps.append("ltv_inputs_missing")
    if derived_metrics.get("ltv") is not None and cac not in {None, 0}:
        derived_metrics["ltv_cac_ratio"] = derived_metrics["ltv"] / cac
    else:
        gaps.append("cac_missing")
    net_margin = _find_number(financials, ("net_margin", "销售净利率"))
    margin_bridge = None
    if gross_margin is not None and net_margin is not None:
        margin_bridge = {"gross_margin_pct": gross_margin, "implied_operating_and_tax_drag_pct": gross_margin - net_margin, "net_margin_pct": net_margin}
    return _result(
        symbol,
        observed={
            "customer_count": customer_count,
            "revenue": revenue,
            "gross_margin_pct": gross_margin,
            "churn_rate_pct": churn,
            "cac": cac,
            "net_margin_pct": net_margin,
        },
        derived={"unit_metrics": derived_metrics, "margin_bridge": margin_bridge},
        gaps=gaps,
    )


def _value_creation_plan(
    snapshots: dict[str, dict[str, Any]], symbol: str, inputs: dict[str, Any], _valuation: dict[str, Any], _risk: dict[str, Any]
) -> dict[str, Any]:
    financials = _section(snapshots, "fundamental", "financial_abstract")
    revenue = _find_number(financials, ("revenue", "营业收入"))
    ebitda = _find_number(financials, ("ebitda", "EBITDA"))
    levers = _method_assumptions(inputs, "value_creation_plan").get("levers")
    normalized_levers = [dict(item) for item in levers if isinstance(item, Mapping)] if isinstance(levers, list) else []
    gaps = []
    if revenue is None:
        gaps.append("revenue_missing")
    if ebitda is None:
        gaps.append("reported_ebitda_missing")
    if not normalized_levers:
        gaps.append("explicit_value_creation_levers_missing")
    target_ebitda = ebitda
    quantified = True
    for lever in normalized_levers:
        impact = _number(lever.get("ebitda_impact"))
        if impact is None:
            quantified = False
        elif target_ebitda is not None:
            target_ebitda += impact
    if not quantified:
        target_ebitda = None
        gaps.append("unquantified_value_creation_lever")
    return _result(
        symbol,
        observed={"revenue": revenue, "ebitda": ebitda},
        derived={"levers": normalized_levers, "target_ebitda": target_ebitda},
        gaps=gaps,
        assumptions=["explicit_value_creation_plan"] if normalized_levers else [],
    )


def _result(
    symbol: str,
    *,
    observed: Mapping[str, Any] | None = None,
    derived: Mapping[str, Any] | None = None,
    gaps: Iterable[str] = (),
    assumptions: Iterable[str] = (),
    methodology: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    gap_list = _unique(gaps)
    assumption_list = _unique(assumptions)
    has_content = bool(observed or derived)
    quality = (
        "data_gap"
        if not has_content or (gap_list and not _has_non_null(observed))
        else "degraded"
        if gap_list
        else "assumption_only"
        if assumption_list
        else "available"
    )
    return {
        "symbol": symbol,
        "observed": dict(observed or {}),
        "derived": dict(derived or {}),
        "methodology": dict(methodology or {}),
        "quality_status": quality,
        "data_gap": gap_list,
        "assumption_flags": assumption_list,
    }


def _snapshot_map(rows: list[dict[str, Any]], symbol: str) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("snapshot_type") or ""): row
        for row in rows
        if isinstance(row, dict) and str(row.get("symbol") or "") in {"", symbol}
    }


def _facts(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    if not _has_observation(snapshot):
        return {}
    facts = snapshot.get("facts")
    return dict(facts) if isinstance(facts, Mapping) else {}


def _section(snapshots: dict[str, dict[str, Any]], snapshot_type: str, key: str) -> dict[str, Any]:
    facts = _facts(snapshots.get(snapshot_type))
    value = facts.get(key)
    return dict(value) if isinstance(value, Mapping) else {}


def _has_observation(snapshot: Mapping[str, Any] | None) -> bool:
    if not snapshot:
        return False
    quality = str(snapshot.get("quality_status") or snapshot.get("status") or "available").lower()
    facts = snapshot.get("facts")
    return quality != "data_gap" and isinstance(facts, Mapping) and bool(facts) and str(facts.get("availability") or "").lower() != "data_gap"


def _missing_snapshot_gaps(snapshots: Mapping[str, Any], snapshot_types: Iterable[str]) -> list[str]:
    return [f"{snapshot_type}_snapshot_missing" for snapshot_type in snapshot_types if not _has_observation(snapshots.get(snapshot_type))]


def _event_rows(snapshot: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    facts = _facts(snapshot)
    values = facts.get("events") or facts.get("event_timeline") or facts.get("recent_news") or facts.get("items") or []
    if not isinstance(values, list):
        values = [values] if values else []
    output = []
    for item in values:
        if isinstance(item, Mapping):
            output.append({"date": item.get("date") or item.get("event_date") or item.get("pub_time"), "title": item.get("title") or item.get("headline") or item.get("event"), "source": item.get("source")})
        elif str(item).strip():
            output.append({"date": None, "title": str(item), "source": None})
    return output


def _risk_rows(risk: Mapping[str, Any], symbol: str) -> list[dict[str, Any]]:
    for result in list(risk.get("results") or risk.get("scans") or []):
        if isinstance(result, Mapping) and str(result.get("symbol") or "") == symbol:
            values = result.get("risk_items") or result.get("signals") or []
            return [dict(item) for item in values if isinstance(item, Mapping)]
    return []


def _valuation_for_symbol(valuation: Mapping[str, Any] | None, symbol: str) -> dict[str, Any]:
    for model in list((valuation or {}).get("models") or []):
        if isinstance(model, Mapping) and str(model.get("symbol") or "") == symbol:
            return dict(model)
    return {}


def _valuation_summary(valuation: Mapping[str, Any]) -> dict[str, Any]:
    return {key: valuation.get(key) for key in ("model", "fair_value", "market_price", "margin_of_safety", "verdict", "status")}


def _method_assumptions(inputs: Mapping[str, Any], method_id: str) -> dict[str, Any]:
    assumptions = inputs.get("method_assumptions")
    if not isinstance(assumptions, Mapping):
        return {}
    value = assumptions.get(method_id)
    return dict(value) if isinstance(value, Mapping) else {}


def _pillar(name: str, value: float | None, evidence_type: str) -> dict[str, Any]:
    return {"pillar": name, "value": value, "evidence_type": evidence_type, "status": "available" if value is not None else "data_gap"}


def _force_evidence(value: Any, aliases: Iterable[str]) -> dict[str, Any]:
    alias_set = {str(alias).casefold() for alias in aliases}
    for key, item in _walk(value):
        if key.casefold() in alias_set and item is not None and item != "" and item != [] and item != {}:
            return {"status": "available", "evidence": item, "score": None}
    return {"status": "data_gap", "evidence": None, "score": None}


def _find_number(value: Any, aliases: Iterable[str]) -> float | None:
    alias_set = {str(alias).casefold() for alias in aliases}
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold() in alias_set:
                number = _number(item)
                if number is not None:
                    return number
    for key, item in _walk(value):
        if key.casefold() in alias_set:
            number = _number(item)
            if number is not None:
                return number
    return None


def _walk(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key), item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _number_list(value: Any) -> list[float]:
    if not isinstance(value, list):
        return []
    return [number for item in value if (number := _number(item)) is not None]


def _has_non_null(value: Mapping[str, Any] | None) -> bool:
    if not value:
        return False
    return any(item is not None and item != {} and item != [] for item in value.values())


def _unique(values: Iterable[Any]) -> list[str]:
    output: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in output:
            output.append(text)
    return output


__all__ = ["METHOD_ARTIFACT_TYPES", "UZI_METHOD_PRODUCT_VERSION", "build_method_product"]
