"""Pinned UZI 3.9.4 pure-method execution inside the Finance DomainPack.

The upstream functions are called without rewriting their formulas.  Agent OS
adds only the outer result envelope, source lineage, call-shape validation and
missing-data/default disclosure.  Pinned optional defaults still execute, but
the envelope marks their evidence quality as degraded.
"""

from __future__ import annotations

import importlib
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

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
    # UZI 3.9.4's pipeline registry stores the fund-holder fetcher under
    # ``6_fund_holders`` and spills ``fund_managers`` to raw_data's top level.
    "fund_holders": "6_fund_holders",
    "similar_stocks": "similar_stocks",
}

_REQUIRED_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "dcf": ("0_basic", "1_financials"),
    # ``similar_stocks`` is a raw_data top-level bonus collector in UZI's
    # pipeline, not a numbered dimension.  An empty peer set is valid input to
    # the native comps function, so it must not be treated as a missing
    # dimension here.
    "comps": ("0_basic", "1_financials"),
    "three_statement": ("1_financials",),
    "quick_lbo": ("1_financials",),
    "accretion_dilution": ("0_basic", "1_financials"),
    "initiation_report": ("0_basic", "1_financials", "14_moat"),
    "earnings_review": ("1_financials",),
    "catalyst_calendar": ("15_events",),
    "thesis_tracker": ("1_financials", "2_kline"),
    "morning_note": ("0_basic", "2_kline", "12_capital_flow", "16_lhb", "17_sentiment"),
    "quantitative_screen": (),
    "sector_review": ("4_peers", "5_chain", "7_industry"),
    "ic_memo": ("0_basic", "1_financials", "14_moat"),
    "porter_bcg": ("0_basic", "7_industry", "14_moat"),
    "dd_checklist": ("1_financials",),
    "unit_economics": ("0_basic", "1_financials"),
    "value_creation_plan": ("0_basic", "1_financials", "7_industry"),
    "ai_readiness": (
        "0_basic",
        "1_financials",
        "5_chain",
        "7_industry",
        "11_governance",
        "13_policy",
        "14_moat",
        "15_events",
        "17_sentiment",
        "18_trap",
    ),
    "earnings_preview": ("0_basic", "1_financials"),
    "model_update": ("0_basic", "1_financials"),
}


def build_uzi_raw(snapshot_rows: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
    _initialize_native_imports()
    market_router = importlib.import_module("lib.market_router")
    target_info = market_router.parse_ticker(symbol)
    target_symbol = target_info.full
    dimensions: dict[str, dict[str, Any]] = {}
    root_fields: dict[str, Any] = {}
    has_root_transport = False
    top_level_fields: dict[str, Any] = {}
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
        excluded_from_scoring, degradation = _snapshot_scoring_exclusion(row)
        facts = row.get("facts") if isinstance(row.get("facts"), dict) else {}
        transported_root = (
            facts.get("_uzi_root_fields")
            if isinstance(facts.get("_uzi_root_fields"), dict)
            else {}
        )
        if transported_root:
            has_root_transport = True
        for root_key in ("ticker", "market", "code", "full", "fetched_at"):
            transported = transported_root.get(root_key)
            if (
                isinstance(transported, dict)
                and transported.get("present") is True
                and root_key not in root_fields
            ):
                root_fields[root_key] = transported.get("value")
        spill_fields = (
            facts.get("_uzi_top_level_fields")
            if isinstance(facts.get("_uzi_top_level_fields"), dict)
            else {}
        )
        for spill_key in ("fund_managers", "similar_stocks"):
            spill = spill_fields.get(spill_key)
            if (
                isinstance(spill, dict)
                and spill.get("present") is True
                and spill_key not in top_level_fields
            ):
                # Preserve UZI's symbol-root transport exactly.  In
                # particular, absent, None and [] are three different states.
                top_level_fields[spill_key] = spill.get("value")
        raw = facts.get("_uzi_raw_result")
        if isinstance(raw, dict):
            native_result = dict(raw)
            if excluded_from_scoring:
                native_result["_excluded_from_scoring"] = True
                native_result["_degradation"] = degradation
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
                    "fund_managers",
                    "similar_stocks",
                    # These are ArtifactBoard display aliases derived from
                    # the same native payload.  They must never be recovered
                    # back into UZI's original dimension envelope.
                    "market_overview",
                    "financial_abstract",
                    "history",
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
                    "source_trace": list(
                        row.get("source_trace")
                        or facts.get("_uzi_source_trace")
                        or row.get("source_refs")
                        or []
                    ),
                }
            # Match lib.pipeline.collect/run exactly: declared top-level fields
            # are removed from the dimension payload and written beside
            # ``dimensions``.  Keep the frozen snapshot itself untouched.
            if fetcher_id == "6_fund_holders":
                fund_managers = native_data.pop("fund_managers", None)
                if (
                    "fund_managers" not in top_level_fields
                    and isinstance(raw.get("data"), dict)
                    and "fund_managers" in raw["data"]
                ):
                    top_level_fields["fund_managers"] = fund_managers
                native_result["data"] = native_data
                dimensions[fetcher_id] = native_result
            elif fetcher_id == "similar_stocks":
                if (
                    "similar_stocks" not in top_level_fields
                    and "similar_stocks" in native_data
                ):
                    top_level_fields["similar_stocks"] = native_data.get(
                        "similar_stocks"
                    )
            else:
                dimensions[fetcher_id] = native_result
        elif excluded_from_scoring and fetcher_id != "similar_stocks":
            dimensions[fetcher_id] = {
                "data": {},
                "error": str(
                    degradation.get("reason_code")
                    or "historical_unavailable_disclosure"
                ),
                "_excluded_from_scoring": True,
                "_degradation": degradation,
            }
    if has_root_transport:
        # Root-routed snapshots carry the exact lane-specific root envelope.
        # Preserve legacy's fetched_at/no-code shape and pipeline's code/full
        # shape instead of manufacturing a common superset.
        raw = {
            **root_fields,
            "dimensions": dimensions,
            **top_level_fields,
        }
    else:
        # Compatibility path for older/on-demand snapshots that predate the
        # root-field transport contract.
        basic_result = dimensions.get("0_basic") or {}
        basic_market = basic_result.get("market")
        resolved_ticker = str(basic_result.get("ticker") or symbol).strip() or symbol
        try:
            resolved_info = market_router.parse_ticker(resolved_ticker)
        except (TypeError, ValueError):
            resolved_info = target_info
        raw = {
            "ticker": resolved_ticker,
            "market": basic_market if basic_market in {"A", "H", "U"} else resolved_info.market,
            "code": resolved_info.code,
            "full": resolved_info.full,
            "dimensions": dimensions,
            **top_level_fields,
        }
    return raw


