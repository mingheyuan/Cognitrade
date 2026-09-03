from __future__ import annotations

"""Pure collection-job diagnostics used by API and UI summaries."""

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

_GAP_STATUSES = {"failed", "partial", "completed_partial", "data_gap"}
_ACTIVE_STATUSES = {"queued", "pending", "running", "processing", "leased"}
_SUCCESS_STATUSES = {"completed", "succeeded", "published"}
_SKIPPED_STATUSES = {"skipped", "cancelled"}
_NON_BLOCKING_GAP_CODES = {"strict_contract_gap", "historical_partial"}


def classify_collection_failure(
    *,
    error_code: str | None,
    error_message: str | None,
    provider_status: str | None = None,
    metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify one failed slot without pretending raw data is absent.

    Collection has three independent gates: acquisition, normalization/query
    proof, and strict evidence/contract validation.  Returning this small
    stable taxonomy lets the API and UI explain which gate failed while the
    original sanitized diagnostic remains available for debugging.
    """

    code = str(error_code or "").strip().lower()
    message = str(error_message or "").strip()
    lowered = message.lower()
    provider = str(provider_status or "").strip().lower()
    metric_values = dict(metrics or {})
    raw_data_present = bool(metric_values.get("raw_data_present"))

    if code == "historical_partial" and raw_data_present:
        kind, stage, label = "partial_available", "acquisition", "UZI 核心可用 · 可选字段缺失"
    elif code == "strict_contract_gap" and raw_data_present:
        kind, stage, label = "audit_only", "audit", "UZI 可用 · 扩展审计提示"
    elif "timeout" in code or "timeout" in provider or "timeout" in lowered:
        kind, stage, label = "source_timeout", "acquisition", "数据源超时"
    elif code in {"provider_error", "provider_data_gap", "collector_result_missing"} or provider in {
        "provider_error",
        "missing_result",
    }:
        kind, stage, label = "source_unavailable", "acquisition", "数据源未返回"
    elif any(
        marker in lowered
        for marker in (
            "grade c evidence",
            "no a/b evidence",
            "evidence record",
            "evidence from",
        )
    ):
        kind, stage, label = "evidence_gap", "audit", "UZI 可用 · 证据审计提示"
    elif any(
        marker in lowered
        for marker in (
            "query proof",
            "query_window",
            "required source",
            "查询证明",
        )
    ):
        kind, stage, label = "query_proof_gap", "audit", "UZI 可用 · 查询审计提示"
    elif raw_data_present:
        kind, stage, label = (
            "normalization_gap",
            "normalization",
            "UZI 可用 · 字段审计提示",
        )
    else:
        kind, stage, label = "source_unavailable", "acquisition", "数据源未返回"

    return {
        "failure_kind": kind,
        "failure_stage": stage,
        "failure_label": label,
        "raw_data_present": raw_data_present,
    }


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _status(item: Any) -> str:
    return str(_value(item, "status", "unknown") or "unknown").lower()


def _attempt(item: Any) -> int:
    return int(_value(item, "attempt_count", _value(item, "attempt", 0)) or 0)


def _max_attempts(item: Any) -> int:
    return max(1, int(_value(item, "max_attempts", 3) or 3))


def task_is_retryable(item: Any) -> bool:
    return bool(
        _status(item) in _GAP_STATUSES
        and str(_value(item, "last_error_code", "") or "").lower()
        not in _NON_BLOCKING_GAP_CODES
        and _value(item, "retryable", False)
        and _attempt(item) < _max_attempts(item)
    )


def task_is_blocked(item: Any) -> bool:
    return bool(
        _status(item) in _GAP_STATUSES
        and str(_value(item, "last_error_code", "") or "").lower()
        not in _NON_BLOCKING_GAP_CODES
        and not task_is_retryable(item)
    )


def _bucket_rows(
    tasks: Iterable[Any],
    *,
    key_name: str,
    fallback: str,
    limit: int = 12,
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for task in tasks:
        key = str(_value(task, key_name, "") or fallback)
        bucket = buckets.setdefault(
            key,
            {
                "key": key,
                "count": 0,
                "retryable_count": 0,
                "blocked_count": 0,
                "active_count": 0,
                "sample_message": None,
            },
        )
        bucket["count"] += 1
        if task_is_retryable(task):
            bucket["retryable_count"] += 1
        elif task_is_blocked(task):
            bucket["blocked_count"] += 1
        elif _status(task) in _ACTIVE_STATUSES:
            bucket["active_count"] += 1
        if not bucket["sample_message"]:
            message = _value(task, "last_error_message", _value(task, "error_message"))
            bucket["sample_message"] = str(message)[:500] if message else None
    return sorted(
        buckets.values(),
        key=lambda row: (-int(row["count"]), str(row["key"])),
    )[:limit]


def summarize_tasks(tasks: Iterable[Any]) -> dict[str, Any]:
    task_rows = list(tasks)
    statuses = Counter(_status(task) for task in task_rows)
    retryable = sum(task_is_retryable(task) for task in task_rows)
    blocked = sum(task_is_blocked(task) for task in task_rows)
    active = sum(_status(task) in _ACTIVE_STATUSES for task in task_rows)
    audit_only = sum(
        str(_value(task, "last_error_code", "") or "").lower()
        in _NON_BLOCKING_GAP_CODES
        for task in task_rows
    )
    succeeded = sum(_status(task) in _SUCCESS_STATUSES for task in task_rows) + audit_only
    skipped = sum(_status(task) in _SKIPPED_STATUSES for task in task_rows)
    return {
        "total": len(task_rows),
        "by_status": dict(statuses),
        "active": active,
        "succeeded": succeeded,
        "skipped": skipped,
        "audit_only": audit_only,
        "retryable_failed": retryable,
        "blocked_failed": blocked,
        "by_error_code": _bucket_rows(
            (task for task in task_rows if _status(task) in _GAP_STATUSES),
            key_name="last_error_code",
            fallback="unknown_error",
        ),
        "by_dimension": _bucket_rows(
            task_rows,
            key_name="dimension",
            fallback="unknown_dimension",
        ),
    }


def summarize_fund_portfolio(
    *,
    checkpoint: Mapping[str, Any],
    child_tasks: Iterable[Any],
    dimensions_per_holding: int,
    top_n: int = 10,
) -> dict[str, Any]:
    holdings = list(checkpoint.get("holdings") or [])
    total_holdings = int(checkpoint.get("child_total_count") or len(holdings) or top_n)
    ready_holdings = int(checkpoint.get("child_complete_count") or 0)
    missing_by_symbol = {
        str(symbol): [str(item) for item in (dimensions or [])]
        for symbol, dimensions in dict(
            checkpoint.get("child_missing_by_symbol") or {}
        ).items()
    }
    if not missing_by_symbol and holdings:
        missing_by_symbol = {
            str(holding.get("symbol") or ""): [
                str(item) for item in (holding.get("missing_dimensions") or [])
            ]
            for holding in holdings
            if holding.get("missing_dimensions")
        }

    child_runs = list(checkpoint.get("child_runs") or [])
    active_runs = [run for run in child_runs if _status(run) in _ACTIVE_STATUSES]
    last_runs = active_runs or list(checkpoint.get("last_child_runs") or [])
    current_child_ids = {
        str(run.get("job_id") or "")
        for run in last_runs
        if isinstance(run, Mapping) and str(run.get("job_id") or "")
    }
    all_child_tasks = list(child_tasks)
    current_tasks = [
        task
        for task in all_child_tasks
        if not current_child_ids
        or str(_value(task, "job_id", "") or "") in current_child_ids
    ]
    if not missing_by_symbol and active_runs:
        # The parent only persists child_missing_by_symbol once an equity wave
        # settles.  Until then, derive the live gap set from the current child
        # tasks so list polling does not report every slot as ready (or blocked)
        # while the child wave is still queued/running.
        for task in current_tasks:
            if _status(task) in _SUCCESS_STATUSES | _SKIPPED_STATUSES:
                continue
            symbol = str(_value(task, "symbol", "") or "")
            dimension = str(
                _value(task, "dimension", _value(task, "dimension_id", "")) or ""
            )
            if symbol and dimension:
                missing_by_symbol.setdefault(symbol, []).append(dimension)
    # child_tasks are ordered by job id by the service query, so a later retry
    # wave overwrites only the slots it actually retried.  Unselected holdings
    # retain the latest task from their previous wave.
    tasks_by_slot = {
        (
            str(_value(task, "symbol", "") or ""),
            str(_value(task, "dimension", _value(task, "dimension_id", "")) or ""),
        ): task
        for task in all_child_tasks
    }
    for task in current_tasks:
        tasks_by_slot[
            (
                str(_value(task, "symbol", "") or ""),
                str(
                    _value(
                        task,
                        "dimension",
                        _value(task, "dimension_id", ""),
                    )
                    or ""
                ),
            )
        ] = task

    retryable_slots = 0
    blocked_slots = 0
    active_slots = 0
    relevant_tasks: list[Any] = []
    for symbol, dimensions in missing_by_symbol.items():
        for dimension in dimensions:
            task = tasks_by_slot.get((symbol, dimension))
            if task is None:
                blocked_slots += 1
                continue
            relevant_tasks.append(task)
            if _status(task) in _ACTIVE_STATUSES:
                active_slots += 1
            elif task_is_retryable(task):
                retryable_slots += 1
            else:
                blocked_slots += 1

    missing_slots = sum(len(dimensions) for dimensions in missing_by_symbol.values())
    total_slots = max(0, total_holdings * max(0, int(dimensions_per_holding)))
    ready_slots = max(0, total_slots - missing_slots)
    attempt = max(
        [int(run.get("attempt") or 0) for run in last_runs if isinstance(run, Mapping)]
        or [0]
    )
    diagnostics = summarize_tasks(relevant_tasks)
    return {
        "total_holdings": total_holdings,
        "ready_holdings": ready_holdings,
        "dimensions_per_holding": max(0, int(dimensions_per_holding)),
        "total_slots": total_slots,
        "ready_slots": ready_slots,
        "missing_slots": missing_slots,
        "retryable_slots": retryable_slots,
        "blocked_slots": blocked_slots,
        "active_slots": active_slots,
        "attempt": attempt,
        "current_child_job_ids": sorted(current_child_ids),
        "by_error_code": diagnostics["by_error_code"],
        "by_dimension": diagnostics["by_dimension"],
    }


__all__ = [
    "classify_collection_failure",
    "summarize_fund_portfolio",
    "summarize_tasks",
    "task_is_blocked",
    "task_is_retryable",
]
