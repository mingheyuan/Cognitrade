"""Extract immutable UZI report conditions; never generate another research verdict."""

from __future__ import annotations

import math
import re
from copy import deepcopy
from datetime import date
from typing import Any

from research_system.strategies.contracts import StrategyDocumentV2

POSITIVE_VERDICTS = frozenset({"值得重仓", "可以蹲一蹲", "可以蹲（偏弱）"})
NATIVE_VERDICTS = POSITIVE_VERDICTS | {
    "观望偏多",
    "观望中性",
    "观望偏空",
    "谨慎",
    "回避",
}
_PIT_ERRORS = {
    "point_in_time_unverified",
    "point_in_time_violation",
    "fund_symbol_mismatch",
    "report_symbol_mismatch",
}


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _price(value: Any) -> float | None:
    """Only upstream scalar CNY values are executable, not ranges or prose."""
    if isinstance(value, str):
        value = value.strip()
        if not re.fullmatch(r"[¥￥]?\s*\d+(?:\.\d+)?", value):
            return None
        value = value.lstrip("¥￥").strip()
    price = _number(value)
    return price if price is not None and price > 0 else None


def native_synthesis(source: dict[str, Any], symbol: str) -> tuple[dict[str, Any], str]:
    direct = _record(source.get("synthesis"))
    if direct:
        return direct, "synthesis"
    for index, result in enumerate(source.get("results") or []):
        candidate = _record(_record(result).get("synthesis"))
        ticker = str(candidate.get("ticker") or _record(result).get("symbol") or "")
        if candidate and (
            ticker == symbol or ticker.split(".")[0] == symbol.split(".")[0]
        ):
            return candidate, f"results[{index}].synthesis"
    if "verdict_label" in source and "dashboard" in source:
        return source, "$"
    return {}, "synthesis"


def _reference_price_errors(
    evidence: dict[str, Any],
    *,
    symbol: str,
    as_of: date,
    prices: dict[str, float | None],
) -> list[str]:
    """Verify the actual 0_basic anchor used by the pinned native battle plan.

    Native score_fns reads 0_basic.data.price, not its qfq technical candles.
    The orchestration boundary verifies the fixed Bundle/snapshot; this layer
    checks that its evidence really supports these exact executable prices.
    """
    errors: list[str] = []
    anchor = _price(evidence.get("price"))
    if evidence.get("status") != "verified" or anchor is None:
        errors.append("report_reference_price_unverified")
    if str(evidence.get("subject_code") or "").split(".")[0] != symbol.split(".")[0]:
        errors.append("report_reference_price_symbol_mismatch")
    if (
        evidence.get("basis"),
        evidence.get("currency"),
        evidence.get("price_unit"),
    ) != ("raw_unadjusted", "CNY", "per_share") or evidence.get(
        "source_field"
    ) != "0_basic.data.price":
        errors.append("report_reference_price_basis_unverified")
    try:
        trade_day = date.fromisoformat(str(evidence.get("trade_date") or ""))
        report_day = date.fromisoformat(str(evidence.get("as_of_date") or ""))
        if report_day != as_of or trade_day > as_of:
            errors.append("report_reference_price_date_mismatch")
    except ValueError:
        errors.append("report_reference_price_date_unverified")
    if not all(
        evidence.get(key) for key in ("provider", "snapshot_id", "bundle_id")
    ) or any(
        not re.fullmatch(r"[0-9a-fA-F]{64}", str(evidence.get(key) or ""))
        for key in ("snapshot_content_hash", "bundle_manifest_hash")
    ):
        errors.append("report_reference_price_lineage_missing")
    if anchor is not None and all(prices.values()):
        expected = {
            key: round(anchor * factor, 2)
            for key, factor in (("entry", 0.92), ("stop", 0.85), ("target", 1.25))
        }
        if any(
            not math.isclose(prices[key], expected[key], abs_tol=1e-9, rel_tol=0)
            for key in expected
        ):
            errors.append("battle_plan_reference_price_mismatch")
    return errors