def _snapshot_scoring_exclusion(
    row: Mapping[str, Any],
) -> tuple[bool, dict[str, Any]]:
    """Read the persisted, audit-backed scoring policy from a frozen row."""

    scoring_policy = (
        row.get("scoring_policy")
        if isinstance(row.get("scoring_policy"), Mapping)
        else {}
    )
    query_manifest = (
        row.get("query_manifest")
        if isinstance(row.get("query_manifest"), Mapping)
        else {}
    )
    degradation = (
        dict(query_manifest.get("degradation"))
        if isinstance(query_manifest.get("degradation"), Mapping)
        else {}
    )
    excluded = bool(
        scoring_policy.get("excluded") is True
        or degradation.get("excluded_from_scoring") is True
    )
    if excluded and not degradation:
        degradation = {
            "excluded_from_scoring": True,
            "reason_code": scoring_policy.get("reason_code"),
            "message": scoring_policy.get("reason"),
        }
    return excluded, degradation


def _missing_native_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in {"", "—", "-", "N/A", "None"}
    return isinstance(value, (list, dict)) and not value


def _missing_positive_observation(value: object) -> bool:
    if isinstance(value, bool) or _missing_native_value(value):
        return True
    try:
        number = float(value)
    except (TypeError, ValueError):
        return True
    return not math.isfinite(number) or number <= 0


def _has_numeric_observation(value: object) -> bool:
    if isinstance(value, bool) or _missing_native_value(value):
        return False
    try:
        number = float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def _numeric_observation_count(value: object) -> int:
    if not isinstance(value, list):
        return 0
    return sum(1 for item in value if _has_numeric_observation(item))


_SCREEN_REQUIRED_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "value": ("1_financials",),
    "growth": ("1_financials",),
    "quality": ("1_financials", "14_moat"),
    "gulp": ("1_financials", "2_kline"),
    "short": ("1_financials",),
}


def _dimension_data(
    dimensions: Mapping[str, object],
    dimension: str,
) -> Mapping[str, Any]:
    value = dimensions.get(dimension)
    if isinstance(value, Mapping) and isinstance(value.get("data"), Mapping):
        return value["data"]
    return {}


