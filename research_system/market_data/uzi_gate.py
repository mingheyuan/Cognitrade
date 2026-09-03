from __future__ import annotations

import importlib
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from research_system.market_data.historical import is_safe_degradation_manifest


_UZI_UNAVAILABLE_QUALITIES = frozenset(
    {
        "assumption_only",
        "data_gap",
        "error",
        "failed",
        "missing",
        "missing_result",
        "no_data",
        "parse_failed",
        "provider_unavailable",
        "rate_limited",
        "timeout",
    }
)


@lru_cache(maxsize=1)
def _native_integrity_module():
    """Load the pinned upstream module instead of maintaining a host copy."""

    from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import (
        Uzi392FetcherRuntime,
    )

    workspace_root = Path(__file__).resolve().parents[2]
    Uzi392FetcherRuntime(workspace_root=workspace_root)
    return importlib.import_module("lib.data_integrity")


def native_quality(snapshot: Any) -> str:
    query_manifest = dict(getattr(snapshot, "query_manifest_json", None) or {})
    validation = dict(getattr(snapshot, "validation_json", None) or {})
    quality = (
        str(
            query_manifest.get("native_quality_status")
            or validation.get("native_quality_status")
            or validation.get("requested_status")
            or getattr(snapshot, "status", "")
            or ""
        )
        .strip()
        .lower()
    )
    return quality or "data_gap"


def native_payload(snapshot: Any) -> dict[str, Any]:
    payload = dict(getattr(snapshot, "payload_json", None) or {})
    query_manifest = dict(getattr(snapshot, "query_manifest_json", None) or {})
    outcome = str(query_manifest.get("collection_outcome") or "").strip().lower()
    if outcome == "verified_empty":
        observation = query_manifest.get("native_empty_observation")
        return (
            dict(observation)
            if isinstance(observation, Mapping)
            else {"observation_status": "verified_empty"}
        )
    if outcome in {"retryable_failure", "unavailable"}:
        return {}
    # ``raw`` is the persisted computation payload and may contain missing-only
    # recovery fields. The envelope below is retained for native metadata and
    # exact self-review error placement.
    raw = payload.get("raw")
    if isinstance(raw, Mapping):
        return dict(raw)
    envelope = payload.get("_uzi_raw_result")
    if isinstance(envelope, Mapping) and isinstance(envelope.get("data"), Mapping):
        return dict(envelope["data"])
    if str(payload.get("availability") or "").strip().lower() in {
        "historically_unavailable",
        "provider_unavailable",
    }:
        return {}
    return {
        str(key): value
        for key, value in payload.items()
        if not str(key).startswith("_")
    }


def native_snapshot_usable(snapshot: Any) -> bool:
    validation = dict(getattr(snapshot, "validation_json", None) or {})
    quality = native_quality(snapshot)
    if validation.get("native_available") is False:
        return False
    if quality in _UZI_UNAVAILABLE_QUALITIES:
        return False
    return bool(native_payload(snapshot))


def _native_result(snapshot: Any) -> dict[str, Any]:
    quality = native_quality(snapshot)
    payload = native_payload(snapshot)
    stored_payload = dict(getattr(snapshot, "payload_json", None) or {})
    envelope = stored_payload.get("_uzi_raw_result")
    result = dict(envelope) if isinstance(envelope, Mapping) else {}
    result["data"] = payload
    result.setdefault(
        "source", str(getattr(snapshot, "source_version", None) or "database")
    )
    query_manifest = dict(getattr(snapshot, "query_manifest_json", None) or {})
    if query_manifest.get("collection_outcome") == "retryable_failure":
        failure = query_manifest.get("provider_failure")
        message = failure.get("message") if isinstance(failure, Mapping) else None
        if not result.get("error"):
            result["error"] = str(message or "historical provider failure")
    result.setdefault("fallback", quality in _UZI_UNAVAILABLE_QUALITIES)
    return result