def report_condition_calculation(
    node, report_set, document: StrategyDocumentV2
) -> dict[str, Any]:
    source = _record(node.source_json)
    synthesis, root = native_synthesis(source, node.subject_code)
    battle_plan = _record(_record(synthesis.get("dashboard")).get("battle_plan"))
    verdict_label = str(synthesis.get("verdict_label") or "")
    # Upstream adds a school-disagreement suffix; score rounding is not authoritative.
    verdict = verdict_label.split("·", 1)[0].strip()
    errors: list[str] = []
    if node.status not in {"completed", "available", "final"}:
        errors.append("holding_report_unavailable")
    if source.get("report_allowed") is not True:
        errors.append("report_not_allowed")
    if (_number(source.get("critical_count")) or 0) > 0:
        errors.append("critical_issues_present")
    gaps = {
        *map(str, node.data_gaps_json or []),
        *map(str, report_set.data_gaps_json or []),
    }
    errors.extend(sorted(gaps & _PIT_ERRORS))
    ticker = str(synthesis.get("ticker") or "")
    if ticker and ticker.split(".")[0] != node.subject_code.split(".")[0]:
        errors.append("report_symbol_mismatch")
    if not re.fullmatch(r"\d{6}(?:\.(?:SH|SZ|BJ))?", node.subject_code):
        errors.append("report_condition_market_unsupported")
    # A holding context link is not the native analysis artifact.  Production
    # nodes retain its ID separately while content_hash refers to the native
    # content; never pair the context ID with that unrelated hash.
    canonical_id = source.get("canonical_artifact_id")
    canonical_hash = source.get("canonical_content_hash")
    has_canonical = (
        "canonical_artifact_id" in source or "canonical_content_hash" in source
    )
    artifact_id = canonical_id if has_canonical else node.artifact_id
    content_hash = canonical_hash if has_canonical else node.content_hash
    if not artifact_id or not content_hash:
        errors.append("report_artifact_lineage_missing")
    if has_canonical and canonical_hash and canonical_hash != node.content_hash:
        errors.append("report_artifact_lineage_mismatch")
    if verdict not in NATIVE_VERDICTS:
        errors.append("native_verdict_missing_or_unknown")
    prices = {key: _price(battle_plan.get(key)) for key in ("entry", "stop", "target")}
    if any(value is None for value in prices.values()):
        errors.append("battle_plan_prices_not_executable")
    elif not (prices["stop"] < prices["entry"] < prices["target"]):
        errors.append("battle_plan_price_order_invalid")
    reference_price_evidence = deepcopy(_record(source.get("reference_price_evidence")))
    price_errors = _reference_price_errors(
        reference_price_evidence,
        symbol=node.subject_code,
        as_of=report_set.as_of_date,
        prices=prices,
    )
    errors.extend(price_errors)
    weight = _number(node.disclosed_weight_pct)
    if weight is None or not 0 <= weight <= 100:
        errors.append("original_disclosed_weight_unavailable")
    plan = {
        "schema_version": "1",
        "model": document.report_signal.model,
        "extractor_version": document.report_signal.extractor_version,
        "uzi_source_commit": document.pins.uzi_source_commit,
        "subject_code": node.subject_code,
        "source_report_set_id": report_set.id,
        "source_report_revision": report_set.revision,
        "source_node_id": node.id,
        "source_artifact_id": artifact_id,
        "source_content_hash": content_hash,
        "context_link_artifact_id": source.get("context_link_artifact_id"),
        "source_field": f"{root}.dashboard.battle_plan",
        "report_as_of": report_set.as_of_date.isoformat(),
        "native_verdict": verdict,
        "native_verdict_label": verdict_label,
        "native_overall_score": synthesis.get("overall_score"),
        "native_battle_plan": deepcopy(battle_plan),
        "prices": prices,
        "currency": "CNY" if not price_errors else None,
        "price_unit": "per_share" if not price_errors else None,
        "adjustment": "raw" if not price_errors else None,
        "price_basis_verified": not price_errors,
        "reference_price_evidence": reference_price_evidence,
        "suggested_collection": {
            "symbols": [node.subject_code],
            "market": "CN_EQUITY",
            "dimensions": ["0_basic"],
            "start_date": report_set.as_of_date.isoformat(),
            "end_date": report_set.as_of_date.isoformat(),
            "mode": "force_all",
        }
        if price_errors and reference_price_evidence.get("status") != "not_evaluated"
        else None,
        "original_disclosed_weight_pct": weight,
        "entry_fraction": document.report_signal.entry_fraction,
        "max_single_stock_pct": document.execution.max_single_stock_pct,
        "entry_permitted": not errors and verdict in POSITIVE_VERDICTS,
        "executable": not errors,
        "errors": errors,
        "execution_rules": document.report_signal.model_dump(mode="json"),
    }
    score = _number(synthesis.get("overall_score"))
    state = "BLOCKED" if errors else "ALLOW_BUY" if plan["entry_permitted"] else "WATCH"
    return {
        "subject_code": node.subject_code,
        "state": state,
        "score_lower_bound": score if score is not None else 0.0,
        "score_upper_bound": score if score is not None else 0.0,
        "confidence": float(node.confidence or 0.0),
        "coverage": 0.0 if errors else 1.0,
        "reasons": errors
        or [
            "native_positive_verdict"
            if plan["entry_permitted"]
            else "native_verdict_no_new_entry"
        ],
        "execution_plan": plan,
    }