def _method_quality_gaps(
    method_id: str,
    dimensions: Mapping[str, object],
    assumptions: Mapping[str, Any],
) -> list[str]:
    required = _REQUIRED_DIMENSIONS.get(method_id, ())
    if method_id == "quantitative_screen":
        style = str(assumptions.get("style") or "quality")
        required = _SCREEN_REQUIRED_DIMENSIONS.get(style, ())
    gaps = [
        f"dimension_missing:{dimension}"
        for dimension in required
        if not _dimension_available(dimensions.get(dimension))
    ]

    needs_pe_source = method_id in {"comps", "thesis_tracker", "morning_note"} or (
        method_id == "model_update" and "target_pe" in assumptions
    ) or (
        method_id == "quantitative_screen"
        and str(assumptions.get("style") or "quality") in {"value", "gulp", "short"}
    )
    basic = dimensions.get("0_basic")
    valuation = dimensions.get("10_valuation")
    basic_data = (
        basic.get("data")
        if isinstance(basic, Mapping) and isinstance(basic.get("data"), dict)
        else {}
    )
    valuation_data = (
        valuation.get("data")
        if isinstance(valuation, Mapping) and isinstance(valuation.get("data"), dict)
        else {}
    )

    if method_id in {"earnings_review", "initiation_report", "ic_memo"}:
        financial_data = _dimension_data(dimensions, "1_financials")
        if not _numeric_observation_count(financial_data.get("revenue_history")):
            gaps.append("observation_missing:1_financials.revenue_history")
        if not _numeric_observation_count(financial_data.get("net_profit_history")):
            gaps.append("observation_missing:1_financials.net_profit_history")
        if method_id in {"initiation_report", "ic_memo"} and not _numeric_observation_count(
            financial_data.get("roe_history")
        ):
            gaps.append("observation_missing:1_financials.roe_history")
    if needs_pe_source and _missing_positive_observation(
        basic_data.get("pe_ttm")
    ) and _missing_positive_observation(valuation_data.get("pe")):
        gaps.append("observation_missing:pe")
    if (
        method_id == "morning_note"
        and _missing_positive_observation(basic_data.get("pe_ttm"))
        and not _missing_positive_observation(valuation_data.get("pe"))
    ):
        gaps.append("native_source_limitation:morning_note.basic_pe_display")

    needs_pb_source = method_id == "quantitative_screen" and str(
        assumptions.get("style") or "quality"
    ) == "value"
    if needs_pb_source and _missing_positive_observation(
        basic_data.get("pb")
    ) and _missing_positive_observation(valuation_data.get("pb")):
        gaps.append("observation_missing:pb")

    if method_id == "quantitative_screen":
        style = str(assumptions.get("style") or "quality")
        financial_data = _dimension_data(dimensions, "1_financials")
        health = (
            financial_data.get("financial_health")
            if isinstance(financial_data.get("financial_health"), Mapping)
            else {}
        )
        moat_data = _dimension_data(dimensions, "14_moat")
        moat_scores = (
            moat_data.get("scores")
            if isinstance(moat_data.get("scores"), Mapping)
            else {}
        )
        if style in {"growth", "gulp", "short"} and _numeric_observation_count(
            financial_data.get("revenue_history")
        ) < 4:
            gaps.append("observation_missing:1_financials.revenue_history_4y")
        if style in {"growth", "gulp", "short"} and not _numeric_observation_count(
            financial_data.get("roe_history")
        ):
            gaps.append("observation_missing:1_financials.roe_history")
        if style == "growth":
            if not _numeric_observation_count(financial_data.get("net_profit_history")):
                gaps.append("observation_missing:1_financials.net_profit_history")
            gaps.append(
                "native_source_limitation:gross_margin_expanding_unimplemented"
            )
        if style in {"value", "quality"}:
            if not _has_numeric_observation(
                financial_data.get("operating_cash_flow_yi")
            ):
                gaps.append("observation_missing:1_financials.operating_cash_flow_yi")
            if not _has_numeric_observation(health.get("debt_ratio")):
                gaps.append("observation_missing:1_financials.financial_health.debt_ratio")
        if style == "value" and (
            not _has_numeric_observation(basic_data.get("dividend_yield_ttm"))
            and not _has_numeric_observation(valuation_data.get("dividend_yield"))
        ):
            gaps.append("observation_missing:dividend_yield")
        if style == "quality":
            if _numeric_observation_count(financial_data.get("roe_history")) < 5:
                gaps.append("observation_missing:1_financials.roe_history_5y")
            if not _numeric_observation_count(
                financial_data.get("revenue_history")
            ) or not _numeric_observation_count(financial_data.get("net_profit_history")):
                gaps.append("observation_missing:1_financials.net_margin_inputs")
            for key in ("intangible", "switching", "network", "scale"):
                if not _has_numeric_observation(moat_scores.get(key)):
                    gaps.append(f"observation_missing:14_moat.scores.{key}")
        if style == "gulp":
            kline_data = _dimension_data(dimensions, "2_kline")
            if _missing_native_value(kline_data.get("stage")):
                gaps.append("observation_missing:2_kline.stage")
        if style == "short" and not _has_numeric_observation(health.get("debt_ratio")):
            gaps.append("observation_missing:1_financials.financial_health.debt_ratio")

    if method_id == "ai_readiness":
        financial_data = _dimension_data(dimensions, "1_financials")
        if _missing_positive_observation(
            basic_data.get("market_cap_yi") or basic_data.get("market_cap")
        ):
            gaps.append("observation_missing:0_basic.market_cap")
        if _missing_native_value(basic_data.get("industry")) and _missing_native_value(
            basic_data.get("name")
        ):
            gaps.append("observation_missing:0_basic.company_identity")
        if not _numeric_observation_count(
            financial_data.get("revenue_history")
        ) or not _numeric_observation_count(financial_data.get("net_profit_history")):
            gaps.append("observation_missing:1_financials.margin_inputs")
        required_observations = {
            "5_chain": ("upstream", "main_business_breakdown", "downstream"),
            "7_industry": ("industry", "industry_overview", "growth"),
            "11_governance": ("pledge", "insider_trades_1y"),
            "13_policy": ("policy_dir", "snippets"),
            "14_moat": ("scores",),
            "15_events": ("event_timeline", "recent_news", "recent_notices"),
            "17_sentiment": ("thermometer_value", "sentiment_label"),
            "18_trap": ("trap_level", "signals"),
        }
        for dimension, keys in required_observations.items():
            data = _dimension_data(dimensions, dimension)
            if dimension in {"11_governance", "15_events", "18_trap"}:
                available = any(key in data for key in keys)
            elif dimension == "14_moat":
                scores = data.get("scores") if isinstance(data.get("scores"), Mapping) else {}
                available = any(
                    _has_numeric_observation(value) for value in scores.values()
                )
            else:
                available = any(
                    not _missing_native_value(data.get(key)) for key in keys
                )
            if not available:
                gaps.append(f"observation_missing:{dimension}.semantic_fields")

    if method_id in {"dcf", "initiation_report", "ic_memo", "model_update"}:
        financial = dimensions.get("1_financials")
        financial_data = (
            financial.get("data")
            if isinstance(financial, Mapping)
            and isinstance(financial.get("data"), dict)
            else {}
        )
        if _missing_positive_observation(financial_data.get("operating_cash_flow_yi")):
            gaps.append("native_proxy_used:dcf.fcf_from_net_income")

    if method_id == "earnings_review" and "consensus" not in assumptions:
        research = dimensions.get("6_research")
        research_data = (
            research.get("data")
            if isinstance(research, Mapping) and isinstance(research.get("data"), dict)
            else {}
        )
        if _missing_positive_observation(
            research_data.get("consensus_rev_yi")
        ) or _missing_positive_observation(research_data.get("consensus_ni_yi")):
            gaps.append("native_fallback_used:earnings_review.consensus")

    if method_id == "earnings_preview" and not _dimension_available(
        dimensions.get("6_research")
    ):
        gaps.append("native_fallback_used:earnings_preview.consensus")

    if method_id == "earnings_preview":
        financial = dimensions.get("1_financials")
        financial_data = (
            financial.get("data")
            if isinstance(financial, Mapping)
            and isinstance(financial.get("data"), dict)
            else {}
        )
        if not _numeric_observation_count(financial_data.get("revenue_history")):
            gaps.append("observation_missing:1_financials.revenue_history")
        if not _numeric_observation_count(financial_data.get("net_profit_history")):
            gaps.append("observation_missing:1_financials.net_profit_history")

    if method_id == "morning_note":
        if _missing_positive_observation(basic_data.get("price")):
            gaps.append("observation_missing:0_basic.price")
        kline = dimensions.get("2_kline")
        kline_data = (
            kline.get("data")
            if isinstance(kline, Mapping) and isinstance(kline.get("data"), dict)
            else {}
        )
        if _missing_native_value(kline_data.get("stage")):
            gaps.append("observation_missing:2_kline.stage")
        capital = dimensions.get("12_capital_flow")
        capital_data = (
            capital.get("data")
            if isinstance(capital, Mapping) and isinstance(capital.get("data"), dict)
            else {}
        )
        if _missing_native_value(capital_data.get("main_5d")) and _missing_native_value(
            capital_data.get("main_fund_flow_20d")
        ):
            gaps.append("observation_missing:12_capital_flow.main_flow")
        lhb = dimensions.get("16_lhb")
        lhb_data = (
            lhb.get("data")
            if isinstance(lhb, Mapping) and isinstance(lhb.get("data"), dict)
            else {}
        )
        if "lhb_count_30d" not in lhb_data and "events" not in lhb_data:
            gaps.append("observation_missing:16_lhb.activity")
        sentiment = dimensions.get("17_sentiment")
        sentiment_data = (
            sentiment.get("data")
            if isinstance(sentiment, Mapping)
            and isinstance(sentiment.get("data"), dict)
            else {}
        )
        if _missing_native_value(
            sentiment_data.get("thermometer_value")
        ) and _missing_native_value(sentiment_data.get("sentiment_label")):
            gaps.append("observation_missing:17_sentiment.heat")

    if method_id == "sector_review":
        industry = dimensions.get("7_industry")
        industry_data = (
            industry.get("data")
            if isinstance(industry, Mapping) and isinstance(industry.get("data"), dict)
            else {}
        )
        if all(
            _missing_native_value(industry_data.get(key))
            for key in ("industry", "tam", "growth", "lifecycle")
        ):
            gaps.append("observation_missing:7_industry.overview")
        peers = dimensions.get("4_peers")
        peers_data = (
            peers.get("data")
            if isinstance(peers, Mapping) and isinstance(peers.get("data"), dict)
            else {}
        )
        if _missing_native_value(peers_data.get("peer_table")) and _missing_native_value(
            peers_data.get("peer_comparison")
        ):
            gaps.append("observation_missing:4_peers.competitive_map")
        chain = dimensions.get("5_chain")
        chain_data = (
            chain.get("data")
            if isinstance(chain, Mapping) and isinstance(chain.get("data"), dict)
            else {}
        )
        if all(
            _missing_native_value(chain_data.get(key))
            for key in ("upstream", "main_business_breakdown", "downstream")
        ):
            gaps.append("observation_missing:5_chain.value_chain")

    if method_id == "model_update" and assumptions:
        financial = dimensions.get("1_financials")
        financial_data = (
            financial.get("data")
            if isinstance(financial, Mapping)
            and isinstance(financial.get("data"), dict)
            else {}
        )
        if "rev_growth" in assumptions and _numeric_observation_count(
            financial_data.get("revenue_history")
        ) < 2:
            gaps.append("observation_missing:model_update.rev_growth_baseline")
        if "gross_margin" in assumptions and _missing_native_value(
            financial_data.get("gross_margin")
        ):
            gaps.append("observation_missing:model_update.gross_margin_baseline")
        if "net_margin" in assumptions and (
            not _numeric_observation_count(financial_data.get("revenue_history"))
            or not _numeric_observation_count(financial_data.get("net_profit_history"))
        ):
            gaps.append("observation_missing:model_update.net_margin_baseline")
        for key in ("capex_pct", "stage1_growth", "terminal_g", "beta"):
            if key in assumptions:
                gaps.append(f"native_source_limitation:model_update.{key}_baseline")

    if method_id == "three_statement":
        financial = dimensions.get("1_financials")
        financial_data = (
            financial.get("data")
            if isinstance(financial, Mapping)
            and isinstance(financial.get("data"), dict)
            else {}
        )
        health = (
            financial_data.get("financial_health")
            if isinstance(financial_data.get("financial_health"), dict)
            else {}
        )
        has_equity = not _missing_positive_observation(health.get("equity"))
        has_market_cap = not _missing_positive_observation(
            basic_data.get("market_cap_yi") or basic_data.get("market_cap")
        )
        has_pb = not _missing_positive_observation(
            basic_data.get("pb") or valuation_data.get("pb")
        )
        if not has_equity and not (has_market_cap and has_pb):
            gaps.append("observation_missing:equity_or_market_cap_and_pb")

    if method_id == "porter_bcg":
        gaps.append("native_defaults_used:porter_bcg.supplier_buyer_power")
        moat = dimensions.get("14_moat")
        moat_data = (
            moat.get("data")
            if isinstance(moat, Mapping) and isinstance(moat.get("data"), dict)
            else {}
        )
        scores = moat_data.get("scores") if isinstance(moat_data.get("scores"), dict) else {}
        for key in ("intangible", "switching", "scale"):
            if _missing_native_value(scores.get(key)):
                gaps.append(f"observation_missing:14_moat.scores.{key}")
        industry = dimensions.get("7_industry")
        industry_data = (
            industry.get("data")
            if isinstance(industry, Mapping)
            and isinstance(industry.get("data"), dict)
            else {}
        )
        if _missing_native_value(industry_data.get("growth")):
            gaps.append("observation_missing:7_industry.growth")
        cninfo = (
            industry_data.get("cninfo_metrics")
            if isinstance(industry_data.get("cninfo_metrics"), dict)
            else {}
        )
        if _missing_native_value(cninfo.get("total_mcap_yi")):
            gaps.append("observation_missing:7_industry.cninfo_metrics.total_mcap_yi")

    if method_id == "thesis_tracker":
        financial = dimensions.get("1_financials")
        financial_data = (
            financial.get("data")
            if isinstance(financial, Mapping)
            and isinstance(financial.get("data"), dict)
            else {}
        )
        if _numeric_observation_count(financial_data.get("revenue_history")) < 4:
            gaps.append("observation_missing:1_financials.revenue_history_4y")
        if not _numeric_observation_count(financial_data.get("roe_history")):
            gaps.append("observation_missing:1_financials.roe_history")
        kline = dimensions.get("2_kline")
        kline_data = (
            kline.get("data")
            if isinstance(kline, Mapping) and isinstance(kline.get("data"), dict)
            else {}
        )
        if _missing_native_value(kline_data.get("stage")):
            gaps.append("observation_missing:2_kline.stage")
        if _missing_native_value(financial_data.get("operating_cash_flow_yi")):
            gaps.append("native_proxy_used:thesis_tracker.fcf")

    if method_id == "catalyst_calendar":
        gaps.append("native_defaults_used:catalyst_calendar.standard_events")

    default_fields = {
        "three_statement": {
            "revenue_growth_y1", "revenue_growth_y2", "revenue_growth_y3",
            "revenue_growth_y4", "revenue_growth_y5", "gross_margin",
            "opex_pct_revenue", "tax_rate", "capex_pct_revenue",
            "dep_pct_revenue", "nwc_pct_revenue",
        },
        "quick_lbo": {
            "entry_multiple", "debt_multiple", "exit_multiple", "hold_years",
            "ebitda_growth", "interest_rate",
        },
        "accretion_dilution": {
            "premium_pct", "cash_pct", "synergies_yi", "new_debt_rate",
        },
    }
    if method_id in default_fields and not default_fields[method_id].issubset(assumptions):
        gaps.append(f"native_defaults_used:{method_id}")
    if method_id == "model_update" and not assumptions:
        gaps.append("native_defaults_used:model_update.demo_mode")
    if method_id in {"unit_economics", "value_creation_plan"}:
        gaps.append(f"native_defaults_used:{method_id}")
    return list(dict.fromkeys(gaps))


