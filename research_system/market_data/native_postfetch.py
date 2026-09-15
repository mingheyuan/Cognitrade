"""First-collection native post-fetch, before archive/research/freeze.

Only the claimed current-day task slots can acquire new live observations.
History continues through the existing historical archive path, not these live
search/browser calls. This stage never re-fetches holdings or starts a model.
"""
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic

from sqlalchemy import select

from domain_packs.finance_ashare.tools.uzi_native_postfetch import (
    VERSION,
    recover_snapshot_postfetch,
)
from domain_packs.finance_ashare.vendor.uzi_3_9_2.search_budget import (
    normalize_search_budget,
)
from research_system.market_data.agent_recovery import (
    _load_draft_inputs,
    _persist_recovered_snapshots,
    _snapshot_identity,
)
from research_system.market_data.contracts import canonical_hash
from research_system.market_data.models import (
    MarketDataCollectionJob,
    MarketDataCollectionTask,
    MarketDataCollectionTaskResult,
    MarketDataSnapshot,
)
from research_system.market_data.research_archive import ResearchDocumentStore


def _collection_search_budgets(database, *, job_id, task_ids, scopes):
    """Use only this wave's current task results, never coverage's best rows.

    Skipped/reused snapshots and previous attempts did not consume this wave's
    search allowance. All dimensions of a root acquisition share one receipt;
    a post-fetch revision carries its later counter on the same current result.
    """
    budgets = {}
    with database.session() as session:
        tasks = list(session.scalars(select(MarketDataCollectionTask).where(
            MarketDataCollectionTask.job_id == job_id, MarketDataCollectionTask.id.in_(task_ids))))
        for scope in scopes:
            receipts, unknown = [], False
            for task in tasks:
                if task.symbol != scope["symbol"]:
                    continue
                result = session.scalar(select(MarketDataCollectionTaskResult).where(
                    MarketDataCollectionTaskResult.task_id == task.id,
                    MarketDataCollectionTaskResult.as_of_date == scope["as_of_date"],
                    MarketDataCollectionTaskResult.attempt == task.attempt_count))
                if result is None or result.status == "skipped":
                    continue
                row = session.get(MarketDataSnapshot, result.snapshot_id) if result.snapshot_id else None
                if row is None or row.collection_task_id != task.id or (
                    row.symbol, row.dimension, row.as_of_date
                ) != (task.symbol, task.dimension, scope["as_of_date"]):
                    unknown = True
                    continue
                postfetch = (row.query_manifest_json or {}).get("native_postfetch") or {}
                revision = (row.query_manifest_json or {}).get("agent_recovery") or {}
                execution = (row.payload_json or {}).get("_uzi_execution") or {}
                receipt = postfetch.get("search_budget") if revision.get("job_id") == job_id else None
                if receipt is None:
                    receipt = execution.get("search_budget")
                if not isinstance(receipt, Mapping):
                    unknown = True
                else:
                    receipts.append(dict(receipt))
            if unknown:
                budgets[scope["symbol"]] = {"reason_code": "collection_search_budget_unrecorded"}
            elif receipts:
                if len({str(row.get("scope_id")) for row in receipts}) != 1:
                    budgets[scope["symbol"]] = {"reason_code": "collection_search_budget_ambiguous"}
                else:
                    # Some slots may still point to the structured snapshot.
                    # Never reset the wave to a smaller earlier consumption.
                    try:
                        receipts = [normalize_search_budget(row) for row in receipts]
                        if len({row["scope"] for row in receipts}) != 1:
                            raise ValueError("inconsistent scope kind")
                        budgets[scope["symbol"]] = {**receipts[0],
                            "cap": min(int(row["cap"]) for row in receipts),
                            **{key: max(row[key] for row in receipts) for key in ("used", "skipped")}}
                    except (KeyError, TypeError, ValueError):
                        budgets[scope["symbol"]] = {"reason_code": "collection_search_budget_invalid"}
    return budgets


