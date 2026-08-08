"""Pinned UZI command boundaries that are not single native-method calls."""

from __future__ import annotations

import importlib
from typing import Any, Mapping

from domain_packs.finance_ashare.subagents.uzi_392_native_methods import build_uzi_raw
from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import UZI_SOURCE_COMMIT, Uzi392FetcherRuntime


_PROMOTION_CONTEXT = ("朋友推荐", "群里说", "老师带", "内幕消息", "直播间", "跟单")


def execute_panel_only(snapshot_rows: list[dict[str, Any]], symbols: list[str]) -> dict[str, Any]:
    _initialize()
    results: list[dict[str, Any]] = []
    for symbol in symbols:
        raw = build_uzi_raw(snapshot_rows, symbol)
        gaps = _required_dimension_gaps(raw, ("0_basic", "1_financials"))
        if gaps:
            results.append({"symbol": symbol, "result": None, "data_gap": gaps})
            continue
        score_fns = importlib.import_module("lib.pipeline.score_fns")
        dimensions = score_fns.score_dimensions(raw)
        panel = score_fns.generate_panel(dimensions, raw)
        results.append(
            {
                "symbol": symbol,
                "result": {"dimensions": dimensions, "panel": panel},
                "data_gap": [],
            }
        )
    return _command_envelope("panel-only", results, phase="complete")


def execute_scan_trap(
    snapshot_rows: list[dict[str, Any]],
    symbols: list[str],
    *,
    query: str,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    promotion_context = [marker for marker in _PROMOTION_CONTEXT if marker in query]
    for symbol in symbols:
        raw = build_uzi_raw(snapshot_rows, symbol)
        trap = (raw.get("dimensions") or {}).get("18_trap")
        gaps = [] if _dimension_available(trap) else ["dimension_missing:18_trap"]
        data = (trap or {}).get("data") if isinstance(trap, dict) else None
        snippets = data.get("snippets") if isinstance(data, dict) else None
        evidence_rows = [
            row
            for values in (snippets or {}).values()
            for row in (values if isinstance(values, list) else [])
            if isinstance(row, dict) and any(str(row.get(key) or "").strip() for key in ("title", "body", "url"))
        ]
        if not gaps and not evidence_rows:
            gaps.append("trap_search_evidence_missing")
        if gaps:
            results.append({"symbol": symbol, "result": None, "data_gap": gaps})
            continue
        base_result = dict(data)
        adjusted_result = _escalate_trap_level(base_result, promotion_context)
        results.append(
            {
                "symbol": symbol,
                "result": {
                    "base_result": base_result,
                    "command_result": adjusted_result,
                    "promotion_context": promotion_context,
                },
                "data_gap": [],
            }
        )
    return _command_envelope("scan-trap", results, phase="complete")


def execute_segmental_model(
    snapshot_rows: list[dict[str, Any]],
    symbols: list[str],
    inputs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _initialize()
    segmental = importlib.import_module("lib.segmental_model")
    supplied = dict((inputs or {}).get("segmental_model") or {})
    results: list[dict[str, Any]] = []
    for symbol in symbols:
        raw = build_uzi_raw(snapshot_rows, symbol)
        gaps = _required_dimension_gaps(raw, ("0_basic", "1_financials", "5_chain"))
        if gaps:
            results.append({"symbol": symbol, "result": None, "data_gap": gaps})
            continue
        skeleton_object = segmental.discover_segments(raw)
        skeleton = skeleton_object.to_dict()
        markdown = segmental.render_skeleton_markdown(skeleton_object)
        if not supplied:
            results.append(
                {
                    "symbol": symbol,
                    "result": {"phase": "discovery", "skeleton": skeleton, "markdown": markdown, "validation": None},
                    "data_gap": ["segmental_model_assumptions_required"],
                }
            )
            continue
        validation = segmental.validate_model(supplied, raw)
        results.append(
            {
                "symbol": symbol,
                "result": {
                    "phase": "validated" if validation.get("passed") else "validation_failed",
                    "skeleton": skeleton,
                    "markdown": markdown,
                    "filled_model": supplied,
                    "validation": validation,
                },
                "data_gap": [] if validation.get("passed") else ["segmental_model_validation_failed"],
            }
        )
    phase = "validated" if supplied else "awaiting_assumptions"
    return _command_envelope("segmental-model", results, phase=phase)


def _command_envelope(command_id: str, results: list[dict[str, Any]], *, phase: str) -> dict[str, Any]:
    gaps = list(
        dict.fromkeys(
            f"{row.get('symbol') or 'unknown'}:{gap}"
            for row in results
            for gap in list(row.get("data_gap") or [])
        )
    )
    completed = [row for row in results if isinstance(row.get("result"), dict) and not row.get("data_gap")]
    return {
        "command_id": command_id,
        "source_commit": UZI_SOURCE_COMMIT,
        "implementation": "vendored_uzi_3_9_2_command_boundary",
        "phase": phase,
        "results": results,
        "quality_status": "available" if results and len(completed) == len(results) else "degraded" if any(row.get("result") for row in results) else "data_gap",
        "data_gap": gaps or ([] if results else ["target_missing"]),
    }


def _required_dimension_gaps(raw: dict[str, Any], dimensions: tuple[str, ...]) -> list[str]:
    values = dict(raw.get("dimensions") or {})
    return [f"dimension_missing:{dimension}" for dimension in dimensions if not _dimension_available(values.get(dimension))]


def _dimension_available(value: object) -> bool:
    return isinstance(value, dict) and not value.get("error") and isinstance(value.get("data"), dict) and bool(value["data"])


def _escalate_trap_level(base: dict[str, Any], context: list[str]) -> dict[str, Any]:
    result = dict(base)
    if not context:
        return result
    levels = ["🟢 安全", "🟡 注意", "🟠 警惕", "🔴 高度可疑"]
    current = str(result.get("trap_level") or "")
    if current in levels:
        result["trap_level"] = levels[min(levels.index(current) + 1, len(levels) - 1)]
    result["context_escalated"] = True
    result["context_reason"] = f"用户描述包含高风险推广语境：{'、'.join(context)}"
    return result


def _initialize() -> None:
    from pathlib import Path

    Uzi392FetcherRuntime(workspace_root=Path(__file__).resolve().parents[3] / "workspace")


__all__ = ["execute_panel_only", "execute_scan_trap", "execute_segmental_model"]