def _feature_quality_gaps(
    method_id: str,
    features: Mapping[str, Any],
    assumptions: Mapping[str, Any],
) -> list[str]:
    gaps: list[str] = []
    if method_id in {"dcf", "initiation_report", "ic_memo", "model_update"}:
        price_available = not _missing_positive_observation(features.get("price"))
        shares_available = not _missing_positive_observation(
            features.get("shares_outstanding_yi")
        )
        derivable_shares = price_available and not _missing_positive_observation(
            features.get("market_cap_yi")
        )
        if not price_available:
            gaps.append("observation_missing:price")
        if not shares_available and not derivable_shares:
            gaps.append("observation_missing:shares_or_market_cap_and_price")
    if method_id == "accretion_dilution":
        for key in ("price", "shares_outstanding_yi", "eps", "net_profit_latest_yi"):
            value = features.get(key)
            if _missing_native_value(value) or not isinstance(value, (int, float)) or float(value) <= 0:
                gaps.append(f"observation_missing:acquirer.{key}")
    if method_id == "quick_lbo":
        if _missing_positive_observation(features.get("ebitda_yi")):
            if _missing_positive_observation(features.get("revenue_latest_yi")):
                gaps.append("observation_missing:quick_lbo.ebitda_or_revenue")
            else:
                gaps.append("native_proxy_used:quick_lbo.ebitda")
        else:
            gaps.append("native_proxy_used:quick_lbo.ebitda_from_net_income")
    if method_id == "porter_bcg" and _missing_positive_observation(
        features.get("market_cap_yi")
    ):
        gaps.append("observation_missing:market_cap_yi")
    if method_id == "value_creation_plan" and not features.get("market_share"):
        gaps.append("observation_missing:market_share")
    if method_id == "model_update" and "target_price" in assumptions and not features.get(
        "price"
    ):
        gaps.append("observation_missing:price")
    if method_id == "thesis_tracker":
        if _missing_native_value(features.get("roe_last")) and not _missing_native_value(
            features.get("roe_latest")
        ):
            gaps.append("native_source_limitation:roe_last_alias")
    if method_id == "dd_checklist":
        if _missing_native_value(features.get("roe_last")) and not _missing_native_value(
            features.get("roe_latest")
        ):
            gaps.append("native_source_limitation:dd_checklist.roe_last_alias")
    if method_id == "quantitative_screen":
        style = str(assumptions.get("style") or "quality")
        if style in {"growth", "gulp", "short"} and _missing_native_value(
            features.get("roe_last")
        ) and not _missing_native_value(features.get("roe_latest")):
            gaps.append("native_source_limitation:roe_last_alias")
        if style == "growth" and _missing_native_value(
            features.get("eps_growth_3y")
        ) and not _missing_native_value(features.get("net_profit_growth_latest")):
            gaps.append("native_source_limitation:eps_growth_3y_alias")
    return gaps


