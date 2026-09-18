from __future__ import annotations

"""Pure collection-job diagnostics used by API and UI summaries."""

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from research_system.market_data.historical import (
    capability_for_dimension,
    historical_provider_revision,
)

_GAP_STATUSES = {"failed", "partial", "completed_partial", "data_gap"}
_ACTIVE_STATUSES = {
    "queued",
    "pending",
    "running",
    "processing",
    "leased",
    "cancelling",
}
_SUCCESS_STATUSES = {"completed", "succeeded", "published"}
_SKIPPED_STATUSES = {"skipped", "cancelled"}
_NON_BLOCKING_GAP_CODES = {"strict_contract_gap", "historical_partial"}
_NON_RETRYABLE_AUDIT_CODES = {"strict_contract_gap"}
_LEGACY_HISTORICAL_PROVIDER_GAP_CODES = {
    "historical_unavailable",
    "provider_data_gap",
}


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
    reason = str(metric_values.get("reason_code") or code).strip().lower()
    outcome = str(metric_values.get("collection_outcome") or "").strip().lower()
    source_timed_out = any("timeout" in value for value in (code, provider, lowered))
    source_failed = (
        bool(
            {code, reason}
            & {
                "historical_provider_failure",
                "provider_error",
                "provider_data_gap",
                "collector_result_missing",
            }
        )
        or provider
        in {
            "provider_error",
            "provider_unavailable",
            "missing_result",
            "rate_limited",
            "parse_failed",
        }
        or outcome == "retryable_failure"
        or any("timeout" in value for value in (code, provider))
    )

    # A saved raw response can be an error envelope or an older usable snapshot.
    # Neither can turn this request's explicit acquisition failure into an audit
    # hint. Inspect the aggregate result, not optional failures in source_trace.
    if source_failed:
        kind, stage, label = (
            ("source_timeout", "acquisition", "数据源超时")
            if source_timed_out
            else ("source_unavailable", "acquisition", "数据源未返回")
        )
    elif reason in {
        "moat_basic_dependency_missing", "moat_archive_dependency_missing",
        "macro_basic_dependency_missing", "macro_archive_dependency_missing",
    } or (
        reason.startswith(("moat_", "macro_")) and metric_values.get("missing_dependency") is True
    ):
        kind, stage, label = (
            "missing_dependency", "derivation",
            "历史派生依赖缺失 · 补齐依赖后可定向重试",
        )
    elif reason == "normalization_or_derivation_missing":
        kind, stage, label = (
            "normalization_or_derivation_missing",
            "normalization",
            "历史字段派生尚未完成",
        )
    elif reason == "bounded_archive_gap":
        kind, stage, label = (
            "bounded_archive_gap",
            "acquisition",
            "历史候选记录不足 · 非来源故障",
        )
    elif code == "historical_partial" and raw_data_present:
        kind, stage, label = (
            "partial_available",
            "acquisition",
            "UZI 核心可用 · 可选字段缺失",
        )
    elif code == "strict_contract_gap" and raw_data_present:
        kind, stage, label = "audit_only", "audit", "UZI 可用 · 扩展审计提示"
    elif source_timed_out:
        kind, stage, label = "source_timeout", "acquisition", "数据源超时"
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
    dimension = str(
        _value(item, "dimension", _value(item, "dimension_id", "")) or ""
    ).strip()
    provider_revision = historical_provider_revision(dimension)
    checkpoint = _value(item, "checkpoint_json", {})
    checkpoint = checkpoint if isinstance(checkpoint, Mapping) else {}
    provider_upgraded = bool(
        provider_revision
        and str(checkpoint.get("historical_provider_revision") or "")
        != provider_revision
        and str(_value(item, "last_error_code", "") or "").lower()
        in _LEGACY_HISTORICAL_PROVIDER_GAP_CODES
    )
    return bool(
        _status(item) in _GAP_STATUSES
        and str(_value(item, "last_error_code", "") or "").lower()
        not in _NON_RETRYABLE_AUDIT_CODES
        and (_value(item, "retryable", False) or provider_upgraded)
        and (provider_upgraded or _attempt(item) < _max_attempts(item))
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
    limit: int | None = 12,
    projected_slots: bool = False,
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
        if projected_slots:
            bucket["retryable_count" if _value(task, "retryable", False) else "blocked_count"] += 1
        elif task_is_retryable(task):
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
        and not task_is_retryable(task)
        for task in task_rows
    )
    succeeded = (
        sum(_status(task) in _SUCCESS_STATUSES for task in task_rows) + audit_only
    )
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


