from __future__ import annotations

import importlib
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from research_system.market_data.native_projection import (
    PROJECTION_VERSION,
    native_payload,
    native_quality,
    project_native_result,
)

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


def native_snapshot_usable(snapshot: Any) -> bool:
    validation = dict(getattr(snapshot, "validation_json", None) or {})
    quality = native_quality(snapshot)
    if validation.get("native_available") is False:
        return False
    if quality in _UZI_UNAVAILABLE_QUALITIES:
        return False
    return bool(native_payload(snapshot))


def _native_result(snapshot: Any, *, version: str = PROJECTION_VERSION) -> dict[str, Any]:
    return project_native_result(snapshot, version=version)


def evaluate_uzi_collection_gate(
    snapshots: Mapping[str, Any],
    *,
    profile: str = "medium",
    market: str | None = None,
    projection_version: str = PROJECTION_VERSION,
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
            dimension: _native_result(snapshot, version=projection_version)
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
        native_result = raw["dimensions"].get(dimension) or {}
        if native_result.get("data"):
            continue
        has_error = bool(native_result.get("_timeout") or native_result.get("error"))
        issues.append(
            {
                "severity": (
                    "warning" if has_error else "critical"
                ),
                "check": "empty_dims",
                "dimension": dimension,
                "message": (
                    "维度数据为空" + ("（上游失败）" if has_error else "")
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
        kline = (raw["dimensions"].get("2_kline") or {}).get("data") or {}
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
        financials = (raw["dimensions"].get("1_financials") or {}).get("data") or {}
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