def _native_result_quality_gaps(method_id: str, result: object) -> list[str]:
    if not isinstance(result, dict):
        return []
    gaps: list[str] = []
    if method_id == "comps" and int(result.get("peer_count") or 0) < 2:
        gaps.append("observation_missing:comps.peer_count")
    if method_id == "comps" and int(result.get("peer_count") or 0) >= 2 and not dict(
        result.get("peer_stats") or {}
    ):
        gaps.append("observation_missing:comps.peer_valuation_metrics")
    if method_id == "earnings_preview" and list(result.get("consensus_notes") or []):
        gaps.append("native_warning:earnings_preview.consensus_missing")
    if method_id == "model_update":
        for key in ("dcf_impact", "comps_impact"):
            impact = result.get(key)
            if not isinstance(impact, dict) or impact.get("available") is not True:
                gaps.append(f"native_warning:model_update.{key}_unavailable")
    if method_id == "dcf" and str(result.get("net_debt_bridge_note") or "").strip():
        gaps.append("native_source_limitation:dcf.net_debt_bridge_missing")
    return gaps


def _nested_valuation_quality_gaps(
    dcf_result: object,
    comps_result: object,
) -> list[str]:
    gaps: list[str] = []
    if not isinstance(dcf_result, dict) or dcf_result.get("error"):
        gaps.append("native_warning:nested_dcf_unavailable")
    else:
        gaps.extend(_native_result_quality_gaps("dcf", dcf_result))
    if not isinstance(comps_result, dict) or comps_result.get("error"):
        gaps.append("native_warning:nested_comps_unavailable")
    else:
        gaps.extend(_native_result_quality_gaps("comps", comps_result))
    return list(dict.fromkeys(gaps))