def _candidate_gap_dimensions(
    checkpoint: Mapping[str, Any],
) -> tuple[bool, dict[str, list[str]]]:
    candidate = checkpoint.get("publication_candidate")
    if not isinstance(candidate, Mapping):
        return False, {}

    gaps: dict[str, list[str]] = {}
    candidate_has_gap_shape = (
        "bundles" in candidate or "missing_dimensions" in candidate
    )
    for proposal in list(candidate.get("bundles") or candidate.get("scopes") or []):
        if not isinstance(proposal, Mapping):
            continue
        if str(proposal.get("asset_role") or "").lower() == "fund":
            continue
        symbol = str(proposal.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        dimensions = [
            str(dimension)
            for dimension in list(proposal.get("missing_dimensions") or [])
            if str(dimension).strip()
        ]
        # Native missing_dimensions describes report-input disclosure. It is
        # not the full set of unsuccessful acquisitions in the same candidate.
        dimensions.extend(
            [
                str(item.get("dimension") or "")
                for item in list(proposal.get("items") or [])
                if isinstance(item, Mapping)
                and str(item.get("status") or "").lower() == "data_gap"
                and str(item.get("dimension") or "").strip()
            ]
        )
        if dimensions:
            gaps[symbol] = list(dict.fromkeys(dimensions))

    for raw_slot in list(candidate.get("missing_dimensions") or []):
        slot = str(raw_slot or "").strip()
        if ":" not in slot:
            continue
        symbol, dimension = slot.rsplit(":", 1)
        symbol = symbol.strip().upper()
        dimension = dimension.strip()
        if symbol and dimension:
            gaps.setdefault(symbol, []).append(dimension)
    return candidate_has_gap_shape, {
        symbol: list(dict.fromkeys(dimensions))
        for symbol, dimensions in gaps.items()
        if dimensions
    }


def _checkpoint_fund_gap_dimensions(
    checkpoint: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Return candidate acquisition gaps, including native disclosure gaps.

    A publication candidate is authoritative even when every gap is safe to
    disclose.  Older checkpoints used ``child_missing_by_symbol`` only for
    report-blocking gaps and therefore made report-ready portfolios look
    strictly complete.  The fallbacks keep active and pre-candidate jobs
    observable without changing persisted history.
    """

    candidate_known, candidate_gaps = _candidate_gap_dimensions(checkpoint)
    if candidate_known:
        return candidate_gaps

    retry_scope = {
        str(symbol).strip().upper(): [
            str(dimension)
            for dimension in list(dimensions or [])
            if str(dimension).strip()
        ]
        for symbol, dimensions in dict(
            checkpoint.get("retry_scope_by_symbol") or {}
        ).items()
        if str(symbol).strip()
    }
    missing_by_symbol = {
        str(symbol).strip().upper(): [
            str(item) for item in list(dimensions or []) if str(item).strip()
        ]
        for symbol, dimensions in dict(
            checkpoint.get("child_missing_by_symbol") or {}
        ).items()
        if str(symbol).strip()
    }
    if missing_by_symbol:
        return missing_by_symbol
    if retry_scope:
        return retry_scope
    return {
        str(holding.get("symbol") or "").strip().upper(): [
            str(item)
            for item in list(holding.get("missing_dimensions") or [])
            if str(item).strip()
        ]
        for holding in list(checkpoint.get("holdings") or [])
        if isinstance(holding, Mapping)
        and str(holding.get("symbol") or "").strip()
        and holding.get("missing_dimensions")
    }


def latest_fund_child_tasks(
    *, checkpoint: Mapping[str, Any], child_tasks: Iterable[Any]
) -> dict[tuple[str, str], Any]:
    """Project every holding/dimension to its newest persisted child task."""

    child_ids = [
        str(child_id)
        for child_id in list(checkpoint.get("child_job_ids") or [])
        if str(child_id).strip()
    ]
    for run in list(checkpoint.get("last_child_runs") or []) + list(
        checkpoint.get("child_runs") or []
    ):
        if not isinstance(run, Mapping):
            continue
        child_id = str(run.get("job_id") or "").strip()
        if child_id and child_id not in child_ids:
            child_ids.append(child_id)
    child_order = {child_id: index for index, child_id in enumerate(child_ids)}
    latest: dict[tuple[str, str], Any] = {}
    latest_order: dict[tuple[str, str], int] = {}
    for fallback_order, task in enumerate(child_tasks, start=len(child_order)):
        symbol = str(_value(task, "symbol", "") or "").strip().upper()
        dimension = str(
            _value(task, "dimension", _value(task, "dimension_id", "")) or ""
        ).strip()
        if not symbol or not dimension:
            continue
        slot = (symbol, dimension)
        job_id = str(_value(task, "job_id", "") or "")
        order = child_order.get(job_id, -1 if child_order else fallback_order)
        if slot not in latest or order >= latest_order[slot]:
            latest[slot] = task
            latest_order[slot] = order
    return latest


def _candidate_item_statuses(
    checkpoint: Mapping[str, Any],
) -> dict[str, dict[str, str]]:
    candidate = checkpoint.get("publication_candidate")
    if not isinstance(candidate, Mapping):
        return {}
    return {
        str(proposal.get("symbol") or "").strip().upper(): {
            str(item.get("dimension") or ""): str(item.get("status") or "").lower()
            for item in proposal.get("items") or []
            if isinstance(item, Mapping) and item.get("dimension")
        }
        for proposal in candidate.get("bundles") or candidate.get("scopes") or []
        if isinstance(proposal, Mapping)
        and "items" in proposal
        and proposal.get("asset_role") != "fund"
    }


def fund_gap_dimensions(
    checkpoint: Mapping[str, Any],
    *,
    child_tasks: Iterable[Any] = (),
) -> dict[str, list[str]]:
    """One acquisition-gap projection for summaries and directed retries.

    A report-usable degraded envelope must not erase a failed source request.
    A selected available/verified-empty snapshot remains usable after a failed
    refresh; neither that failure nor audit-only partials invalidate evidence.
    Latest real failures fill gaps only when no usable item is selected.
    """
    candidate_items = _candidate_item_statuses(checkpoint)
    gaps = {
        symbol: [
            dimension
            for dimension in dimensions
            if candidate_items.get(symbol, {}).get(dimension)
            not in {"available", "verified_empty"}
        ]
        for symbol, dimensions in _checkpoint_fund_gap_dimensions(checkpoint).items()
    }
    latest = latest_fund_child_tasks(checkpoint=checkpoint, child_tasks=child_tasks)
    for (symbol, dimension), task in latest.items():
        if (
            candidate_items.get(symbol, {}).get(dimension)
            not in {"available", "verified_empty"}
            and _status(task) == "failed"
            and task_is_retryable(task)
        ):
            gaps.setdefault(symbol, []).append(dimension)
    # A usable value produced against an obsolete profile is still stale.
    # This projection is supplied by the coordinator from selected DB rows,
    # not inferred from an error string or promoted to a native critical.
    for symbol, dimensions in dict(checkpoint.get("profile_dependency_gaps") or {}).items():
        gaps.setdefault(symbol, []).extend(dimensions)
    return {
        symbol: list(dict.fromkeys(dimensions))
        for symbol, dimensions in gaps.items()
        if dimensions
    }


def fund_retryable_scope(
    *,
    checkpoint: Mapping[str, Any],
    child_tasks: Iterable[Any],
    symbols: Iterable[str] | None = None,
    dimensions: Iterable[str] | None = None,
) -> dict[str, list[str]]:
    """Return real retries plus missed first attempts with PIT input support.

    Native-eligible data gaps used to be omitted from fund dispatch entirely.
    No child task means not attempted, not a proven source/derivation failure.
    Inputs-only dimensions can acquire their historical inputs once; after a
    real result exists its typed retryability wins, so this does not turn a
    missing implementation into an endless provider retry.
    """

    child_tasks = list(child_tasks)
    gap_dimensions = fund_gap_dimensions(checkpoint, child_tasks=child_tasks)
    selected_symbols = {
        str(symbol).strip().upper() for symbol in symbols or [] if str(symbol).strip()
    }
    selected_dimensions = {
        str(dimension).strip()
        for dimension in dimensions or []
        if str(dimension).strip()
    }
    latest = latest_fund_child_tasks(checkpoint=checkpoint, child_tasks=child_tasks)
    scope: dict[str, list[str]] = {}
    for symbol, gap_rows in gap_dimensions.items():
        if selected_symbols and symbol not in selected_symbols:
            continue
        for dimension in gap_rows:
            if selected_dimensions and dimension not in selected_dimensions:
                continue
            task = latest.get((symbol, dimension))
            profile_pending = dimension in (checkpoint.get("profile_dependency_gaps") or {}).get(symbol, [])
            if (profile_pending and (task is None or _status(task) not in _ACTIVE_STATUSES)
                and capability_for_dimension(dimension).can_reconstruct) or (task is not None and task_is_retryable(task)) or (
                task is None and capability_for_dimension(dimension).can_reconstruct
            ):
                scope.setdefault(symbol, []).append(dimension)
    return {
        symbol: list(dict.fromkeys(dimensions))
        for symbol, dimensions in sorted(scope.items())
        if dimensions
    }


def project_fund_parent_status(
    *,
    status: str,
    checkpoint: Mapping[str, Any],
    child_tasks: Iterable[Any] = (),
) -> str:
    """Project legacy successful parents with acquisition gaps as partial.

    New collection waves persist ``partial`` directly. Older rows may already
    be stored as ``succeeded`` because degraded slots were once counted as
    complete; projecting them on read keeps API/UI semantics consistent without
    rewriting historical records or frozen bundles.
    """

    normalized = str(status or "unknown").lower()
    if normalized == "succeeded" and fund_gap_dimensions(
        checkpoint, child_tasks=child_tasks
    ):
        return "partial"
    return normalized


def summarize_fund_portfolio(
    *,
    checkpoint: Mapping[str, Any],
    child_tasks: Iterable[Any],
    dimensions_per_holding: int,
    top_n: int = 10,
    child_results: Iterable[Any] = (),
    dimension_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Count the entire portfolio, never infer readiness from absent work.

    A finished candidate records the selected snapshots' acquisition states,
    independently of native report permissibility. Before it exists, only
    persisted positive per-date results prove availability:
    the task's ``succeeded`` status also includes reused degraded snapshots.
    Unknown and active slots are not confirmed UZI gaps or blocked failures.
    """
    holdings = list(checkpoint.get("holdings") or [])
    total_holdings = max(
        top_n, int(checkpoint.get("child_total_count") or 0), len(holdings)
    )
    dimension_count = max(0, int(dimensions_per_holding))
    all_child_tasks = list(child_tasks)
    missing_by_symbol = fund_gap_dimensions(checkpoint, child_tasks=all_child_tasks)
    retry_scope = fund_retryable_scope(checkpoint=checkpoint, child_tasks=all_child_tasks)
    child_runs = list(checkpoint.get("child_runs") or [])
    active_runs = [run for run in child_runs if _status(run) in _ACTIVE_STATUSES]
    last_runs = active_runs or list(checkpoint.get("last_child_runs") or [])
    current_child_ids = {
        str(run.get("job_id") or "")
        for run in last_runs
        if isinstance(run, Mapping) and str(run.get("job_id") or "")
    }
    tasks_by_slot = latest_fund_child_tasks(
        checkpoint=checkpoint,
        child_tasks=all_child_tasks,
    )
    results_by_task: dict[str, Any] = {}
    for result in child_results:
        task_id = str(_value(result, "task_id", ""))
        previous = results_by_task.get(task_id)
        if previous is None or _attempt(result) >= _attempt(previous):
            results_by_task[task_id] = result
    candidate = checkpoint.get("publication_candidate") or {}
    candidate_symbols = (
        {
            str(row.get("symbol") or "").strip().upper()
            for row in list(candidate.get("bundles") or candidate.get("scopes") or [])
            if isinstance(row, Mapping)
            and row.get("asset_role") != "fund"
            and row.get("symbol")
        }
        if isinstance(candidate, Mapping)
        else set()
    )
    candidate_items = _candidate_item_statuses(checkpoint)
    holding_symbols = list(
        dict.fromkeys(
            str(holding.get("symbol") or "").strip().upper()
            for holding in holdings
            if isinstance(holding, Mapping) and str(holding.get("symbol") or "").strip()
        )
    )
    configured_dimensions = set(dimension_ids)
    by_holding = []
    gap_diagnostics: list[dict[str, Any]] = []
    for symbol in holding_symbols:
        known_dimensions = (
            (
                configured_dimensions
                or {
                    dimension
                    for task_symbol, dimension in tasks_by_slot
                    if task_symbol == symbol
                }
            )
            | set(missing_by_symbol.get(symbol, []))
            | set(candidate_items.get(symbol, {}))
        )
        counts = Counter()
        for dimension in known_dimensions:
            profile_pending = dimension in (checkpoint.get("profile_dependency_gaps") or {}).get(symbol, [])
            task = tasks_by_slot.get((symbol, dimension))
            result = results_by_task.get(
                str(_value(task, "id", _value(task, "task_id", "")))
            )
            if result is not None and _attempt(result) != _attempt(task):
                result = None  # A previous attempt cannot settle the new one.
            metrics = dict(_value(result, "metrics_json", {}) or {})
            outcome = str(metrics.get("collection_outcome") or "")
            degraded = (
                bool(metrics.get("degraded"))
                or _value(result, "provider_status") == "existing_degraded_resolution"
                or outcome in {"unavailable", "retryable_failure"}
            )
            positive_result = bool(
                task is not None
                and _status(task) in _SUCCESS_STATUSES | {"skipped"}
                and result is not None
                and _value(result, "snapshot_id")
                and not degraded
                and (
                    _status(result) in _SUCCESS_STATUSES
                    or (
                        _status(result) == "skipped"
                        and outcome in {"available", "verified_empty"}
                    )
                )
            )
            if task is not None and _status(task) in _ACTIVE_STATUSES:
                counts["active_slots"] += 1
            elif symbol in candidate_symbols and dimension not in missing_by_symbol.get(
                symbol, []
            ):
                # New/full candidates must supply an actual usable item.
                # Preserve compatibility with legacy compact candidates that
                # recorded only missing_dimensions and omitted items entirely.
                if symbol not in candidate_items or candidate_items[symbol].get(
                    dimension
                ) in {"available", "verified_empty"}:
                    counts["ready_slots"] += 1
                else:
                    counts["unassessed_slots"] += 1
            elif task is None and symbol not in candidate_symbols and not profile_pending:
                counts["unassessed_slots"] += 1
            elif symbol not in candidate_symbols and positive_result and not profile_pending:
                # A successful new result supersedes the pre-retry gap list;
                # an actual native candidate still has the final say above.
                counts["ready_slots"] += 1
            elif (
                dimension in missing_by_symbol.get(symbol, [])
                or degraded
                or (task is not None and _status(task) in _GAP_STATUSES)
                or (
                    result is not None
                    and _status(result) in _GAP_STATUSES | {"degraded"}
                )
            ):
                counts["missing_slots"] += 1
                counts["degraded_slots"] += int(
                    degraded
                    or symbol in candidate_symbols
                    or bool(_value(result, "snapshot_id"))
                )
                retryable = dimension in retry_scope.get(symbol, [])
                # Count the same projected gap exactly once, even when there
                # was no child task or a successful task reused a gap snapshot.
                gap_diagnostics.append({
                    "dimension": dimension,
                    "retryable": retryable,
                    "last_error_code": _value(task, "last_error_code") or _value(result, "error_code") or "data_gap",
                    "last_error_message": _value(task, "last_error_message") or _value(result, "error_message")
                        or ("冻结候选仍有数据缺口，可定向补采" if retryable else "冻结候选仍有数据缺口，当前不可直接重试，按降级披露"),
                })
                counts[
                    "retryable_slots"
                    if retryable
                    else "blocked_slots"
                ] += 1
            else:
                counts["unassessed_slots"] += 1
        remaining = max(0, dimension_count - len(known_dimensions))
        counts[
            "ready_slots"
            if symbol in candidate_symbols and symbol not in candidate_items
            else "unassessed_slots"
        ] += remaining
        by_holding.append(
            {
                "symbol": symbol,
                "total_slots": dimension_count,
                **{
                    key: counts[key]
                    for key in (
                        "ready_slots",
                        "missing_slots",
                        "retryable_slots",
                        "blocked_slots",
                        "active_slots",
                        "unassessed_slots",
                        "degraded_slots",
                    )
                },
            }
        )
    total_slots = max(0, total_holdings * dimension_count)
    totals = Counter()
    for row in by_holding:
        for key, value in row.items():
            if key not in {"symbol", "total_slots"}:
                totals[key] += value
    totals["unassessed_slots"] += (
        max(0, total_holdings - len(holding_symbols)) * dimension_count
    )
    attempt = max(
        [int(run.get("attempt") or 0) for run in last_runs if isinstance(run, Mapping)]
        or [0]
    )
    return {
        "total_holdings": total_holdings,
        "ready_holdings": sum(
            row["ready_slots"] == dimension_count and dimension_count > 0
            for row in by_holding
        ),
        "dimensions_per_holding": dimension_count,
        "total_slots": total_slots,
        **{
            key: totals[key]
            for key in (
                "ready_slots",
                "missing_slots",
                "retryable_slots",
                "blocked_slots",
                "active_slots",
                "unassessed_slots",
                "degraded_slots",
            )
        },
        "by_holding": by_holding,
        "attempt": attempt,
        "current_child_job_ids": sorted(current_child_ids),
        "by_error_code": _bucket_rows(gap_diagnostics, key_name="last_error_code", fallback="data_gap",
                                      projected_slots=True, limit=None),
        "by_dimension": _bucket_rows(gap_diagnostics, key_name="dimension", fallback="unknown_dimension",
                                     projected_slots=True, limit=None),
    }


__all__ = [
    "classify_collection_failure",
    "fund_gap_dimensions",
    "fund_retryable_scope",
    "latest_fund_child_tasks",
    "project_fund_parent_status",
    "summarize_fund_portfolio",
    "summarize_tasks",
    "task_is_blocked",
    "task_is_retryable",
]