def evaluate_uzi_collection_gate(
    snapshots: Mapping[str, Any],
    *,
    profile: str = "medium",
    market: str | None = None,
) -> dict[str, Any]:
    """Evaluate the data-stage gates used by pinned UZI 3.9.4.

    Bundle publication happens before panel/synthesis exist, so this mirrors
    only UZI's data-integrity and profile-aware data self-review checks.  The
    remaining native self-review checks still run in the analysis workflow.
    Cognitrade-only PIT/evidence/shape checks are intentionally excluded.
    """

    selected_profile = str(profile or "medium").strip().lower()
    if selected_profile not in {"lite", "medium", "deep"}:
        raise ValueError("UZI gate profile must be lite, medium or deep")

    raw = {
        "dimensions": {
            dimension: _native_result(snapshot)
            for dimension, snapshot in snapshots.items()
            if snapshot is not None and dimension != "similar_stocks"
        }
    }
    module = _native_integrity_module()
    integrity = dict(module.validate(raw))

    profile_module = importlib.import_module("lib.analysis_profile")
    enabled = set(profile_module.get_profile(selected_profile).fetchers_enabled)
    enabled_numbered = {dimension for dimension in enabled if dimension[:1].isdigit()}
    enabled_numbers = {
        int(dimension.split("_", 1)[0]) for dimension in enabled_numbered
    }
    issues: list[dict[str, Any]] = []

    present_numbers = {
        int(dimension.split("_", 1)[0])
        for dimension, snapshot in snapshots.items()
        if snapshot is not None and dimension.split("_", 1)[0].isdigit()
    }
    for dimension_number in sorted(enabled_numbers - present_numbers):
        issues.append(
            {
                "severity": "critical",
                "check": "all_dims_exist",
                "dimension": f"{dimension_number}_",
                "message": "应跑维度完全缺失",
            }
        )

    # Native check_empty_dims iterates every dimension present in raw_data and
    # scopes numeric keys by their integer prefix. Therefore 6_fund_holders is
    # checked in medium/deep together with 6_research, although it is a bonus
    # collector rather than a separate required number in check_all_dims_exist.
    for dimension, snapshot in sorted(snapshots.items()):
        if snapshot is None or dimension == "similar_stocks":
            continue
        prefix = dimension.split("_", 1)[0]
        if prefix.isdigit() and int(prefix) not in enabled_numbers:
            continue
        if native_payload(snapshot):
            continue
        native_result = raw["dimensions"].get(dimension) or {}
        has_error = bool(native_result.get("_timeout") or native_result.get("error"))
        safe_historical_gap = is_safe_degradation_manifest(
            getattr(snapshot, "query_manifest_json", None)
        )
        issues.append(
            {
                "severity": (
                    "warning" if has_error or safe_historical_gap else "critical"
                ),
                "check": "empty_dims",
                "dimension": dimension,
                "message": (
                    "维度历史数据不可恢复，已从评分排除"
                    if safe_historical_gap
                    else "维度数据为空" + ("（上游失败）" if has_error else "")
                ),
            }
        )

    # Mirror the two market-specific data checks in UZI self_review.py.  The
    # generic integrity report above is recovery input, not an independent
    # report blocker; upstream only turns it into a gate through the checks
    # below (empty dimensions and profile-aware coverage).
    selected_market = str(market or "").strip().upper()
    if not selected_market:
        selected_market = (
            str(
                next(
                    (
                        getattr(snapshot, "market", "")
                        for snapshot in snapshots.values()
                        if snapshot is not None
                    ),
                    "",
                )
            )
            .strip()
            .upper()
        )
    if selected_market in {"H", "HK", "HK_EQUITY"}:
        kline = (
            native_payload(snapshots.get("2_kline")) if snapshots.get("2_kline") else {}
        )
        kline_count = kline.get("kline_count", 0)
        stage = kline.get("stage", "—")
        if kline_count == 0:
            issues.append(
                {
                    "severity": "critical",
                    "check": "hk_kline_populated",
                    "dimension": "2_kline",
                    "message": "港股 kline_count=0",
                }
            )
        elif stage == "—" and kline_count > 60:
            issues.append(
                {
                    "severity": "warning",
                    "check": "hk_kline_populated",
                    "dimension": "2_kline",
                    "message": "港股有 K 线数据但 stage 未分类",
                }
            )
        financials = (
            native_payload(snapshots.get("1_financials"))
            if snapshots.get("1_financials")
            else {}
        )
        if not financials:
            issues.append(
                {
                    "severity": "critical",
                    "check": "hk_financials_populated",
                    "dimension": "1_financials",
                    "message": "港股财务维度完全为空",
                }
            )
        elif not financials.get("roe_history"):
            issues.append(
                {
                    "severity": "warning",
                    "check": "hk_financials_populated",
                    "dimension": "1_financials",
                    "message": "港股 roe_history 缺失",
                }
            )

    checks = [row for row in list(module.CRITICAL_CHECKS) if row[0] in enabled]
    passed = 0
    for dimension, path, _label, _critical in checks:
        value = module._get(
            (raw["dimensions"].get(dimension) or {}).get("data") or {}, path
        )
        if not module._is_missing(value):
            passed += 1
    coverage_pct = round(passed / len(checks) * 100, 0) if checks else 0
    if coverage_pct < 60:
        severity = "critical" if coverage_pct < 40 else "warning"
        if selected_profile == "lite" and severity == "critical":
            severity = "warning"
        issues.append(
            {
                "severity": severity,
                "check": "coverage_threshold",
                "dimension": "overall",
                "message": f"UZI 数据完整性 {coverage_pct:.0f}%",
            }
        )

    critical_count = sum(row["severity"] == "critical" for row in issues)
    warning_count = sum(row["severity"] == "warning" for row in issues)
    # Pinned UZI 3.9.4's executable gate blocks critical only.  Warnings must
    # be acknowledged and remain visible in the native report banner, but do
    # not prevent HTML assembly (assemble_report.py::assemble).
    gate_passed = critical_count == 0
    return {
        **integrity,
        "coverage_pct": coverage_pct,
        "passed_checks": passed,
        "total_checks": len(checks),
        "profile": selected_profile,
        "issues": issues,
        "critical_count": critical_count,
        "warning_count": warning_count,
        "gate_passed": gate_passed,
        "gate_policy": f"uzi-3.9.4-native-data-self-review-{selected_profile}",
    }


__all__ = [
    "evaluate_uzi_collection_gate",
    "native_payload",
    "native_quality",
    "native_snapshot_usable",
]