def _hard_input_gaps(method_id: str, assumptions: Mapping[str, Any]) -> list[str]:
    gaps: list[str] = []
    if method_id == "earnings_review" and "consensus" in assumptions:
        consensus = assumptions.get("consensus")
        if not isinstance(consensus, dict):
            return ["explicit_assumption_invalid:consensus"]
        for key in ("rev", "ni"):
            value = consensus.get(key)
            if _missing_native_value(value):
                gaps.append(f"explicit_assumption_missing:consensus.{key}")
            elif (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) <= 0
            ):
                gaps.append(f"explicit_assumption_invalid:consensus.{key}")
    if method_id == "thesis_tracker" and "direction" in assumptions and assumptions.get(
        "direction"
    ) not in {"long", "short"}:
        gaps.append("explicit_assumption_invalid:direction")
    if method_id == "quantitative_screen" and "style" in assumptions and assumptions.get(
        "style"
    ) not in {"value", "growth", "quality", "short", "gulp"}:
        gaps.append("explicit_assumption_invalid:style")
    if method_id == "accretion_dilution":
        target = assumptions.get("target")
        if not isinstance(target, dict):
            gaps.append("explicit_assumption_missing:target")
        else:
            gaps.extend(
                f"explicit_assumption_missing:target.{key}"
                for key in (
                    "ticker", "name", "shares_yi", "price", "net_income_yi",
                )
                if _missing_native_value(target.get(key))
            )
            for key in ("shares_yi", "price"):
                value = target.get(key)
                if not _missing_native_value(value) and _missing_positive_observation(value):
                    gaps.append(f"explicit_assumption_invalid:target.{key}")
            net_income = target.get("net_income_yi")
            if not _missing_native_value(net_income) and (
                isinstance(net_income, bool)
                or not isinstance(net_income, (int, float))
                or not math.isfinite(float(net_income))
            ):
                gaps.append("explicit_assumption_invalid:target.net_income_yi")
    return gaps