def run_collection_postfetch(database, *, job_id, registry, settings, task_ids,
                            deadline_at, cancellation, write_fence, sources=None,
                            progress_callback=None):
    with database.session() as session:
        if not write_fence(session) or cancellation.is_set():
            return {"status": "not_run", "reason_code": "postfetch_worker_stopped"}
        job = session.get(MarketDataCollectionJob, job_id)
        if job is None or job.cancel_requested or not (job.request_json or {}).get("include_research_archive") or (
            ((job.checkpoint_json or {}).get("publication") or {}).get("status") == "published"
        ):
            return {"status": "not_run", "reason_code": "postfetch_job_not_mutable"}
    if task_ids is None or isinstance(task_ids, (str, bytes)):
        raise ValueError("postfetch requires the explicit claimed task IDs")
    prepared = _load_draft_inputs(database, job_id=job_id, registry=registry,
                                 requested_task_ids=task_ids)
    if not prepared["scopes"]:
        return {"status": "not_run", "reason_code": "no_current_day_postfetch_scope",
                "skipped_historical_scopes": prepared["skipped_historical_scopes"],
                "attempted": 0, "persisted_snapshot_count": 0}
    if monotonic() >= deadline_at:
        return {"status": "not_run", "reason_code": "research_deadline_exceeded"}
    snapshots = prepared["snapshots"]
    search_budgets = _collection_search_budgets(database, job_id=job_id,
        task_ids=prepared["task_ids"].values(), scopes=prepared["scopes"])
    originals = {_snapshot_identity(row): canonical_hash(row) for row in snapshots}
    parent_cancellation = cancellation

    class WorkerCancellation:
        def is_set(self):
            if parent_cancellation.is_set() or monotonic() >= deadline_at:
                return True
            # A provider wave is small; revalidate durable ownership at each
            # source boundary, not only when the final results are persisted.
            with database.session() as session:
                if not write_fence(session):
                    return True
                job = session.get(MarketDataCollectionJob, job_id)
                return job is None or job.cancel_requested or (
                    ((job.checkpoint_json or {}).get("publication") or {}).get("status") == "published")

    result = recover_snapshot_postfetch(snapshots, symbols=prepared["symbols"], profile="deep",
        settings=settings, sources=sources, cancellation=WorkerCancellation(), deadline_at=deadline_at,
        active_browser=True, collection_search_budgets=search_budgets)
    # Never put entire browser HTML into progress/checkpoint JSON. Full source
    # observations are content-addressed evidence below, with compact receipts.
    compact = {key: deepcopy(value) for key, value in result.items() if key != "observations"}
    if progress_callback:
        progress_callback(compact)
    persisted = _persist_recovered_snapshots(database, job_id=job_id, registry=registry,
        snapshots=snapshots, before_hashes=originals, task_ids=prepared["task_ids"],
        allowed_scopes=prepared["scopes"], write_fence=write_fence, cancellation=cancellation,
        deadline_at=deadline_at,
        on_persist=refresh_first_collection_result,
        observation_store=ResearchDocumentStore(Path(settings.workspace_root) / "cache" / "market_data_documents"))
    return {**compact, "persisted_snapshot_count": len(persisted), "persisted_snapshots": persisted,
            "skipped_historical_scopes": prepared["skipped_historical_scopes"]}


def refresh_first_collection_result(session, task_id, snapshot):
    """Complete the same in-flight attempt, keeping its initial source receipt.

    Do not add a retry attempt merely because collection has a post-fetch
    stage. Terminal old jobs/results and immutable snapshots are not rewritten.
    """
    task = session.get(MarketDataCollectionTask, task_id)
    job = session.get(MarketDataCollectionJob, task.job_id) if task else None
    if job is None or job.status != "running":
        return
    result = session.scalar(select(MarketDataCollectionTaskResult).where(
        MarketDataCollectionTaskResult.task_id == task.id,
        MarketDataCollectionTaskResult.as_of_date == snapshot.as_of_date,
        MarketDataCollectionTaskResult.attempt == task.attempt_count,
    ))
    if result is None:
        return
    previous = {key: getattr(result, key) for key in (
        "snapshot_id", "status", "provider_status", "error_code", "error_message", "retryable")}
    previous["metrics"] = deepcopy(result.metrics_json or {})
    manifest = snapshot.query_manifest_json or {}
    outcome = manifest.get("collection_outcome") or ("available" if snapshot.status in {"available", "verified_empty"} else "unavailable")
    result.status = {"available": "succeeded", "verified_empty": "succeeded", "partial": "partial",
                     "retryable_failure": "failed", "unavailable": "degraded"}.get(outcome, "data_gap")
    result.snapshot_id, result.provider_status = snapshot.id, snapshot.status
    result.retryable = result.status != "succeeded"
    result.error_code = "native_postfetch_data_gap" if result.retryable else None
    result.error_message = "原生补搜已执行，仍有数据缺口；原来源失败与新响应均已保留。" if result.retryable else None
    result.completed_at = datetime.now(timezone.utc)
    result.metrics_json = {"native_postfetch": {"version": VERSION, "initial_result": previous},
        "content_hash": snapshot.content_hash, "collection_outcome": outcome,
        "raw_data_present": bool((snapshot.payload_json or {}).get("raw")),
        "source_trace": deepcopy(manifest.get("source_trace") or []),
        "field_coverage": deepcopy(manifest.get("field_coverage") or {}),
        "validation_passed": bool((snapshot.validation_json or {}).get("passed"))}
    # A date-range task may contain older untouched results. Resolve its latest
    # result per date, rather than declaring the whole range complete today.
    latest = {}
    for row in session.scalars(select(MarketDataCollectionTaskResult).where(
        MarketDataCollectionTaskResult.task_id == task.id).order_by(MarketDataCollectionTaskResult.attempt.desc())):
        latest.setdefault(row.as_of_date, row)
    statuses = {row.status for row in latest.values()}
    task.status = ("succeeded" if statuses <= {"succeeded", "skipped"} else
                   "partial" if statuses & {"succeeded", "skipped", "partial", "degraded"} else "failed")
    task.retryable = any(row.retryable for row in latest.values())
    failure = next((row for row in latest.values() if row.status not in {"succeeded", "skipped"}), None)
    task.last_error_code = failure.error_code if failure else None
    task.last_error_message = failure.error_message if failure else None
    task.next_retry_at = (result.completed_at + timedelta(seconds=min(60, 2**task.attempt_count))
                          if task.retryable and task.attempt_count < task.max_attempts else None)
    task.updated_at = task.completed_at = result.completed_at