def execute_native_method(
    method_id: str,
    snapshot_rows: list[dict[str, Any]],
    symbols: list[str],
    inputs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if method_id not in UZI_NATIVE_METHOD_IDS:
        raise ValueError(f"Unsupported UZI 3.9.4 native method: {method_id}")
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
    completed = [
        row
        for row in results
        if isinstance(row.get("result"), dict)
        and not row["result"].get("error")
    ]
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
    assumptions = _method_assumptions(inputs, method_id)
    hard_gaps = _hard_input_gaps(method_id, assumptions)
    if hard_gaps:
        return {"symbol": symbol, "result": None, "data_gap": hard_gaps}
    quality_gaps = _method_quality_gaps(method_id, dimensions, assumptions)

    try:
        _initialize_native_imports()
        features = importlib.import_module("lib.stock_features").extract_features(raw, {})
        quality_gaps.extend(_feature_quality_gaps(method_id, features, assumptions))

        fin = importlib.import_module("lib.fin_models")
        workflow = importlib.import_module("lib.research_workflow")
        deep = importlib.import_module("lib.deep_analysis_methods")
        nested_dcf: dict[str, Any] | None = None
        nested_comps: dict[str, Any] | None = None
        if method_id in {"initiation_report", "ic_memo", "model_update"}:
            nested_dcf = fin.compute_dcf(features)
            nested_comps = fin.build_comps_table(features, _peer_features(raw))
            quality_gaps.extend(
                _nested_valuation_quality_gaps(nested_dcf, nested_comps)
            )
        if method_id == "dcf":
            result = fin.compute_dcf(features, assumptions or None)
        elif method_id == "comps":
            result = fin.build_comps_table(features, _peer_features(raw))
        elif method_id == "three_statement":
            result = fin.project_three_stmt(features, assumptions or None)
        elif method_id == "quick_lbo":
            result = fin.quick_lbo(features, **assumptions)
        elif method_id == "accretion_dilution":
            deal = dict(assumptions)
            target = dict(deal.pop("target"))
            acquirer = {
                **features,
                "shares_yi": features.get("shares_outstanding_yi"),
                "net_income_yi": features.get("net_profit_latest_yi"),
            }
            result = fin.accretion_dilution(acquirer, target, **deal)
        elif method_id == "initiation_report":
            result = workflow.build_initiating_coverage(
                features, raw, nested_dcf, nested_comps
            )
        elif method_id == "earnings_review":
            result = workflow.build_earnings_analysis(features, raw, consensus=assumptions.get("consensus"))
        elif method_id == "catalyst_calendar":
            result = workflow.build_catalyst_calendar(features, raw)
        elif method_id == "thesis_tracker":
            result = workflow.build_thesis_tracker(features, raw, direction=str(assumptions.get("direction") or "long"))
        elif method_id == "morning_note":
            result = workflow.build_morning_note(features, raw)
        elif method_id == "quantitative_screen":
            workflow_features = dict(features)
            if (
                not workflow_features.get("moat_known")
                and workflow_features.get("moat_total") is None
            ):
                # UZI 3.9.4 preserves a missing moat as None, while this
                # older internal screen still performs one direct comparison.
                # Keep raw/features truthful and normalize only its reader copy.
                workflow_features["moat_total"] = 0
            result = workflow.run_idea_screen(
                workflow_features,
                style=str(assumptions.get("style") or "quality"),
            )
        elif method_id == "sector_review":
            result = workflow.build_sector_overview(features, raw)
        elif method_id == "ic_memo":
            result = deep.build_ic_memo(
                features, raw, nested_dcf, nested_comps
            )
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
            result = importlib.import_module("lib.tier1.model_update").build_model_update(
                features, raw, assumptions or None, nested_dcf, nested_comps
            )
        else:
            raise AssertionError(method_id)
    except Exception as exc:
        return {
            "symbol": symbol,
            "result": None,
            "data_gap": list(
                dict.fromkeys([*quality_gaps, f"native_exception:{type(exc).__name__}"])
            ),
        }

    if isinstance(result, dict) and result.get("error"):
        quality_gaps.append("native_error")
    quality_gaps.extend(_native_result_quality_gaps(method_id, result))
    return {
        "symbol": symbol,
        "result": result,
        "data_gap": list(dict.fromkeys(quality_gaps)),
    }


def _execute_portfolio_method(method_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
    holdings: list[dict[str, Any]] = []
    for row in list(inputs.get("portfolio_holdings") or []):
        if not isinstance(row, dict):
            continue
        holding = dict(row)
        if not str(holding.get("ticker") or "").strip() and str(
            holding.get("symbol") or ""
        ).strip():
            holding["ticker"] = str(holding["symbol"]).strip()
        holdings.append(holding)
    gaps: list[str] = []
    if not holdings:
        gaps.append("portfolio_holdings_missing")
    if gaps:
        result = None
    else:
        try:
            _initialize_native_imports()
            if method_id == "returns_attribution":
                result = importlib.import_module(
                    "lib.tier1.returns_attrib"
                ).build_returns_attribution(holdings, inputs.get("benchmark_return"))
                if any(holding.get("weight") is None for holding in holdings):
                    gaps.append(
                        "native_defaults_used:returns_attribution.weight_normalization"
                    )
            elif method_id == "holding_rebalance":
                threshold = inputs.get("drift_threshold")
                result = importlib.import_module("lib.tier1.rebalance").build_rebalance(
                    holdings,
                    inputs.get("rebalance_targets"),
                    float(5.0 if threshold is None else threshold),
                )
                if any(holding.get("weight") is None for holding in holdings):
                    gaps.append(
                        "native_defaults_used:holding_rebalance.weight_normalization"
                    )
                if not isinstance(inputs.get("rebalance_targets"), dict):
                    gaps.append("native_defaults_used:holding_rebalance.equal_weight")
                gaps.append("native_defaults_used:holding_rebalance.fee_schedule")
            else:
                native_holdings: list[dict[str, Any]] = []
                for holding in holdings:
                    native_holding = dict(holding)
                    if "market_value_yuan" not in native_holding:
                        if holding.get("market_value") is not None:
                            native_holding["market_value_yuan"] = holding.get(
                                "market_value"
                            )
                        elif holding.get("value") is not None:
                            native_holding["market_value_yuan"] = holding.get("value")
                    native_holdings.append(native_holding)
                result = importlib.import_module(
                    "lib.deep_analysis_methods"
                ).build_portfolio_rebalance(
                    native_holdings, inputs.get("asset_class_targets")
                )
                if not isinstance(inputs.get("asset_class_targets"), dict):
                    gaps.append(
                        "native_defaults_used:portfolio_rebalance.target_allocation"
                    )
        except Exception as exc:
            result = None
            gaps.append(f"native_exception:{type(exc).__name__}")

    if method_id == "returns_attribution" and isinstance(result, dict):
        for ticker in list(result.get("missing_return_tickers") or []):
            gaps.append(f"native_warning:missing_return:{ticker}")
    if isinstance(result, dict) and result.get("error"):
        gaps.append("native_error")
    successful = isinstance(result, dict) and not result.get("error")
    return {
        "method_id": method_id,
        "source_commit": UZI_SOURCE_COMMIT,
        "implementation": "vendored_uzi_3_9_2_pure_function",
        "results": [{"symbol": None, "result": result, "data_gap": gaps}],
        "quality_status": (
            "available" if successful and not gaps else "degraded" if successful else "data_gap"
        ),
        "data_gap": gaps,
        "assumptions": {
            key: inputs.get(key)
            for key in ("benchmark_return", "rebalance_targets", "drift_threshold", "asset_class_targets")
            if key in inputs
        },
    }


def _initialize_native_imports() -> None:
    Uzi392FetcherRuntime(workspace_root=Path(__file__).resolve().parents[3] / "workspace")


def _dimension_available(value: object) -> bool:
    if not isinstance(value, dict) or value.get("error"):
        return False
    data = value.get("data")
    if not isinstance(data, dict) or not data or data.get("placeholder") is True:
        return False
    metadata_only = {
        "source",
        "sources",
        "status",
        "provider",
        "provider_id",
        "retrieved_at",
        "fetched_at",
        "as_of",
        "fallback",
        "cached",
    }
    return any(
        not str(key).startswith("_")
        and str(key) not in metadata_only
        and item is not None
        and item != ""
        for key, item in data.items()
    )


def _method_assumptions(inputs: Mapping[str, Any], method_id: str) -> dict[str, Any]:
    all_assumptions = inputs.get("method_assumptions")
    if isinstance(all_assumptions, dict) and isinstance(all_assumptions.get(method_id), dict):
        return dict(all_assumptions[method_id])
    if method_id == "model_update" and isinstance(inputs.get("model_updates"), dict):
        return dict(inputs["model_updates"])
    return {}


def _peer_features(raw: dict[str, Any]) -> list[dict[str, Any]]:
    peers = raw.get("similar_stocks")
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
